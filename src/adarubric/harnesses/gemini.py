"""Gemini CLI harness adapter.

Runs `gemini -y --output-format stream-json` — the full live trajectory, one JSON event per line
(verified against gemini-cli's own source: packages/core/src/output/types.ts):

    {"type":"init","session_id":...,"model":...}
    {"type":"message","role":"user"|"assistant","content":...,"delta":true?}
    {"type":"tool_use","tool_name":...,"tool_id":...,"parameters":{...}}       <- file paths live here
    {"type":"tool_result","tool_id":...,"status":"success"|"error","output":...}
    {"type":"result","status":...,"stats":{input_tokens,output_tokens,total_tokens,cached,
                                           duration_ms,tool_calls,models:{name:{...}}}}

Why stream-json and not `-o json`: the summary JSON only tallies tool calls by name
(`read_file: 4`), while the stream carries each call's ARGUMENTS — so we can see WHICH files were
read, which is what makes `skill_files_read` (and a real `skill_depth`) measurable for gemini. It
also means the live terminal view shows every tool call as it happens.

Fallbacks, in order (older CLIs): stream-json unsupported → `-o json` summary (tool tally, no file
paths) → plain text. An unsupported flag makes the CLI exit immediately with an argument error —
nothing runs and nothing is spent before the fallback.

Skill usage: the stream is the COMPLETE trajectory, so the answer is definitive — a skill tool call
or a read under `.../skills/<name>/` → True; tool calls present but none touching a skill → False;
no trajectory at all (plain-text fallback) → None (unknown).

Headless trust: gemini-cli refuses `-y` (YOLO) in an "untrusted" folder and exits 55 unless the
workspace is trusted. In a sandbox the workspace IS the isolation, so we set
`GEMINI_CLI_TRUST_WORKSPACE=true` (the CLI's own documented headless escape hatch).
"""

from __future__ import annotations

import json
import re

from adarubric.core.contracts import PROMPT_RELPATH, Harness, RunCommand
from adarubric.core.models import RunOutput, SkillTrigger, TriggerSource
from adarubric.core.turns import ReplyCounter

# Headless/automated escape hatch for gemini-cli's trusted-folder gate (else `-y` → exit 55).
_TRUST_ENV = {"GEMINI_CLI_TRUST_WORKSPACE": "true"}

#: A path inside a skill-discovery dir, in tool parameters. Matches `/`, `\` and the JSON-escaped
#: `\\` so it works on POSIX containers and Windows-local runs alike (same pattern the ACP
#: harness uses on its JSON-encoded haystacks).
_SKILL_PATH_RE = re.compile(
    r"(?:\\{1,2}|/)\.(?:claude|agents|gemini|codex)(?:\\{1,2}|/)skills(?:\\{1,2}|/)([^\\/\s'\"]+)"
)
_SKILL_MD_RE = re.compile(r"SKILL\.md", re.IGNORECASE)


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
        # Full trajectory first: every tool call with its arguments, streamed live.
        result = run_command(
            f'gemini -y --output-format stream-json{model_flag} < "{PROMPT_RELPATH}"', _TRUST_ENV)
        out = parse_gemini_stream(result.stdout, result.stderr, result.exit_code)
        if out is not None:
            return out
        if result.exit_code == 0 and result.stdout.strip():
            # It RAN (tokens were spent) but produced no stream events — don't run it again and
            # pay twice; keep what it said. Only an argument error (nothing ran) falls through.
            return RunOutput(output=result.stdout.strip(), raw_output=result.stdout)
        # Older CLI without stream-json: the summary JSON (tool tally, no file paths).
        result = run_command(f'gemini -y -o json{model_flag} < "{PROMPT_RELPATH}"', _TRUST_ENV)
        out = parse_gemini_output(result.stdout, result.stderr)
        if out is not None:
            return out
        # Older still (no -o json): plain-text run. A nonzero exit is a real failure.
        result = run_command(f'gemini -y{model_flag} < "{PROMPT_RELPATH}"', _TRUST_ENV)
        combined = (result.stdout + "\n" + result.stderr).strip()
        return RunOutput(
            output=combined,
            raw_output=result.stdout,
            error=(None if result.exit_code == 0
                   else f"gemini failed (exit {result.exit_code}): {combined[:300]}"),
        )


