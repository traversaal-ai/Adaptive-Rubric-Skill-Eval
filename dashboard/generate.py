"""Generate a self-contained HTML run-tracker from an AdaRubric ``output/`` tree.

Walks ``output/<harness>/<task>/attempt-*/trial-*/`` reading each trial's ``run.json`` (+ ``raw.log``
tail), builds one JSON blob, and injects it into ``template.html`` to produce a single, dependency-free
``report.html`` you can open in any browser (charts, logs, accuracy, cost — no server, no network).

Usage:
    python dashboard/generate.py                       # scans ./output → dashboard/report.html
    python dashboard/generate.py --output out --out r.html
    python dashboard/generate.py --body-only ...        # emit body-only fragment (for embedding)

It is also exposed as ``adarubric report`` (see the CLI).
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TEMPLATE = _HERE / "template.html"
_SAMPLE_RE = re.compile(r"/\* __ADA_SAMPLE_START__ \*/.*?/\* __ADA_SAMPLE_END__ \*/", re.DOTALL)
_LOG_TAIL = 6000   # chars of raw.log kept per run
_MAX_FILES = 80    # cap per created/modified/deleted list (kept lean in the embedded JSON)


def collect(output_root: str) -> dict:
    """Scan an output tree into the dashboard's data shape. Pure + tested (no rendering).

    Accepts both the current ``attempt-N/trial-T/run.json`` layout and the older
    ``attempt-N/run.json`` layout (run.json directly under the attempt).
    """
    root = Path(output_root)
    runs: list[dict] = []
    for run_json in sorted(root.rglob("run.json")):
        # Must sit under an attempt-* dir (directly, or under a trial-* inside it).
        if not _under_attempt(run_json):
            continue
        try:
            meta = json.loads(run_json.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        runs.append(_run_record(run_json, meta))
    return {
        "output_root": str(output_root),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "runs": runs,
    }


def _under_attempt(run_json: Path) -> bool:
    p = run_json.parent
    return p.name.startswith("attempt-") or p.parent.name.startswith("attempt-")


def _run_record(run_json: Path, meta: dict) -> dict:
    trial_dir = run_json.parent
    # Layouts: <attempt>/trial-T/run.json  OR  <attempt>/run.json.
    if trial_dir.name.startswith("trial-"):
        trial = _int_after(trial_dir.name, "trial-")
        attempt = _int_after(trial_dir.parent.name, "attempt-")
    else:
        trial = 1
        attempt = _int_after(trial_dir.name, "attempt-")
    usage = meta.get("usage") or {}
    timing = meta.get("timing") or {}
    skill = meta.get("skill_usage") or {}
    total_ms = timing.get("total_ms")

    cost = usage.get("cost_usd")
    est = usage.get("estimated_cost_usd")
    cost_usd = cost if cost is not None else est

    changes = _changes(trial_dir / "changes.json", meta)

    return {
        "harness": meta.get("harness", "?"),
        "task": meta.get("task", trial_dir.parents[1].name),
        "model": meta.get("model"),
        "sandbox": meta.get("sandbox", "?"),
        "attempt": attempt,
        "trial": trial,
        "success": bool(meta.get("success")),
        "timed_out": bool(meta.get("timed_out")),
        "graded": bool(meta.get("graded")),
        "reward": meta.get("reward") if meta.get("graded") else None,
        "skill_opened": skill.get("skill_opened"),
        "turns": usage.get("num_turns"),
        "tool_calls": usage.get("num_tool_calls"),
        "commands": usage.get("num_commands"),
        "tools": usage.get("tool_counts") or {},
        "tokens": {
            "input": usage.get("input_tokens"),
            "output": usage.get("output_tokens"),
            "total": usage.get("total_tokens"),
        },
        "cost_usd": cost_usd,
        "cost_source": usage.get("cost_source"),
        "time_s": round(total_ms / 1000, 1) if total_ms is not None else None,
        "changes": changes,
        "error": meta.get("error"),
        "output_dir": trial_dir.as_posix(),
        "log_excerpt": _log_tail(trial_dir / "raw.log"),
    }


def _changes(changes_json: Path, meta: dict) -> dict:
    """Read the created/modified/deleted file LISTS (falling back to counts from run.json)."""
    created: list[str] = []
    modified: list[str] = []
    deleted: list[str] = []
    if changes_json.is_file():
        try:
            c = json.loads(changes_json.read_text(encoding="utf-8"))
            created, modified, deleted = c.get("created") or [], c.get("modified") or [], c.get("deleted") or []
        except (ValueError, OSError):
            pass
    return {
        "created": created[:_MAX_FILES], "modified": modified[:_MAX_FILES], "deleted": deleted[:_MAX_FILES],
        "n_created": meta.get("files_created", len(created)),
        "n_modified": meta.get("files_modified", len(modified)),
        "n_deleted": meta.get("files_deleted", len(deleted)),
    }


def _int_after(name: str, prefix: str) -> int | None:
    if name.startswith(prefix):
        try:
            return int(name[len(prefix):])
        except ValueError:
            return None
    return None


def _log_tail(path: Path) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-_LOG_TAIL:] if len(text) > _LOG_TAIL else text


def render(data: dict, *, body_only: bool = False) -> str:
    """Inject ``data`` into the template, returning a full HTML document (or a body-only fragment)."""
    template = _TEMPLATE.read_text(encoding="utf-8")
    payload = f"const DATA = {json.dumps(data, indent=None)};"
    body = _SAMPLE_RE.sub(lambda _m: payload, template, count=1)
    if body_only:
        return body
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<title>AdaRubric — Run Tracker</title>\n</head>\n<body>\n" + body + "\n</body>\n</html>\n"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate a self-contained AdaRubric run-tracker HTML.")
    ap.add_argument("--output", default="output", help="Output tree to scan (default: output).")
    ap.add_argument("--out", default=str(_HERE / "report.html"), help="HTML file to write.")
    ap.add_argument("--body-only", action="store_true", help="Emit a body-only fragment, not a full doc.")
    args = ap.parse_args()

    data = collect(args.output)
    Path(args.out).write_text(render(data, body_only=args.body_only), encoding="utf-8")
    print(f"Wrote {args.out} — {len(data['runs'])} run(s) from {args.output}/")


if __name__ == "__main__":
    main()
