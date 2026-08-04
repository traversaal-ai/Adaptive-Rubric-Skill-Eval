"""AdaRubric run-tracker — a live dashboard served on a local port.

One command, one dashboard, live: it scans ``output/`` on every request and serves
``dashboard.html`` on ``http://localhost:<port>``. The page polls ``/api/data`` every ~2s and
re-renders **in place** (no reload) — you watch the docker build, files copied into the container,
the current stage, accuracy, turns, cost, and per-run logs as a run happens. Stdlib only; no dummy
data, no static files to regenerate.

Usage:
    python dashboard/serve.py                      # http://127.0.0.1:8765, scans ./output
    python dashboard/serve.py --output output --port 8765 --no-open
"""

from __future__ import annotations

import argparse
import json
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PAGE = _HERE / "dashboard.html"
_LOG_TAIL = 6000   # chars of raw.log kept per run
_MAX_FILES = 80    # cap per created/modified/deleted list


# --------------------------------------------------------------------------- scan output/ → data

def collect(output_root: str) -> dict:
    """Scan an output tree into the dashboard's data shape (fresh each call — this is the live feed).

    Accepts both the current ``attempt-N/trial-T/run.json`` layout and the older ``attempt-N/run.json``.
    """
    root = Path(output_root)
    status = _read_status(root)
    trials = status.get("trials") or {}
    runs: list[dict] = []
    for run_json in sorted(root.rglob("run.json")):
        if not _under_attempt(run_json):
            continue
        try:
            meta = json.loads(run_json.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        rec = _run_record(run_json, meta)
        rec["activity"] = (trials.get(rec["key"], {}) or {}).get("activity", [])
        runs.append(rec)
    # Add jobs that are RUNNING now — they have no run.json yet (it's written at finish), but should
    # still show in the list (with a "running" tag) and be openable. Keyed/identified by output path.
    seen = {r["key"] for r in runs}
    for t in trials.values():
        if t.get("done"):
            continue
        rec = _running_record(str(output_root), t)
        if rec["key"] not in seen:
            runs.append(rec)
    return {
        "output_root": str(output_root),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "runs": runs,
        "live": _live(status),
    }


def _read_status(root: Path) -> dict:
    p = root / "status.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _live(s: dict) -> dict:
    if not s:
        return {"running": False, "activity": [], "in_progress": [], "updated_at": None}
    in_progress = [
        {"harness": t.get("harness"), "task": t.get("task"), "attempt": t.get("attempt"),
         "trial": t.get("trial"), "stage": t.get("stage"), "updated_at": t.get("updated_at")}
        for t in (s.get("trials") or {}).values() if not t.get("done")
    ]
    return {
        "running": bool(s.get("running")),
        "updated_at": s.get("updated_at"),
        "activity": (s.get("activity") or [])[-60:],
        "in_progress": in_progress,
    }


def _derived_depth(skill: dict) -> str | None:
    """Best-effort depth for a run recorded before ``skill_depth`` existed.

    Uses only what that run already stored, so nothing is invented: a read of a file inside a skill
    folder other than SKILL.md means it went deeper; otherwise it was merely opened.
    """
    opened = skill.get("skill_opened")
    if opened is False:
        return "not_opened"
    if opened is not True:
        return None
    for entry in skill.get("skill_files_read") or []:
        text = str(entry).lower()
        if "skills" in text and "skill.md" not in text.rsplit("/", 1)[-1]:
            return "used"
    return "noticed"


def _under_attempt(run_json: Path) -> bool:
    p = run_json.parent
    return p.name.startswith("attempt-") or p.parent.name.startswith("attempt-")


def _run_record(run_json: Path, meta: dict) -> dict:
    trial_dir = run_json.parent
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
    harness = meta.get("harness", "?")
    task = meta.get("task", trial_dir.parents[1].name)
    return {
        "key": f"{harness}/{task}/attempt-{attempt}/trial-{trial}",
        "harness": harness,
        "task": task,
        "model": meta.get("model"),                      # what the CLI reported actually running
        "model_requested": meta.get("model_requested"),  # what --model asked for (may be None)
        "sandbox": meta.get("sandbox", "?"),
        "attempt": attempt,
        "trial": trial,
        "running": False,
        "success": bool(meta.get("success")),
        "timed_out": bool(meta.get("timed_out")),
        "graded": bool(meta.get("graded")),
        "reward": meta.get("reward") if meta.get("graded") else None,
        # Set when grading never reached a verdict (broken check script, wrong sandbox). Rendered as
        # its own state — NOT as a reward of zero, which would read as "the agent got it all wrong".
        "grading_error": meta.get("grading_error"),
        # Archival copy failed. The run is still valid and still scored — shown as a footnote, not
        # as a failure, so a file lock can't masquerade as the agent losing.
        "export_error": meta.get("export_error"),
        "skill_opened": skill.get("skill_opened"),
        # "used" (read past the front page) | "noticed" (front page only) | "not_opened" | None.
        # Derived here when the run predates the field, so the table never mixes two vocabularies —
        # showing "opened" on old rows next to "noticed" on new ones just looks like two scales.
        "skill_depth": skill.get("skill_depth") or _derived_depth(skill),
        "skill_files": skill.get("skill_files_read") or [],
        "turns": usage.get("num_turns"),                     # ours: model replies, one definition
        "turns_reported": usage.get("num_turns_reported"),   # what the agent itself claimed
        "tool_calls": usage.get("num_tool_calls"),
        "commands": usage.get("num_commands"),
        "tools": usage.get("tool_counts") or {},
        "tokens": {
            "input": usage.get("input_tokens"),
            "output": usage.get("output_tokens"),
            "total": usage.get("total_tokens"),
        },
        "cost_usd": cost if cost is not None else est,
        "cost_source": usage.get("cost_source"),
        "time_s": round(total_ms / 1000, 1) if total_ms is not None else None,
        # ISO-8601 UTC, straight from run.json. The page renders them in the viewer's local zone —
        # "when did this run" is unanswerable from a duration alone once you have a week of results.
        "started_at": meta.get("started_at"),
        "ended_at": meta.get("ended_at"),
        "changes": _changes(trial_dir / "changes.json", meta),
        "error": meta.get("error"),
        "output_dir": trial_dir.as_posix(),
        "log_excerpt": _log_tail(trial_dir / "raw.log"),
    }


def _running_record(output_root: str, t: dict) -> dict:
    """A row for a job that's running now (no run.json yet). Identified by its output path."""
    harness = t.get("harness", "?")
    task = t.get("task", "?")
    attempt, trial = t.get("attempt"), t.get("trial")
    key = f"{harness}/{task}/attempt-{attempt}/trial-{trial}"
    return {
        "key": key, "harness": harness, "task": task, "model": None, "model_requested": None,
        "sandbox": None,
        "attempt": attempt, "trial": trial, "running": True, "stage": t.get("stage"),
        "success": False, "timed_out": False, "graded": False, "reward": None,
        "grading_error": None, "export_error": None, "skill_opened": None,
        "skill_depth": None, "skill_files": [],
        "turns": None, "turns_reported": None, "tool_calls": None, "commands": None, "tools": {},
        "tokens": {"input": None, "output": None, "total": None}, "cost_usd": None, "cost_source": None,
        "time_s": None,
        # A live job has a real start time (from status.json) but no end time yet — the page turns
        # that into a ticking elapsed clock rather than a blank cell.
        "started_at": t.get("started_at"),
        "ended_at": None,
        "changes": {"created": [], "modified": [], "deleted": [], "n_created": 0, "n_modified": 0, "n_deleted": 0},
        "error": None, "output_dir": f"{output_root}/{key}", "log_excerpt": "",
        "activity": t.get("activity", []),
    }


def _changes(changes_json: Path, meta: dict) -> dict:
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


def page_html() -> str:
    """The full dashboard document (dashboard.html wrapped in a minimal HTML skeleton)."""
    body = _PAGE.read_text(encoding="utf-8")
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>AdaRubric — Run Tracker</title>\n</head>\n<body>\n" + body + "\n</body>\n</html>\n"
    )


# --------------------------------------------------------------------------- server

def _make_handler(output_root: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:  # quiet console
            pass

        def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                self._respond(200, "text/html; charset=utf-8", page_html().encode("utf-8"))
            elif path == "/api/data":
                self._respond(200, "application/json", json.dumps(collect(output_root)).encode("utf-8"))
            else:
                self._respond(404, "text/plain; charset=utf-8", b"not found")

        def _respond(self, code: int, ctype: str, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description="Serve the AdaRubric run tracker live on a local port.")
    ap.add_argument("--output", default="output", help="Output tree to scan (default: output).")
    ap.add_argument("--port", type=int, default=8765, help="Port (default: 8765).")
    ap.add_argument("--host", default="127.0.0.1", help="Host (default: 127.0.0.1, localhost only).")
    ap.add_argument("--no-open", action="store_true", help="Don't auto-open the browser.")
    args = ap.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), _make_handler(args.output))
    url = f"http://{args.host}:{args.port}"
    print(f"AdaRubric dashboard live at {url}  — scanning {args.output}/ · updates in place · Ctrl-C to stop")
    if not args.no_open:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
        httpd.server_close()


if __name__ == "__main__":
    main()
