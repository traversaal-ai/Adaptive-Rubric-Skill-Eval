"""The oracle "harness" — runs the task's own reference solution instead of an AI.

Every SkillsBench task ships ``oracle/solve.sh``: a worked solution a human wrote and verified. Run
it and the task's grader must return a perfect score. If it doesn't, the *task* is broken — not the
model — and any agent scores you collect are meaningless.

This is the cheapest possible check: no API key, no model, no tokens, no money. It caught nothing for
us only because we didn't have it. Without it we spent real money on gemini and codex runs that all
scored 0.0, then spent an hour discovering the cause was Windows line endings mangling the grader.
One oracle run would have said "the task itself can't pass" immediately.

The oracle is a reference ANSWER, so it is staged into the container only for this harness — a real
agent must never see it (see ``EvalRunner._run_trial``).
"""

from __future__ import annotations

from adarubric.core.contracts import Harness, RunCommand
from adarubric.core.models import RunOutput

#: Where the runner stages the task's ``oracle/`` directory inside the sandbox.
ORACLE_DIR = "/oracle"


class OracleHarness(Harness):
    """Executes ``oracle/solve.sh`` in the prepared task environment."""

    name = "oracle"
    cli = "bash"
    #: No API key: this is a shell script, not a model. Running it is free.
    env_keys: tuple[str, ...] = ()
    #: Skills are guidance for a model. The reference solution needs none, so nothing is injected.
    skill_dirs: tuple[str, ...] = ()
    docker_install = ""
    #: Tells the runner to stage the task's oracle/ before running (never done for real agents).
    runs_oracle = True

    def run(self, instruction: str, workspace: str, run_command: RunCommand) -> RunOutput:
        script = f"{ORACLE_DIR}/solve.sh"
        res = run_command(
            f"if [ -f '{script}' ]; then bash '{script}'; else echo 'no solve.sh in oracle/'; exit 66; fi"
        )
        error = None
        if res.exit_code != 0:
            # The reference solution itself failed to run — that is a broken task environment, and
            # worth reporting loudly, because it invalidates every agent score for this task.
            detail = (res.stderr or res.stdout or "").strip()[-800:]
            error = f"oracle solve.sh failed (exit {res.exit_code}): {detail}"
        return RunOutput(
            output=(res.stdout or "").strip(),
            raw_output=(res.stdout or "") + (("\n" + res.stderr) if res.stderr else ""),
            model="oracle (reference solution, no model)",
            error=error,
        )
