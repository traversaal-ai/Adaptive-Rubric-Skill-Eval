"""OpenAI Codex CLI harness adapter.

Runs `codex exec --json` (JSONL trajectory) with the prompt fed via stdin redirection from the
canonical prompt file. Auth: codex >= 0.14 doesn't read OPENAI_API_KEY directly — a best-effort
`codex login --with-api-key` preflight (POSIX; harmless no-op elsewhere/already-authed) writes
`~/.codex/auth.json`.

Skill-usage measurement (paper caveat, matching SkillsBench's own audit): codex injects skills as
`<skill>` prompt fragments, NOT tool calls — implicit injections are invisible in the trajectory.
We detect *explicit evidence* (shell commands touching a `.../skills/<name>/…` path, or `<skill>`
markers in output). Evidence → skill_opened=True; no evidence → **None** (unknown), never a false
False. This partial observability is inherent to codex, not our harness.
"""

from __future__ import annotations

import json
import re

from adarubric.core.contracts import PROMPT_RELPATH, Harness, RunCommand
from adarubric.core.models import RunOutput, SkillTrigger, TriggerSource

_SKILL_PATH_RE = re.compile(r"[\\/]\.(?:claude|agents|gemini|codex)[\\/]skills[\\/]([^\\/\s'\"]+)")
_SKILL_MARKER_RE = re.compile(r"<skill>\s*<name>([^<]+)</name>", re.DOTALL)


class CodexHarness(Harness):
    name = "codex"
    cli = "codex"
    env_keys = ("OPENAI_API_KEY",)
    skill_dirs = (".agents/skills",)  # codex discovers skills under .agents/skills (NOT .claude)
    # Static musl binary from GitHub releases — no node required.
    docker_install = (
        "curl -fsSL -o /tmp/codex.tar.gz "
        "https://github.com/openai/codex/releases/latest/download/codex-x86_64-unknown-linux-musl.tar.gz "
        "&& tar -xzf /tmp/codex.tar.gz -C /tmp "
        "&& mv /tmp/codex-x86_64-unknown-linux-musl /usr/local/bin/codex "
        "&& chmod +x /usr/local/bin/codex && codex --version"
    )

    def run(self, instruction: str, workspace: str, run_command: RunCommand) -> RunOutput:
        # Best-effort API-key login (writes ~/.codex/auth.json). POSIX-only; ignored elsewhere.
        run_command(
            "sh -c 'if [ -n \"$OPENAI_API_KEY\" ]; then "
            "printenv OPENAI_API_KEY | codex login --with-api-key >/dev/null 2>&1 || true; fi' "
            "2>nul || true"
        )
        # --dangerously-bypass...: the sandbox IS the isolation; codex's own sandbox needs
        # unprivileged userns unavailable in containers. --skip-git-repo-check: temp dirs aren't repos.
        model_flag = f" -m {self.model}" if self.model else ""
        result = run_command(
            "codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check "
            f'{model_flag} --json < "{PROMPT_RELPATH}"'
        )
        return parse_codex_jsonl(result.stdout, result.stderr)


#: Where codex has been seen to name its model. It announces this on a session/thread event whose
#: exact shape has moved between releases, so rather than hard-coding one path we check the handful
#: of plausible containers. Recording the real name matters: without it a run is filed as "default"
#: and you can no longer tell which model produced the result.
_MODEL_CONTAINERS = ("", "item", "session", "thread", "turn", "config")


def _find_model(ev: dict) -> str | None:
    """Pull a model name out of one codex JSONL event, whichever shape this version uses."""
    for key in _MODEL_CONTAINERS:
        holder = ev if key == "" else ev.get(key)
        if isinstance(holder, dict):
            value = holder.get("model")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def parse_codex_jsonl(stdout: str, stderr: str = "") -> RunOutput:
    """Parse `codex exec --json` JSONL: item.completed (agent_message / command_execution),
    turn.completed (usage)."""
    tool_counts: dict[str, int] = {}
    skills: list[SkillTrigger] = []
    seen: set[tuple] = set()
    messages: list[str] = []
    errors: list[str] = []
    in_tok = out_tok = turns = cached_in = 0
    parsed_any = False
    model: str | None = None

    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        parsed_any = True
        etype = ev.get("type")
        item = ev.get("item") or {}
        if model is None:
            model = _find_model(ev)
        if etype == "item.completed":
            itype = item.get("type")
            if itype == "agent_message" and item.get("text"):
                messages.append(item["text"])
                # One model reply == one turn. Codex's own `turn.completed` counts PROMPT cycles, so
                # it is always 1 for our single-prompt runs — a 10-command session reported "1 turn"
                # while claude reported 12 for comparable work, making the column meaningless.
                turns += 1
            elif itype == "command_execution":
                tool_counts["command_execution"] = tool_counts.get("command_execution", 0) + 1
                m = _SKILL_PATH_RE.search(item.get("command") or "")
                if m and ("read", m.group(1)) not in seen:
                    seen.add(("read", m.group(1)))
                    skills.append(
                        SkillTrigger(
                            name=m.group(1),
                            source=TriggerSource.FILE_READ,
                            details=(item.get("command") or "")[:200],
                        )
                    )
            elif itype in ("tool_use", "function_call"):
                tname = item.get("name") or "unknown"
                tool_counts[tname] = tool_counts.get(tname, 0) + 1
        elif etype == "turn.completed":
            # Usage only — NOT a turn count (see the agent_message branch above).
            usage = ev.get("usage") or {}
            in_tok += usage.get("input_tokens") or 0
            out_tok += usage.get("output_tokens") or 0
            cached_in += usage.get("cached_input_tokens") or 0
        elif etype in ("error", "turn.failed"):
            msg = ev.get("message") or (ev.get("error") or {}).get("message") or json.dumps(ev)
            errors.append(str(msg)[:500])

    # Explicit <skill> injection markers echoed anywhere in the stream (scan parsed text too —
    # inside raw JSONL the newlines are escaped).
    for m in _SKILL_MARKER_RE.finditer(stdout + "\n" + "\n".join(messages)):
        sname = m.group(1).strip()
        if ("marker", sname) not in seen:
            seen.add(("marker", sname))
            skills.append(SkillTrigger(name=sname, source=TriggerSource.TOOL_USE))

    if not parsed_any:
        # No JSONL at all → the CLI itself failed. Must surface as a failed attempt.
        combined = (stdout + "\n" + stderr).strip()
        return RunOutput(
            output=combined,
            raw_output=stdout or stderr,
            error=f"codex produced no JSONL: {combined[:300]}",
        )

    return RunOutput(
        output="\n".join(messages).strip(),
        raw_output=stdout,
        model=model,
        input_tokens=in_tok or None,
        output_tokens=out_tok or None,
        cached_input_tokens=cached_in or None,
        num_turns=turns or None,
        tools_used=sorted(tool_counts),
        tool_counts=tool_counts,
        skills_triggered=skills,
        # Codex trajectories are only PARTIALLY observable for skills → evidence gives True,
        # absence gives None (unknown), never a false False.
        skill_opened=True if skills else None,
        skill_files_read=[s.details for s in skills if s.source == TriggerSource.FILE_READ and s.details],
        error=("; ".join(dict.fromkeys(errors)) or None),
    )
