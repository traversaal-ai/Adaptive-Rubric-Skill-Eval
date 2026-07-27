"""Gemini CLI harness adapter.

Runs `gemini -y -o json` (yolo auto-approve, JSON output with stats when supported), prompt via
stdin redirection from the canonical prompt file. Falls back gracefully to plain-text parsing on
older gemini-cli versions.

Skill-usage caveat: gemini-cli's output does not expose a per-tool trajectory we can parse yet, so
`skill_opened` stays **None** (unknown) — recorded honestly, never a fabricated False. (Docker runs
inject skills into `/root/.gemini/skills`, gemini's real discovery dir, so the skill IS available.)
"""

from __future__ import annotations

import json

from adarubric.core.contracts import PROMPT_RELPATH, Harness, RunCommand
from adarubric.core.models import RunOutput


class GeminiHarness(Harness):
    name = "gemini-cli"
    cli = "gemini"
    env_keys = ("GEMINI_API_KEY",)
    skill_dirs = (".gemini/skills",)  # gemini discovers skills under .gemini/skills
    # Needs node 20+; nodesource then npm.
    docker_install = (
        "curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && "
        "apt-get install -y nodejs && npm install -g @google/gemini-cli && gemini --version"
    )

    def run(self, instruction: str, workspace: str, run_command: RunCommand) -> RunOutput:
        model_flag = f" -m {self.model}" if self.model else ""
        result = run_command(f'gemini -y -o json{model_flag} < "{PROMPT_RELPATH}"')
        out = parse_gemini_output(result.stdout, result.stderr)
        if out is not None:
            return out
        # Older CLI without -o json: plain-text run. A nonzero exit is a real failure.
        result = run_command(f'gemini -y{model_flag} < "{PROMPT_RELPATH}"')
        combined = (result.stdout + "\n" + result.stderr).strip()
        return RunOutput(
            output=combined,
            raw_output=result.stdout,
            error=(None if result.exit_code == 0
                   else f"gemini failed (exit {result.exit_code}): {combined[:300]}"),
        )


def parse_gemini_output(stdout: str, stderr: str = "") -> RunOutput | None:
    """Parse `gemini -o json`: {"response": str, "stats": {"models": {name: {tokens: {...}}}}}.
    Returns None when the output isn't JSON (older CLI) — caller falls back to plain text."""
    try:
        data = json.loads(stdout.strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    model = None
    in_tok = out_tok = 0
    models = ((data.get("stats") or {}).get("models")) or {}
    for mname, mstats in models.items():
        model = model or mname
        tokens = (mstats or {}).get("tokens") or {}
        in_tok += tokens.get("prompt") or tokens.get("input") or 0
        out_tok += tokens.get("candidates") or tokens.get("output") or 0

    return RunOutput(
        output=(data.get("response") or "").strip(),
        raw_output=stdout,
        model=model,
        input_tokens=in_tok or None,
        output_tokens=out_tok or None,
        # No trajectory visibility → unknown, honestly.
        skill_opened=None,
    )
