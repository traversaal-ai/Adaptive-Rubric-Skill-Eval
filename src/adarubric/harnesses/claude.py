"""Claude Code harness adapter.

Runs `claude -p --output-format stream-json --verbose`, feeding the instruction via stdin
redirection from the canonical prompt file the sandbox wrote (cross-platform, container-safe,
no shell escaping).

We use **stream-json** (the full trajectory), not plain `json` (final answer only), so we can
measure *skill usage* from the tool-call events — the paper-critical signal:
  * a `Skill(name=…)` tool call  → the agent explicitly invoked the skill (body loaded)
  * a `Read`/`Bash` touching `.../<harness>/skills/<name>/...` → it read a skill file
`skill_opened` is then a definitive True/False (not `null`): the whole trajectory is visible, so
"no skill event seen" means the agent did not open the skill during the run.
"""

from __future__ import annotations

import json
import re

from adarubric.core.contracts import PROMPT_RELPATH, Harness, RunCommand
from adarubric.core.models import RunOutput, SkillTrigger, TriggerSource

# Matches an injected skill file path in any harness discovery dir (Windows or POSIX separators).
_SKILL_PATH_RE = re.compile(r"[\\/]\.(?:claude|agents|gemini|codex)[\\/]skills[\\/]([^\\/\s]+)")


class ClaudeHarness(Harness):
    name = "claude-code"
    cli = "claude"
    env_keys = ("ANTHROPIC_API_KEY",)
    skill_dirs = (".claude/skills",)  # claude-code discovers skills here (project + ~ )
    # Native installer (no node needed); binary lands in ~/.local/bin — symlink onto PATH.
    docker_install = (
        "curl -fsSL https://claude.ai/install.sh | bash && "
        "ln -sf /root/.local/bin/claude /usr/local/bin/claude && claude --version"
    )

    def run(self, instruction: str, workspace: str, run_command: RunCommand) -> RunOutput:
        # stream-json requires --verbose in print mode. --max-budget-usd caps spend (configurable
        # later). IS_SANDBOX=1 lets --dangerously-skip-permissions run as root inside containers
        # (the container IS the sandbox); passed as env so it works on both cmd.exe and sh.
        model_flag = f" --model {self.model}" if self.model else ""
        # self.cli, not a literal: wrappers around the same binary (e.g. TogetherLink's `tclaude`)
        # subclass this harness and only swap the command name — output format is identical.
        result = run_command(
            f"{self.cli} -p --output-format stream-json --verbose "
            f'--dangerously-skip-permissions --max-budget-usd 5{model_flag} < "{PROMPT_RELPATH}"',
            {"IS_SANDBOX": "1"},
        )
        return parse_stream_json(result.stdout, result.stderr, exit_code=result.exit_code)


def parse_stream_json(stdout: str, stderr: str = "", exit_code: int = 0) -> RunOutput:
    """Parse `claude --output-format stream-json` (one JSON object per line: system/assistant/user/result)."""
    events = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not events:
        # No trajectory at all → the CLI itself failed (bad flags, refused to start, auth error).
        # This must surface as a failed attempt, never a silent success.
        combined = (stdout + "\n" + stderr).strip()
        return RunOutput(
            output=combined,
            raw_output=stdout or stderr,
            error=f"claude produced no stream-json (exit {exit_code}): {combined[:300]}",
        )

    tool_counts: dict[str, int] = {}
    skills: list[SkillTrigger] = []
    seen: set[tuple] = set()
    text_parts: list[str] = []
    result_obj: dict = {}
    model: str | None = None
    #: One model reply can arrive as SEVERAL `assistant` events — one per content block (a text block,
    #: then a tool_use block, ...). They share a message id, so distinct ids = distinct replies. On a
    #: real run that was 34 events / 15 ids, while claude's own `num_turns` said 20 (it counts its
    #: internal steps, including tool-result messages). We report both; see core/turns.py.
    reply_ids: list[str] = []

    for ev in events:
        etype = ev.get("type")
        if etype == "system" and not model:
            model = ev.get("model")
        elif etype == "assistant":
            msg_id = ((ev.get("message") or {}).get("id"))
            if msg_id and msg_id not in reply_ids:
                reply_ids.append(str(msg_id))
            for block in (ev.get("message") or {}).get("content", []) or []:
                btype = block.get("type")
                if btype == "text":
                    text_parts.append(block.get("text", ""))
                elif btype == "tool_use":
                    _record_tool_use(block, tool_counts, skills, seen)
        elif etype == "result":
            result_obj = ev

    usage = result_obj.get("usage") or {}
    model = model or result_obj.get("model")
    skill_files_read = [s.details for s in skills if s.source == TriggerSource.FILE_READ and s.details]
    output = result_obj.get("result") or "\n".join(p for p in text_parts if p).strip()
    error = None
    if result_obj.get("is_error") or (
        result_obj.get("subtype") and result_obj.get("subtype") != "success"
    ):
        error = f"claude result: {result_obj.get('subtype') or 'error'} — {str(output)[:300]}"

    return RunOutput(
        output=output,
        raw_output=stdout,
        model=model,
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        # Ours (comparable) and claude's own (different definition) — both kept.
        num_turns=len(reply_ids) or None,
        num_turns_reported=result_obj.get("num_turns"),
        cost_usd=result_obj.get("total_cost_usd"),
        tools_used=sorted(tool_counts),
        tool_counts=tool_counts,
        skills_triggered=skills,
        skill_opened=bool(skills),  # definitive: full trajectory was inspected
        skill_files_read=skill_files_read,
        error=error,
    )


def _record_tool_use(
    block: dict, tool_counts: dict[str, int], skills: list[SkillTrigger], seen: set
) -> None:
    name = block.get("name", "?")
    tool_counts[name] = tool_counts.get(name, 0) + 1
    inp = block.get("input") or {}

    # 1) Explicit skill invocation via the Skill tool.
    if name.lower() == "skill":
        sname = str(inp.get("name") or inp.get("command") or inp.get("skill") or "?")
        key = ("invoke", sname)
        if key not in seen:
            seen.add(key)
            skills.append(SkillTrigger(name=sname, source=TriggerSource.TOOL_USE))
        return

    # 2) Reading a skill file via Read/Bash/Grep.
    haystack = " ".join(
        str(inp.get(k, "")) for k in ("file_path", "path", "command", "pattern")
    )
    m = _SKILL_PATH_RE.search(haystack)
    if m:
        sname = m.group(1)
        key = ("read", sname, name)
        if key not in seen:
            seen.add(key)
            skills.append(
                SkillTrigger(name=sname, source=TriggerSource.FILE_READ, details=haystack.strip()[:200])
            )
