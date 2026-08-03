"""Deterministic graders — objective, mechanical scoring run AFTER the agent has finished.

Two graders:

* ``DeterministicGrader`` (generic mode) — runs a user-supplied shell command
  (``graders: [{type: deterministic, run: ...}]``) in the final workspace and reads a 0..1 score
  from its stdout (``{"score": x}`` JSON, or a ``REWARD SCORE: x`` line, or falls back to the exit
  code: 0 → 1.0, non-zero → 0.0).

* ``SkillsBenchVerifier`` (skillbench mode) — stages the task's ``verifier/`` into the container at
  ``/verifier`` (AFTER the agent is gone), runs ``bash /verifier/test.sh`` (which runs pytest,
  computes passed/total, writes ``/logs/verifier/reward.txt``), and reads that fraction back.
  Docker-only: the verifier hardcodes ``/verifier`` / ``/logs`` / ``/app``.

Isolation invariant: graders are invoked by the runner only after ``harness.run`` returns and the
workspace has been snapshotted/exported — the verifier is never present while the agent runs.
"""

from __future__ import annotations

import json
import re

from adarubric.core.contracts import Grader, Sandbox
from adarubric.core.models import EvalSpec, GraderResult, GraderSpec, TranscriptEntry

_JSON_SCORE_RE = re.compile(r'"score"\s*:\s*(-?[0-9]*\.?[0-9]+)')
_REWARD_RE = re.compile(r"REWARD SCORE:\s*(-?[0-9]*\.?[0-9]+)", re.IGNORECASE)


#: Exit codes accepted as a genuine pass/fail verdict when a check script printed no score:
#: 0 = checks passed, 1 = checks failed (also pytest's convention). EVERY other code means the
#: script never reached a conclusion — pytest 2 = interrupted/collection error, 4 = usage error,
#: 5 = no tests collected; shell 126 = not executable, 127 = not found. Turning those into 0.0
#: blames the agent for our broken plumbing, so they produce "no verdict" instead.
_VERDICT_EXIT_CODES = (0, 1)


def parse_score(stdout: str, exit_code: int = 0) -> tuple[float | None, str]:
    """Extract a 0..1 score + a short detail from grader stdout. Pure function (unit-tested).

    Returns ``(None, why)`` when no score could be determined at all. Callers must surface that as a
    grading *error*, never as a score of zero.
    """
    text = stdout or ""
    m = _JSON_SCORE_RE.search(text)
    if m:
        return _clamp(float(m.group(1))), "score from JSON"
    m = _REWARD_RE.search(text)
    if m:
        return _clamp(float(m.group(1))), "score from REWARD SCORE line"
    if exit_code in _VERDICT_EXIT_CODES:
        return (1.0 if exit_code == 0 else 0.0), f"score from exit code {exit_code}"
    return None, f"no score in output and the check script exited {exit_code} (never reached a verdict)"


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


class DeterministicGrader(Grader):
    """Generic deterministic grader — runs a shell command in the workspace and reads its score."""

    name = "deterministic"

    def grade(
        self,
        workspace: str,
        sandbox: Sandbox,
        grader_spec: GraderSpec,
        spec: EvalSpec,
        transcript: list[TranscriptEntry],
        env: dict[str, str] | None = None,
    ) -> GraderResult:
        command = grader_spec.command
        if not command:
            return GraderResult("deterministic", 0.0, grader_spec.weight,
                                "no `run` command in grader spec", error="grader is misconfigured")
        res = sandbox.run_command(workspace, command, env)
        score, how = parse_score(res.stdout, res.exit_code)
        detail = f"{how}; exit={res.exit_code}"
        # Keep BOTH streams: a check script that dies usually explains itself on stderr, and
        # discarding it is what made the last failure impossible to diagnose from the artifacts.
        tail = _tail(res.stdout, res.stderr)
        if tail:
            detail += f"\n{tail}"
        return GraderResult("deterministic", score if score is not None else 0.0,
                            grader_spec.weight, detail,
                            error=None if score is not None else how)


