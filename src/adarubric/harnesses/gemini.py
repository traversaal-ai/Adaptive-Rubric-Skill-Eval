"""Gemini CLI harness adapter.

Runs `gemini -y -o json` (yolo auto-approve, JSON output with stats when supported), prompt via
stdin redirection from the canonical prompt file. Falls back gracefully to plain-text parsing on
older gemini-cli versions.

Skill usage IS measurable: `gemini -o json` reports `stats.tools.byName` — a COMPLETE per-tool call
tally for the session. Gemini activates a skill via its `activate_skill` tool, so that tool shows up
in `byName` when a skill was used. Because the tally is complete, we get a definitive answer:
`activate_skill` present → `skill_opened=True`; a tools block with no skill tool → `skill_opened=False`
(genuinely not activated); no tools block at all (older CLI) → `None` (unknown). Docker/local inject
the skill into gemini's real discovery dirs (`.gemini/skills` / `.agents/skills`), so it IS available.

Headless trust: gemini-cli refuses `-y` (YOLO) in an "untrusted" folder and exits 55 unless the
workspace is trusted. In a sandbox the workspace IS the isolation, so we set
`GEMINI_CLI_TRUST_WORKSPACE=true` (the CLI's own documented headless escape hatch).
"""

from __future__ import annotations

import json

from adarubric.core.contracts import PROMPT_RELPATH, Harness, RunCommand
from adarubric.core.models import RunOutput, SkillTrigger, TriggerSource

# Headless/automated escape hatch for gemini-cli's trusted-folder gate (else `-y` → exit 55).
_TRUST_ENV = {"GEMINI_CLI_TRUST_WORKSPACE": "true"}


class GeminiHarness(Harness):
    name = "gemini-cli"
    cli = "gemini"
    env_keys = ("GEMINI_API_KEY",)
    # Gemini discovers skills in .gemini/skills (workspace) / ~/.gemini/skills (user), and accepts
    # .agents/skills as a documented alias. Inject into both so discovery is robust either way.
    # (docs: github.com/google-gemini/gemini-cli/docs/cli/using-agent-skills.md)
    skill_dirs = (".gemini/skills", ".agents/skills")
    # Needs node 20+; nodesource then npm.
    docker_install = (
        "curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && "
        "apt-get install -y nodejs && npm install -g @google/gemini-cli && gemini --version"
    )

    def run(self, instruction: str, workspace: str, run_command: RunCommand) -> RunOutput:
        model_flag = f" -m {self.model}" if self.model else ""
        # Trust the workspace so `-y` isn't downgraded to interactive approval (headless → exit 55).
        result = run_command(f'gemini -y -o json{model_flag} < "{PROMPT_RELPATH}"', _TRUST_ENV)
        out = parse_gemini_output(result.stdout, result.stderr)
        if out is not None:
            return out
        # Older CLI without -o json: plain-text run. A nonzero exit is a real failure.
        result = run_command(f'gemini -y{model_flag} < "{PROMPT_RELPATH}"', _TRUST_ENV)
        combined = (result.stdout + "\n" + result.stderr).strip()
        return RunOutput(
            output=combined,
            raw_output=result.stdout,
            error=(None if result.exit_code == 0
                   else f"gemini failed (exit {result.exit_code}): {combined[:300]}"),
        )


def parse_gemini_output(stdout: str, stderr: str = "") -> RunOutput | None:
    """Parse ``gemini -o json``.

    Schema: ``{"response": str, "stats": {"models": {name: {tokens: {...}}},
    "tools": {"totalCalls": int, "byName": {tool: {...}}}}}``. Returns ``None`` when the output isn't
    JSON (older CLI) — the caller falls back to plain text.
    """
    try:
        data = json.loads(stdout.strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    stats = data.get("stats") or {}
    in_tok = out_tok = turns = 0
    # gemini can route parts of one session through more than one model (a cheap flash model for a
    # sub-step, say). Tokens are summed across all of them, and the reported `model` is whichever
    # made the most API calls — so the headline name is what actually did the work, rather than
    # whichever key happened to be first in the dict.
    requests_by_model: dict[str, int] = {}
    for mname, mstats in (stats.get("models") or {}).items():
        tokens = (mstats or {}).get("tokens") or {}
        in_tok += tokens.get("prompt") or tokens.get("input") or 0
        out_tok += tokens.get("candidates") or tokens.get("output") or 0
        # One API request == one turn. gemini has no turn counter, but a round-trip to the model is
        # exactly what "turns to answer" counts for the other harnesses.
        requests_by_model[str(mname)] = (mstats or {}).get("api", {}).get("totalRequests") or 0
        turns += requests_by_model[str(mname)]
    model = max(requests_by_model, key=lambda k: requests_by_model[k]) if requests_by_model else None

    # Tool usage: stats.tools.byName is a COMPLETE per-tool tally for the session.
    tools = stats.get("tools")
    tool_counts: dict[str, int] = {}
    skills: list[SkillTrigger] = []
    skill_opened: bool | None = None
    if isinstance(tools, dict) and ("byName" in tools or "totalCalls" in tools):
        by_name = tools.get("byName") or {}
        if isinstance(by_name, dict):
            tool_counts = {str(name): _tool_count(v) for name, v in by_name.items()}
        # A skill is activated via the `activate_skill` tool → it appears in byName. The tally is
        # complete, so no skill-tool call means the skill was genuinely not activated.
        skill_tools = [n for n in tool_counts if "skill" in n.lower()]
        if skill_tools:
            skill_opened = True
            skills = [SkillTrigger(name=n, source=TriggerSource.TOOL_USE) for n in skill_tools]
        else:
            skill_opened = False

    return RunOutput(
        output=(data.get("response") or "").strip(),
        raw_output=stdout,
        model=model,
        input_tokens=in_tok or None,
        output_tokens=out_tok or None,
        # gemini exposes no turn field, but one API request IS one model call, so this is
        # exactly the shared definition — nothing inferred.
        num_turns=turns or None,
        tools_used=sorted(tool_counts),
        tool_counts=tool_counts,
        skills_triggered=skills,
        skill_opened=skill_opened,  # definitive True/False when a tools block is present; else None
    )


def _tool_count(v: object) -> int:
    """Best-effort call count for one entry in ``stats.tools.byName`` (schema varies)."""
    if isinstance(v, dict):
        for k in ("count", "calls", "totalCalls", "total"):
            if isinstance(v.get(k), int):
                return v[k]
        return 1
    return v if isinstance(v, int) else 1
