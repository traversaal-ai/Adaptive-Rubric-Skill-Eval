# Coding-Agent Harnesses

A **harness** is one coding-agent CLI under test (Claude Code, Gemini CLI, Codex, …). AdaRubric runs
a skill/task on a harness inside an isolated sandbox, captures what it did, and measures whether it
actually **opened the skill** — the paper-critical signal.

This doc covers: (1) the three built-in harnesses and how to use them, and (2) a step-by-step guide
to attaching **any other agentic harness**, including generic ACP-compatible agents.

---

## 1. The harness contract

Every harness is a subclass of [`Harness`](src/adarubric/core/contracts.py) and declares five things
plus one method:

| Field | Meaning |
|-------|---------|
| `name` | Registry id used on the CLI (`--harness <name>`). |
| `cli` | The executable invoked inside the sandbox (`claude`, `gemini`, `codex`). |
| `model` | Optional model pin (set by `--model`); `None` → the CLI's own default. Passed to the CLI as `--model`/`-m`. |
| `env_keys` | The environment variable(s) it needs (e.g. `ANTHROPIC_API_KEY`). Only these are injected — **no key auto-detection**. |
| `skill_dirs` | The dir(s) this harness genuinely searches for skills, so injection is **fair** across harnesses (a valid cross-harness `skill_opened`). |
| `docker_install` | Shell snippet that installs the CLI inside a Debian-based image (used by the Docker sandbox overlay). |
| `run(instruction, workspace, run_command)` | Invokes the CLI (prompt fed via stdin from `.adarubric/prompt.md`) and returns a parsed `RunOutput`. |

The harness never needs to know whether it's local or in Docker — it only calls the `run_command`
callback the active sandbox supplies.

---

## 2. Built-in harnesses

| Harness | `--harness` | CLI | Env key | Skill dir | `skill_opened` observability |
|---------|-------------|-----|---------|-----------|------------------------------|
| Claude Code | `claude-code` | `claude` | `ANTHROPIC_API_KEY` | `.claude/skills` | **Full** — `stream-json` trajectory shows the `Skill(...)` tool call and skill-file reads → definitive `True`/`False`. |
| Gemini CLI | `gemini-cli` | `gemini` | `GEMINI_API_KEY` | `.gemini/skills` | **None yet** — no per-tool trajectory exposed → honest `None` (unknown). |
| Codex | `codex` | `codex` | `OPENAI_API_KEY` | `.agents/skills` | **Partial** — skills are injected as `<skill>` prompt fragments (invisible); we detect explicit file reads / markers → `True` on evidence, else `None`. |

### How each is invoked

- **claude-code** — `claude -p --output-format stream-json --verbose --dangerously-skip-permissions --max-budget-usd 5 [--model M] < .adarubric/prompt.md` (with `IS_SANDBOX=1` so `--dangerously-skip-permissions` is allowed as root in a container). Reports `total_cost_usd`.
- **gemini-cli** — `gemini -y -o json [-m M] < .adarubric/prompt.md` (yolo auto-approve, JSON stats); falls back to plain-text on older CLIs.
- **codex** — a best-effort `codex login --with-api-key` preflight, then `codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check [-m M] --json < .adarubric/prompt.md`. The codex CLI does **not** report cost — AdaRubric estimates it from tokens when the model is known.

### Running them

```bash
# Claude Code, locally, on a plain skill folder
uv run adarubric run ./my-skill --harness claude-code --instruction "Do the thing" \
    --env-file .env

# Pin a specific model (recorded in eval.yaml)
uv run adarubric run ./my-skill --harness claude-code --model claude-opus-4-8 --env-file .env

# A SkillsBench task, faithfully, in Docker (required for the task's real environment + verifier)
uv run adarubric run dataset/skillsbench/tasks/dialogue-parser --harness claude-code \
    --sandbox docker --env-file .env

# The same task on several harnesses in one launch
uv run adarubric run <task> --harness claude-code,codex,gemini-cli --sandbox docker --env-file .env
```

The chosen harness's env key(s) must be present (in the environment or `--env-file`) or the run fails
fast. Only that harness's declared key is injected into the sandbox — never the whole environment.

---

## 3. Adding a new agentic harness (native CLI)

If the agent ships a CLI you can run non-interactively, adding it is three small steps.

**Step 1 — Write the adapter.** Create `src/adarubric/harnesses/<yours>.py`:

```python
from adarubric.core.contracts import PROMPT_RELPATH, Harness, RunCommand
from adarubric.core.models import RunOutput


class MyAgentHarness(Harness):
    name = "my-agent"                       # → --harness my-agent
    cli = "myagent"
    env_keys = ("MYAGENT_API_KEY",)         # only this key is injected
    skill_dirs = (".myagent/skills",)       # where THIS agent discovers skills (be honest — fairness!)
    docker_install = (                      # how to install the CLI in a Debian image (docker sandbox)
        "npm install -g @vendor/myagent-cli && myagent --version"
    )

    def run(self, instruction: str, workspace: str, run_command: RunCommand) -> RunOutput:
        model_flag = f" --model {self.model}" if self.model else ""
        # Feed the prompt via stdin from the canonical file — no shell escaping, cross-platform.
        result = run_command(f'myagent run --json{model_flag} < "{PROMPT_RELPATH}"')
        return _parse(result.stdout, result.stderr, result.exit_code)
```