class SkillsBenchVerifier(Grader):
    """SkillsBench verifier — stages verifier/ into the container and runs its test.sh (docker-only)."""

    name = "skillbench_verifier"

    def grade(
        self,
        workspace: str,
        sandbox: Sandbox,
        grader_spec: GraderSpec,
        spec: EvalSpec,
        transcript: list[TranscriptEntry],
        env: dict[str, str] | None = None,
    ) -> GraderResult:
        if not spec.verifier_path:
            return GraderResult("skillbench_verifier", 0.0, grader_spec.weight, "no verifier_path",
                                error="no verifier/ in this task — nothing to grade against")
        if sandbox.name != "docker":
            msg = ("SkillsBench verifiers hardcode /verifier,/logs,/app — grading requires "
                   "--sandbox docker")
            return GraderResult("skillbench_verifier", 0.0, grader_spec.weight, msg, error=msg)

        # Stage the verifier at /verifier AFTER the agent finished (agent never saw it).
        sandbox.stage(workspace, spec.verifier_path, "/verifier")
        sandbox.run_command(workspace, "mkdir -p /logs/verifier")
        res = sandbox.run_command(
            workspace,
            "if [ -f /verifier/test.sh ]; then bash /verifier/test.sh; else echo 'no test.sh'; fi",
            env,
        )
        # Score, most-authoritative first. reward.txt comes FIRST on purpose: it is the task's own
        # verdict, and SkillsBench tasks are pass/fail by design (test.sh writes 1 only when every
        # test passed). The later sources give partial credit and are fallbacks for when the script
        # wrote no verdict — do not reorder them above reward.txt, or a task the benchmark considers
        # failed would silently earn a fractional score.
        # 1) reward.txt the script wrote, 2) CTRF json passed/tests, 3) pytest summary line,
        # 4) REWARD SCORE line, 5) exit code (only 0/1 - see _VERDICT_EXIT_CODES).
        reward_txt = sandbox.run_command(workspace, "cat /logs/verifier/reward.txt 2>/dev/null || true").stdout.strip()
        ctrf = sandbox.run_command(workspace, "cat /logs/verifier/ctrf.json 2>/dev/null || true").stdout.strip()

        score, how = _score_verifier(reward_txt, ctrf, res.stdout, res.exit_code)
        return GraderResult(
            "skillbench_verifier", score if score is not None else 0.0, grader_spec.weight,
            f"{how}\n{_tail(res.stdout, res.stderr)}",
            error=None if score is not None else f"verifier produced no result — {how}",
        )


_PYTEST_SUMMARY = {
    "passed": re.compile(r"(\d+) passed"),
    "failed": re.compile(r"(\d+) failed"),
    "error": re.compile(r"(\d+) error"),
}


def _tail(stdout: str | None, stderr: str | None, limit: int = 1200) -> str:
    """Last chunk of the check script's output, stderr included and labelled.

    A crashing script writes its reason to stderr ("command not found", "syntax error"). Previously
    only 400 chars of stdout were kept, so a broken verifier left no evidence of *why* it broke.
    """
    parts = []
    if (stdout or "").strip():
        parts.append(stdout.strip()[-limit:])
    if (stderr or "").strip():
        parts.append("stderr:\n" + stderr.strip()[-limit:])
    return "\n".join(parts)


def _score_verifier(
    reward_txt: str, ctrf: str, stdout: str, exit_code: int
) -> tuple[float | None, str]:
    """Score from the most authoritative evidence available; ``None`` when there is none."""
    # 1) reward.txt (a bare fraction the script may have written).
    if reward_txt:
        try:
            return _clamp(float(reward_txt.splitlines()[-1].strip())), f"reward.txt={reward_txt}"
        except ValueError:
            pass
    # 2) CTRF JSON summary (passed/tests) — pytest-json-ctrf output.
    if ctrf:
        try:
            summ = json.loads(ctrf)["results"]["summary"]
            total = summ.get("tests") or 0
            if total:
                return _clamp(summ.get("passed", 0) / total), f"ctrf {summ.get('passed',0)}/{total}"
        except (ValueError, KeyError, TypeError, ZeroDivisionError):
            pass
    # 3) pytest final summary line ("N passed, M failed, K error").
    p = _int(_PYTEST_SUMMARY["passed"], stdout)
    f = _int(_PYTEST_SUMMARY["failed"], stdout)
    e = _int(_PYTEST_SUMMARY["error"], stdout)
    denom = p + f + e
    if denom > 0:
        return _clamp(p / denom), f"pytest {p} passed / {denom} tests"
    # 4/5) REWARD SCORE line, then exit code.
    return parse_score(stdout, exit_code)


def _int(rx: re.Pattern[str], text: str) -> int:
    m = rx.search(text or "")
    return int(m.group(1)) if m else 0