def parse_gemini_stream(stdout: str, stderr: str = "", exit_code: int = 0) -> RunOutput | None:
    """Parse ``gemini --output-format stream-json`` (one JSON event per line — see module doc).

    Returns ``None`` when the output holds no stream events at all (older CLI rejected the flag) —
    the caller decides whether to fall back.
    """
    events: list[dict] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict) and ev.get("type"):
            events.append(ev)
    if not events:
        return None

    model: str | None = None
    blocks: list[str] = []     # assistant messages; delta chunks concatenate into the open block
    block_open = False
    tool_counts: dict[str, int] = {}
    skills: list[SkillTrigger] = []
    seen: set[tuple] = set()
    saw_tool_use = False
    result_ev: dict = {}
    replies = ReplyCounter()   # same "new output after nothing outstanding" rule as claude/acp

    for ev in events:
        etype = ev.get("type")
        if etype == "init":
            model = model or ev.get("model")
        elif etype == "message":
            if ev.get("role") == "assistant" and ev.get("content"):
                replies.output()
                if ev.get("delta") and block_open and blocks:
                    blocks[-1] += str(ev["content"])
                else:
                    blocks.append(str(ev["content"]))
                block_open = True
        elif etype == "tool_use":
            saw_tool_use = True
            block_open = False
            replies.started(str(ev.get("tool_id") or ""))
            name = str(ev.get("tool_name") or "tool")
            tool_counts[name] = tool_counts.get(name, 0) + 1
            _sniff_skills(name, ev.get("parameters") or {}, skills, seen)
        elif etype == "tool_result":
            replies.finished(str(ev.get("tool_id") or ""))
        elif etype == "result":
            result_ev = ev

    stats = result_ev.get("stats") or {}
    models = stats.get("models") or {}
    if model is None and isinstance(models, dict) and models:
        # No init event (shouldn't happen) — name the model that did the most work.
        model = max(models, key=lambda k: (models[k] or {}).get("total_tokens") or 0)

    error: str | None = None
    if result_ev.get("status") == "error":
        err = result_ev.get("error") or {}
        error = f"gemini result: {err.get('type') or 'error'} - {err.get('message') or ''}".strip(" -")
    elif not result_ev and exit_code != 0:
        combined = (stderr or stdout).strip()
        error = f"gemini failed (exit {exit_code}): {combined[:300]}"

    # The stream is the complete trajectory → definitive. Skill evidence → True; tools reported
    # and none was a skill → False; a run that finished without a single tool call → also False.
    if skills:
        skill_opened: bool | None = True
    elif saw_tool_use or result_ev:
        skill_opened = False
    else:
        skill_opened = None

    return RunOutput(
        output="\n".join(b for b in blocks if b).strip(),
        raw_output=stdout,
        model=model,
        input_tokens=stats.get("input_tokens"),
        output_tokens=stats.get("output_tokens"),
        total_tokens=stats.get("total_tokens"),
        cached_input_tokens=stats.get("cached"),
        num_turns=replies.value or None,
        tools_used=sorted(tool_counts),
        tool_counts=tool_counts,
        skills_triggered=skills,
        skill_opened=skill_opened,
        skill_files_read=[s.details for s in skills
                          if s.source == TriggerSource.FILE_READ and s.details],
        error=error,
    )


def _sniff_skills(
    tool_name: str, params: dict, skills: list[SkillTrigger], seen: set
) -> None:
    """Skill evidence in ONE tool call: the skill tool itself, or a file path under a skills dir."""
    if "skill" in tool_name.lower():
        sname = str(params.get("name") or params.get("skill") or params.get("skill_name")
                    or tool_name)
        key = ("invoke", sname)
        if key not in seen:
            seen.add(key)
            skills.append(SkillTrigger(name=sname, source=TriggerSource.TOOL_USE))
        return
    # The parameters carry the file paths (absolute_path for read_file, command for shell, …).
    # JSON-encode the whole dict so no parameter name has to be guessed.
    haystack = json.dumps(params) if params else ""
    m = _SKILL_PATH_RE.search(haystack)
    if m:
        key = ("read", m.group(1), tool_name)
        if key not in seen:
            seen.add(key)
            skills.append(SkillTrigger(name=m.group(1), source=TriggerSource.FILE_READ,
                                       details=haystack.strip()[:200]))
    elif _SKILL_MD_RE.search(haystack):
        key = ("read", "SKILL.md", tool_name)
        if key not in seen:
            seen.add(key)
            skills.append(SkillTrigger(name="SKILL.md", source=TriggerSource.FILE_READ,
                                       details=haystack.strip()[:200]))


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