**Step 2 — Parse the output into a `RunOutput`.** Populate whatever the CLI exposes: `output`,
`model`, `input_tokens`/`output_tokens`, `num_turns`, `cost_usd`, `tool_counts`. For the paper signal
set **`skill_opened`** honestly:
- Full trajectory visible → definitive `True`/`False` (see `parse_stream_json` in [claude.py](src/adarubric/harnesses/claude.py)).
- Only partial visibility → `True` on explicit evidence (a `.../skills/<name>/...` read or a marker), else `None` (never a fabricated `False`) — see [codex.py](src/adarubric/harnesses/codex.py).
- **Never** silently return success when the CLI produced no trajectory — set `RunOutput.error` so the runner marks the trial failed (an empty/garbage run must not look like a pass).

**Step 3 — Register it.** One line in [`src/adarubric/harnesses/registry.py`](src/adarubric/harnesses/registry.py):

```python
from adarubric.harnesses.myagent import MyAgentHarness

_REGISTRY = {
    ...,
    "my-agent": MyAgentHarness,
}
```

That's it — `--harness my-agent` now works on both `--sandbox local` and `--sandbox docker`, with
fair skill injection and metrics.

---

## 4. Attaching an ACP-compatible harness

The **[Agent Client Protocol](https://agentclientprotocol.com/)** (ACP) is a standard for
editor/client ↔ agent communication over **JSON-RPC 2.0 on stdio**. It lets you drive *any*
ACP-speaking agent (e.g. `gemini --acp`) without wiring a bespoke CLI parser — the protocol gives you
a structured session with text updates and tool-call notifications. The reference for the shape is
skillgrade's `src/agents/acp.ts`.

An ACP harness differs from a native-CLI harness: instead of one `run_command(... < prompt)` call, it
spawns the agent as a **long-lived subprocess** and speaks the protocol to it. The cleanest fit for
AdaRubric's `run_command`-based contract is a small **ACP client** that runs on the host and connects
to the agent process inside the sandbox.

**Step-by-step:**

1. **Add the ACP client dependency.** Use an ACP SDK (JS: `@agentclientprotocol/sdk`; or a Python
   ACP client) — declare it in `pyproject.toml`. The agent is launched with an ACP flag, e.g.
   `gemini --acp`.

2. **Create `src/adarubric/harnesses/acp.py`** with an `AcpHarness(Harness)` that carries a
   `command` (how to start the ACP agent, e.g. `"gemini --acp"`) alongside the usual `name` / `cli` /
   `env_keys` / `skill_dirs`. Accept the start command via the constructor so one adapter serves many
   ACP agents.

3. **In `run()`, drive the protocol instead of a one-shot command:**
   - **Spawn** the ACP agent process in the workspace (cwd = the sandbox workspace).
   - **Initialize** the connection (`initialize` with `protocolVersion` + client capabilities), then
     **authenticate** using the harness's env key if the agent advertises an api-key auth method.
   - **Create a session** (`newSession` with `cwd`), then **send the prompt** as a `text`
     content block (`prompt({ sessionId, prompt: [{type:'text', text: instruction}] })`).
   - **Collect `sessionUpdate` notifications** — accumulate text content, and record every tool-call
     notification (name + status). Auto-approve `requestPermission` requests (evaluation context).
   - On the final response, map `stopReason`, the accumulated text, tool counts, and any token stats
     into a `RunOutput`.

4. **Measure `skill_opened` from the tool-call stream.** ACP surfaces tool calls as session updates,
   so watch for a `Skill`/skill-invocation tool or a read of a `.../skills/<name>/...` path — the same
   operational definition as the other harnesses. If the agent's ACP updates don't expose tools, set
   `skill_opened = None` (unknown), honestly.

5. **Register it**, optionally parameterized per agent:

   ```python
   _REGISTRY = {
       ...,
       "acp": lambda model=None: AcpHarness(command="gemini --acp", model=model),
   }
   ```

6. **Clean up** — kill the agent subprocess and drop the connection at the end of `run()` (ACP agents
   are long-lived; a leaked process will hang the trial).

> **Note on sandboxing:** an ACP client talks to the agent over stdio, so for `--sandbox docker` the
> agent process must live in the container and the client must reach its stdio (e.g. run the client
> inside the container, or bridge stdio through `docker exec`). For a first cut, wire ACP on
> `--sandbox local`; the Docker bridge is a follow-up.

---

## 5. Fairness & the `skill_opened` metric

`skill_opened` is only comparable across harnesses if **each harness's skill is injected into the dir
that harness actually searches** (`skill_dirs`) — that's why every adapter declares its own. The
runner injects each skill into every `skill_dir` (relative to the workspace for local, the container
HOME for docker) so the agent can genuinely discover it. Getting `skill_dirs` right for a new harness
is the single most important correctness detail — a wrong path makes the skill undiscoverable and the
metric meaningless.
