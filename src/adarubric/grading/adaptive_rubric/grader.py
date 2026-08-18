"""The adaptive-rubric grader: judges the four generated tests, one focused LLM call each.

Blindness is structural: the evidence pack is built here, from scratch, and simply never includes
other graders' verdicts (the static judge, by ported design, sees them — that anchoring is one of
the flaws adaptive exists to fix). The evidence rule is enforced mechanically: a "pass" whose
evidence field is empty is downgraded to fail — the judge doesn't get to wave things through.

``GraderResult.details`` carries the per-test verdicts as JSON, so the dashboard can render each
test as its own box (check, verdict, quoted evidence, reasoning) instead of one opaque number.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from adarubric.core.contracts import Grader, Sandbox
from adarubric.core.models import EvalSpec, GraderResult, GraderSpec, TranscriptEntry
from adarubric.grading.adaptive_rubric.prompts import JUDGE_BINARY_PROMPT, JUDGE_LEVELS_PROMPT
from adarubric.grading.static_rubric.providers import (
    DEFAULT_MODELS,
    JudgeError,
    call_judge,
    pick_provider,
)

_TYPE = "adaptive_rubric"


class AdaptiveRubricGrader(Grader):
    """Four generated tests, four blind judge calls, score = weighted fraction passed."""

    name = _TYPE

    def grade(
        self,
        workspace: str,
        sandbox: Sandbox,
        grader_spec: GraderSpec,
        spec: EvalSpec,
        transcript: list[TranscriptEntry],
        env: dict[str, str] | None = None,
    ) -> GraderResult:
        criteria = _criteria(grader_spec)
        if not criteria:
            return GraderResult(
                _TYPE, 0.0, grader_spec.weight, "no generated tests on the spec",
                error="adaptive rubric had no tests to judge (generation was skipped or failed)")
        provider = pick_provider(grader_spec.provider, env)
        if provider is None:
            return GraderResult(
                _TYPE, 0.0, grader_spec.weight, "no judge API key",
                error="adaptive rubric needs a judge API key - none found")
        model = grader_spec.model or (env or {}).get("JUDGE_MODEL") or DEFAULT_MODELS.get(provider, "")
        evidence = _evidence_pack(spec, transcript,
                                  file_heads=_created_file_heads(workspace, sandbox, transcript))

        tests: list[dict] = []
        earned = 0.0
        total = 0.0
        for c in criteria:
            weight = float(c.get("weight", 1))
            try:
                verdict = _judge_one(c, evidence, provider, model, env)
            except JudgeError:
                # One retry: judge APIs time out transiently, and losing all four verdicts to a
                # single hiccup wastes the run's whole adaptive signal.
                try:
                    verdict = _judge_one(c, evidence, provider, model, env)
                except JudgeError as e:
                    return GraderResult(
                        _TYPE, 0.0, grader_spec.weight,
                        json.dumps({"judge": f"{provider}/{model}", "tests": tests}),
                        error=f"adaptive judge failed on '{c.get('id')}' (after a retry): {e}")
            tests.append(verdict)
            earned += verdict["score"] * weight
            total += weight
        score = round(earned / total, 4) if total else 0.0
        return GraderResult(
            _TYPE, score, grader_spec.weight,
            json.dumps({"judge": f"{provider}/{model}", "tests": tests}))


def _criteria(grader_spec: GraderSpec) -> list[dict]:
    """The generated tests ride in ``GraderSpec.rubric`` as JSON (the runner puts them there)."""
    try:
        parsed = json.loads(grader_spec.rubric or "")
    except ValueError:
        return []
    items = parsed.get("criteria") if isinstance(parsed, dict) else parsed
    return items if isinstance(items, list) else []


def _judge_one(
    c: dict, evidence: str, provider: str, model: str, env: dict[str, str] | None
) -> dict:
    """One test, one call. Returns {id, dimension, check, weight, score, verdict, evidence, reasoning}."""
    is_levels = c.get("id") == "process" or isinstance(c.get("levels"), dict)
    if is_levels:
        levels = c.get("levels") or {}
        prompt = JUDGE_LEVELS_PROMPT.format(
            check=c.get("check", "process quality"),
            level_1=levels.get("1.0", "direct path"),
            level_05=levels.get("0.5", "worked but wandered"),
            level_0=levels.get("0.0", "flailed"),
            evidence=evidence)
    else:
        prompt = JUDGE_BINARY_PROMPT.format(
            check=c.get("check", ""), evidence_hint=c.get("evidence_hint", "the transcript"),
            evidence=evidence)
    reply = _parse_reply(call_judge(provider, model, prompt, env))

    quoted = str(reply.get("evidence") or "").strip()
    reasoning = str(reply.get("reasoning") or "").strip()
    if is_levels:
        level = str(reply.get("level", "0.0"))
        score = {"1.0": 1.0, "0.5": 0.5, "0.0": 0.0}.get(level, 0.0)
        verdict = level
    else:
        passed = str(reply.get("verdict", "fail")).lower() == "pass"
        if passed and not quoted:
            # The evidence rule, enforced: no quote, no pass.
            passed = False
            reasoning = (reasoning + " ").strip() + "[downgraded to fail: judge quoted no evidence]"
        score = 1.0 if passed else 0.0
        verdict = "pass" if passed else "fail"
    return {
        "id": c.get("id"), "dimension": c.get("dimension"), "check": c.get("check"),
        "weight": float(c.get("weight", 1)), "score": score, "verdict": verdict,
        "evidence": quoted[:600], "reasoning": reasoning[:600],
    }


def _parse_reply(text: str) -> dict:
    cleaned = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
    m = re.search(r"\{[\s\S]*\}", cleaned)
    if m:
        try:
            parsed = json.loads(m.group(0))
            if isinstance(parsed, dict):
                return parsed
        except ValueError:
            pass
    return {}  # unreadable reply → treated as fail by the callers above


#: Extensions worth showing the judge the head of — text the agent produced. Binaries excluded.
#: (.obj/.mtl are text — Wavefront geometry; their absence cost a completeness verdict once.)
_TEXT_EXT = {".csv", ".txt", ".md", ".json", ".yaml", ".yml", ".py", ".js", ".html", ".xml",
             ".tsv", ".obj", ".mtl", ".toml", ".ini", ".cfg", ".log", ".sh", ".sql", ".r"}


def _created_file_heads(
    workspace: str, sandbox: Sandbox, transcript: list[TranscriptEntry],
    max_files: int = 6, head_bytes: int = 60000,
) -> list[tuple[str, str]]:
    """(path, contents) of files the agent created — read from the LIVE workspace, so the judge
    can verify contents instead of failing 'no proof' on completeness.

    The WHOLE file is shown (a 1500-byte peek once made the judge fail a perfectly valid .obj —
    the face lines live past the start). Only genuinely huge files get a middle cut, and then the
    judge is told explicitly what was cut, so "truncated" can never read as "invalid".
    Best-effort: unreadable files are simply skipped, never an error."""
    changed = next((e.output for e in transcript if e.type == "changes" and e.output), None)
    if not changed:
        return []
    try:
        parsed = json.loads(changed)
        # Created AND modified: a fix-the-file task's whole answer lives in a MODIFIED file —
        # showing only created files starved the judge on exactly the most common task shape.
        paths = (parsed.get("created") or []) + (parsed.get("modified") or [])
    except ValueError:
        return []
    heads: list[tuple[str, str]] = []
    for path in paths:
        if len(heads) >= max_files:
            break
        if Path(path).suffix.lower() not in _TEXT_EXT:
            continue
        text = ""
        try:
            res = sandbox.run_command(
                workspace,
                # Whole file when it fits; head + explicit truncation marker + tail when huge.
                f"if [ $(wc -c < '{path}') -le {head_bytes} ]; then cat '{path}'; "
                f"else head -c {head_bytes // 2} '{path}'; "
                f"echo; echo '[... middle truncated: file is' $(wc -c < '{path}') 'bytes total ...]'; "
                f"tail -c {head_bytes // 4} '{path}'; fi 2>/dev/null")
            text = (res.stdout or "").strip()
        except Exception:  # noqa: BLE001 - evidence is best-effort, never fatal
            pass
        if not text:
            # Windows-local has no `head`; read straight from disk (workspace, or the exported
            # copy when re-judging a finished run whose container is gone). Container-absolute
            # paths are mapped onto the export layout (/root/... is exported as home/...).
            candidates = [path.lstrip("/"), re.sub(r"^/root/", "home/", path)]
            for base in (Path(workspace), Path(workspace) / "workspace"):
                for rel in candidates:
                    p = base / rel
                    if p.is_file():
                        try:
                            whole = p.read_text(encoding="utf-8", errors="replace")
                        except OSError:
                            whole = ""
                        if len(whole) <= head_bytes:
                            text = whole.strip()
                        else:
                            text = (whole[: head_bytes // 2]
                                    + f"\n[... middle truncated: file is {len(whole)} chars total ...]\n"
                                    + whole[-head_bytes // 4:]).strip()
                        break
                if text:
                    break
        if text:
            heads.append((path, text))
    return heads


def _evidence_pack(
    spec: EvalSpec, transcript: list[TranscriptEntry],
    file_heads: list[tuple[str, str]] | None = None,
) -> str:
    """Everything the judge may see. Built from scratch so other graders' verdicts CANNOT leak in
    — blindness by construction, not by filtering."""
    sections = [f"## Task instruction\n{spec.instruction}"]
    for p in spec.skill_paths:
        md = Path(p) / "SKILL.md"
        if md.is_file():
            try:
                sections.append(f"## Skill guide the agent had: {Path(p).name}\n"
                                + md.read_text(encoding="utf-8", errors="replace")[:6000])
            except OSError:
                pass
    commands = [e for e in transcript if e.type == "command"]
    if commands:
        cmds = "\n\n".join(
            f"$ {e.command}\n{(e.stdout or '')[:4000]}"
            + (f"\nSTDERR: {(e.stderr or '')[:1500]}" if e.stderr else "")
            + f"\n[exit code: {e.exit_code if e.exit_code is not None else 'unknown'}]"
            for e in commands)
        sections.append(f"## Commands the agent ran (with output)\n{cmds}")
    output = next((e.output for e in transcript if e.type == "run_output" and e.output), None)
    if output:
        sections.append(f"## Agent output\n{output[:8000]}")
    # Hard evidence that doesn't depend on the agent narrating: what actually changed on disk and
    # which tools ran. For ACP agents (tools run in-process, no command entries) this is often the
    # ONLY proof of what was produced.
    changed = next((e.output for e in transcript if e.type == "changes" and e.output), None)
    if changed:
        try:
            c = json.loads(changed)
            lines = []
            for kind in ("created", "modified", "deleted"):
                for f in c.get(kind) or []:
                    lines.append(f"{kind}: {f}")
            tools = c.get("tools_used") or {}
            if tools:
                lines.append("tools used: " + ", ".join(f"{k}×{v}" for k, v in sorted(tools.items())))
            if lines:
                sections.append("## Files changed and tools used (measured, not claimed)\n"
                                + "\n".join(lines[:100]))
        except ValueError:
            pass
    if file_heads:
        blocks = "\n\n".join(f"### {p}\n```\n{text}\n```" for p, text in file_heads)
        sections.append(f"## Contents of files the agent created (first bytes, measured)\n{blocks}")
    return "## Session evidence\n\n" + "\n\n".join(sections)
