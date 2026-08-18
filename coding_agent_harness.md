# Coding-Agent Harnesses

A **harness** is one coding agent under test. AdaRubric runs a skill/task on a harness inside an
isolated sandbox, captures what it did, and measures whether it actually **read the skill** — and how
deeply.

This doc covers:

1. [The harness contract](#1-the-harness-contract)
2. [The built-in harnesses](#2-the-built-in-harnesses) — what each can and can't tell us
3. [Skill discovery](#3-skill-discovery--the-single-most-important-detail)
4. [Adding your own CLI harness](#4-adding-your-own-cli-harness)
5. [The generic ACP harness](#5-the-generic-acp-harness---harness-acp) — run *any* ACP agent
6. [The oracle harness](#6-the-oracle-harness--proving-a-task-is-passable)
7. [Fairness, and what the skill metrics really mean](#7-fairness-and-what-the-skill-metrics-really-mean)

---

## 1. The harness contract

Every harness subclasses [`Harness`](src/adarubric/core/contracts.py):

| Field | Meaning |
|-------|---------|
| `name` | the id used on the CLI (`--harness <name>`) |
| `cli` | the executable run inside the sandbox (`claude`, `gemini`, `codex`) |
| `model` | optional pin from `--model`; `None` → the agent's own default |
| `env_keys` | the environment variable(s) it needs. **Only these are injected** — no auto-detection. |
| `skill_dirs` | the folder(s) *this* agent actually searches for skills. Get it wrong and the metric is meaningless — see §3. |
| `docker_install` | shell snippet installing the CLI into a Debian-based image |
| `run(instruction, workspace, run_command)` | invoke the agent and return a parsed `RunOutput` |

A harness never needs to know whether it's running locally or in Docker. The sandbox hands it what it
needs:

- **`run_command(cmd)`** — send one command, wait, get the output. Enough for a normal CLI.
- **`Sandbox.popen(workspace, cmd, env)`** — start a process that *stays alive* with open pipes. This
  is for ACP, which needs a conversation rather than a single command. Local runs it with the
  workspace as its folder; Docker wraps it in `docker exec -i`.

---

## 2. The built-in harnesses

| Harness | `--harness` | Env key | Skill dir(s) |
|---|---|---|---|
| Claude Code | `claude-code` | `ANTHROPIC_API_KEY` | `.claude/skills` |
| Gemini CLI | `gemini-cli` | `GEMINI_API_KEY` | `.gemini/skills`, `.agents/skills` |
| Codex | `codex` | `OPENAI_API_KEY` | `.agents/skills` |
| Any ACP agent | `acp` | *(you declare it)* | *(you declare it)* |
| Reference solution | `oracle` | **none** | *(none — it needs no guidance)* |

### What each one can actually tell us

Different agents expose wildly different amounts about themselves. This table is the honest picture —
`—` means the agent doesn't report it, so we record nothing rather than guessing:

| | claude-code | gemini-cli | codex | acp |
|---|---|---|---|---|
| **model it ran** | ✅ | ✅ | **—** | ✅ when the agent sends it |
| **cost** | ✅ real, reported | — (estimated) | — (estimated) | ✅ if the agent follows the spec |
| **tokens** | ✅ | ✅ | ✅ (+ cached) | ✅ if reported |
| **turns** | ✅ | ✅ derived | ✅ derived | ✅ derived |
| **skill opened** | ✅ definitive | ✅ definitive | ⚠️ evidence only | ✅ definitive |
| **skill depth** (noticed vs used) | ✅ | **—** no file paths | ✅ | ✅ |

Notes that matter:

- **Codex reports no model at all.** Its `--json` stream never names one, so `model` is `null` and
  cost is estimated from whatever you pinned with `--model`. Verified against a real run.
- **Gemini can't do depth.** It gives per-tool counts with no file paths, so we can tell it *opened* a
  skill but never whether it went deeper. It maxes out at `noticed`.
- **Gemini and claude-acp report a routing mode**, not a model — `auto` and `Default (recommended)`.
  Truthful (they pick per request) but unpriceable, so cost falls back to your pin.
- **Turns mean one thing everywhere:** how many times the model replied. Each CLI defines "turn"
  differently — codex's own counter tracks prompt cycles (always 1 for us) while claude counts
  replies — so we normalise rather than compare unlike things.

### How each is invoked

- **claude-code** — `claude -p --output-format stream-json --verbose --dangerously-skip-permissions
  --max-budget-usd 5 [--model M] < .adarubric/prompt.md`, with `IS_SANDBOX=1` so the permission skip
  is allowed as root in a container. Reports `total_cost_usd`.
- **gemini-cli** — `gemini -y -o json [-m M] < .adarubric/prompt.md`, with
  `GEMINI_CLI_TRUST_WORKSPACE=true`. **That env var is not optional**: without it gemini refuses `-y`
  in an "untrusted" folder and exits 55, which looks like an agent failure but isn't.
- **codex** — a best-effort `codex login --with-api-key`, then `codex exec
  --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check [-m M] --json <
  .adarubric/prompt.md`.

### What's actually been run

"Verified" means a real end-to-end run, not just tests:

| Harness | Verified | Notes |
|---|---|---|
| `claude-code` | ✅ Docker | richest signal; reports real cost |
| `codex` | ✅ Docker | reports no model, no cost. Installer fetches TWO binaries since codex 0.147 (`codex` + `codex-code-mode-host`) — with only the first, codex starts but runs nothing and scores 0 |
| `gemini-cli` | ✅ Docker | `skill_opened: true` measured from its tool tally |
| `acp` + gemini | ✅ Docker | multiple clean scored runs (flood-risk 1.00, threejs, …) |
| `acp` + claude | ✅ Docker | clean scored run (flood-risk 1.00) via `@zed-industries/claude-code-acp` |
| `acp` + codex | ✅ Docker | via `@zed-industries/codex-acp`; requires the ACP `authenticate` step — the client picks the auth method matching the injected env key and retries once |
| `oracle` | ✅ Docker | |
| `claude-code-together` | ⚠️ UNVERIFIED | Claude Code on Together models via TogetherLink's `tclaude` (Beta). Needs a live run before trusting results |
| `codex-together` | ⚠️ UNVERIFIED | Codex on Together models via `tcodex` (Beta). Same caveat |

---

## 3. Skill discovery — the single most important detail

`skill_opened` only means anything if the skill was put where that agent actually looks. Every
adapter declares its own paths, checked against each agent's own docs:

| Harness | Where it looks | We put it in (local = workspace · docker = `/root`) |
|---|---|---|
| claude-code | `<project>/.claude/skills/`, `~/.claude/skills/` | `.claude/skills` · `/root/.claude/skills` |
| codex | `.agents/skills/` (up to repo root), `~/.agents/skills/` | `.agents/skills` · `/root/.agents/skills` |
| gemini-cli | `.gemini/skills/` **or the `.agents/skills/` alias**, `~/.gemini/skills/` | both · `/root/.gemini/skills` + `/root/.agents/skills` |

Sources: claude-code and codex from SkillsBench's source audit
(`dataset/skillsbench/docs/harnesses/skill-invocation-surfaces.md`); gemini from
[its own docs](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/using-agent-skills.md).

**Skills are copied in at container start — never baked into the image.** SkillsBench's own review
checklist calls baking an antipattern, for a good reason: if the skill is in the image, a
`--inject-skills no-skill` control run would still see it, and the comparison would be worthless.

Control files (`grader.yaml`, `adarubric.yaml`, `TASK.md`, `eval.yaml`) are **stripped from the copy**,
so an agent can never read its own marking scheme out of a skill folder.

---

## 4. Adding your own CLI harness

If the agent has a CLI you can run non-interactively, it's three small steps.

**Step 1 — the adapter.** `src/adarubric/harnesses/<yours>.py`:

```python
from adarubric.core.contracts import PROMPT_RELPATH, Harness, RunCommand
from adarubric.core.models import RunOutput


class MyAgentHarness(Harness):
    name = "my-agent"                       # → --harness my-agent
    cli = "myagent"
    env_keys = ("MYAGENT_API_KEY",)         # only this key is injected
    skill_dirs = (".myagent/skills",)       # where THIS agent looks — be accurate (§3)
    docker_install = "npm install -g @vendor/myagent-cli && myagent --version"

    def run(self, instruction: str, workspace: str, run_command: RunCommand) -> RunOutput:
        model_flag = f" --model {self.model}" if self.model else ""
        # The prompt is fed via stdin from the canonical file: no shell escaping, works everywhere.
        result = run_command(f'myagent run --json{model_flag} < "{PROMPT_RELPATH}"')
        return _parse(result.stdout, result.stderr, result.exit_code)
```

**Step 2 — parse into a `RunOutput`.** Fill in whatever the CLI exposes: `output`, `model`,
`input_tokens` / `output_tokens` / `cached_input_tokens`, `num_turns`, `cost_usd`, `tool_counts`.

Three rules that keep the numbers trustworthy:

- **`skill_opened` honestly.** Full trajectory → definitive `True`/`False`. Partial visibility →
  `True` on evidence, else `None`. **Never a fabricated `False`** — "I can't see" is not "it didn't
  happen".
- **`num_turns` = model replies.** Not prompt cycles, not tool calls. See `_find_model` and the
  `agent_message` counter in [codex.py](src/adarubric/harnesses/codex.py) for the shape.
- **Set `RunOutput.error` when the CLI produced nothing usable.** An empty or garbage run must not
  look like a pass.

**Step 3 — register it**, one line in
[`registry.py`](src/adarubric/harnesses/registry.py):

```python
_REGISTRY = {..., "my-agent": MyAgentHarness}
```

`--harness my-agent` now works on both sandboxes.

---

## 5. The generic ACP harness (`--harness acp`)

[ACP](https://agentclientprotocol.com/) (Agent Client Protocol) is a standard for talking to coding
agents over JSON-RPC on stdin/stdout. `--harness acp` drives **any** ACP agent — z.ai, IBM, Zed's
agents, Cursor, `gemini --acp` — **with no per-agent code**.

Ours is dependency-free: a small JSON-RPC client, no SDK.

### Running it

```bash
# gemini (ACP is built into its CLI)
uv run adarubric eval <task> --harness acp \
    --acp-cmd 'gemini --acp' \
    --acp-skill-dir '.gemini/skills' \
    --acp-env-key GEMINI_API_KEY \
    --acp-install gemini-cli \
    --dataset skillbench --sandbox docker
```

```bash
# claude, via the Zed bridge — installed at BUILD time, not with npx at run time
uv run adarubric eval <task> --harness acp \
    --acp-cmd 'claude-code-acp' \
    --acp-skill-dir '.claude/skills' \
    --acp-env-key ANTHROPIC_API_KEY \
    --acp-install "curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
                   && apt-get install -y nodejs && npm i -g @zed-industries/claude-code-acp" \
    --dataset skillbench --sandbox docker
```

```bash
# codex, via the Zed bridge — NOTE: codex-acp demands the ACP `authenticate` step; the client
# handles it (picks the auth method matching the env key it injected, then retries session/new).
uv run adarubric eval <task> --harness acp \
    --acp-cmd 'codex-acp' \
    --acp-skill-dir '.agents/skills' \
    --acp-env-key OPENAI_API_KEY \
    --acp-install "curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
                   && apt-get install -y nodejs && npm i -g @zed-industries/codex-acp" \
    --dataset skillbench --sandbox docker
```

> **Don't use `npx -y` as the run command.** It downloads the package *during* the run, inside the
> container, and those thousands of npm files land in the agent's file-change count — one run showed
> **4273 files created**. Install at build time with `--acp-install` and call the binary directly.

### The flags

| Flag | Why it matters |
|---|---|
| `--acp-cmd` | required: how to start the agent |
| `--acp-skill-dir` | **the one to get right.** Default is `.agents/skills`; a claude agent needs `.claude/skills`. Wrong path → the agent never sees the skill → `skill_opened: false` for the wrong reason. |
| `--acp-env-key` | declares its key so it's injected and checked before the run starts |
| `--acp-install` | a **harness name** (e.g. `gemini-cli`) reuses that harness's installer — which already knows it must install Node before npm exists. Or pass a shell snippet. |
| `--acp-name` | the output label. Defaults to the wrapped agent (`acp-gemini`, `acp-claude-code-acp`) so different agents don't share a folder. |

### How it works under the hood

- **Flow:** `initialize` → `session/new` → `session/prompt`, then read `session/update` notifications
  until the reply arrives.
- **Both sandboxes.** The agent is started through `Sandbox.popen`: a local process, or
  `docker exec -i` into the running container. `-i` keeps stdin open; **no `-t`**, because a terminal
  would inject control characters and corrupt the protocol.
- **The session's working folder is probed, not assumed.** Under Docker the workspace handle is a
  container *ID*, not a path — sending it made gemini reject the session with *"Directory does not
  exist: /root/&lt;container-id&gt;"*. We ask the container shell for `pwd`.
- **File requests** (`fs/read_text_file`, `fs/write_text_file`) are served from your disk for local
  runs, and through the container's shell for Docker runs — base64-encoded, so a file containing
  quotes or newlines can't break the command.
- **Permission requests** are auto-approved (this is an evaluation, not an editor).
- **`raw.log` is the full protocol transcript** (`->` us, `<-` the agent). When a metric comes back
  missing, this is how you find out whether the agent ever sent it.

### Tokens and cost over ACP

ACP **does** define both, and we read both:

| what | where |
|---|---|
| tokens | `usage` on the reply — `input_tokens`, `output_tokens`, `total_tokens`, `thought_tokens`, `cached_read_tokens` |
| **cost** | the `usage_update` notification — `{amount, currency}`, cumulative for the session |
| context window | `usage_update` — `used` / `size` |

Cost arrives **only** in that notification. Non-USD amounts are noted, never silently counted as
dollars.

**gemini-cli is off-spec here:** it reports tokens at `_meta.quota.token_count` and no cost at all
([gemini-cli#24280](https://github.com/google-gemini/gemini-cli/issues/24280)). We read that fallback
too, so you get a token total (no input/output split) and no cost. claude-acp and codex-acp follow the
spec.

### Skill detection over ACP

From the tool-call stream, using the same signals SkillsBench's own audit looks for:

- a dedicated skill tool (claude's `"Launching skill: <name>"`)
- a read under `.../skills/<name>/...`
- any `SKILL.md` read (the fallback for unknown agents)

Verdict is definitive both ways: evidence → `True`; tool calls reported but none a skill → `False`;
**no tool calls at all → `None`**, because silence isn't proof of inaction.

### Expect rough edges on a new agent

The client is built from the spec and tested against a mock agent
([`tests/test_acp_harness.py`](tests/test_acp_harness.py), 25 tests). Real agents have already
surfaced three things the mock couldn't:

1. a rejected working directory (the container-ID bug above)
2. **text split mid-word** — streamed chunks are fragments of one sentence and were being joined with
   newlines, mangling every transcript
3. **tool IDs mistaken for tool names** — counting by `toolCallId` made every call its own "tool" and
   inflated the call count 4×, since ACP sends one `tool_call` plus several progress updates

All three are fixed and pinned by tests. If a new agent misbehaves, read `raw.log` first.

---

## 6. The oracle harness — proving a task is passable

`--harness oracle` isn't an agent. It runs the task's own `oracle/solve.sh` — the worked solution its
author wrote — and lets the task's real grader score it. **A healthy task scores 1.00.**

Use it through the dedicated command:

```bash
uv run adarubric check dataset/skillsbench/tasks/invoice-fraud-detection
```

Free: no model, no key, no tokens. If it doesn't score 1.00, the **task** is broken and any agent
score from it is meaningless. SkillsBench's own scripts do this first and abort if it fails, in their
words *"so we don't burn budget"*.

Two safety properties:

- The oracle is a worked *answer*, so it's staged **only** for this harness. A test asserts a normal
  agent run stages nothing.
- Asking for `--harness oracle` on a task with no oracle stops with a clear message rather than being
  recorded as the oracle "failing".

---

## 7. Fairness, and what the skill metrics really mean

**`skill_opened`** — did the agent open a skill at all? Comparable across harnesses only because each
adapter declares the folder *it* searches, and we inject into all of them.

**`skill_depth`** — how deeply. This is the more honest measure:

| value | meaning |
|---|---|
| `used` | read past `SKILL.md` into the detail files it links to |
| `noticed` | opened `SKILL.md` only — skimmed, not used |
| `not_opened` | available and untouched |
| `null` | this agent can't tell us |

Why: SkillsBench's audit caught codex reading three `SKILL.md` front pages, never opening a single
linked file, then writing code that ignored the advice — reward 0.45. A yes/no "opened a skill" scores
that as a success. It isn't one.

**What depth does *not* measure:** whether the agent then followed the advice. That needs judging the
work itself — a separate step ([`core/skill_depth.py`](src/adarubric/core/skill_depth.py) says so in
its own docstring).

### Measuring what a skill is worth

The number that answers "is this skill any good?" isn't one run — it's the gap between two:

```bash
uv run adarubric eval <task> --harness codex --sandbox docker                      # with
uv run adarubric eval <task> --harness codex --inject-skills no-skill --sandbox docker   # without
```

Both runs record which skills existed and whether they were injected (`skills_injected`), so the pair
stays comparable afterwards. That difference is the point of the whole exercise.


---

## Together AI harnesses (`claude-code-together`, `codex-together`) — Beta, UNVERIFIED

One `TOGETHER_API_KEY` runs open models (Kimi, GLM, Qwen, DeepSeek, …) through the SAME
claude-code and codex harnesses, via Together's own [TogetherLink](https://togetherlink.vercel.app)
wrappers: `tclaude` and `tcodex` run the real CLIs through a local translation proxy pointed at
Together's API. Because they wrap the same binaries, the output parsers are unchanged — the
harnesses are subclasses that swap only the command, the key, and the installer.

Why it matters for the research: the same open model through two different harnesses isolates the
harness/skill-discovery variable — cross-vendor runs (each CLI on its own vendor's model) never can.

```bash
uv run adarubric eval <task> --harness claude-code-together     --model <together-model-id> --sandbox docker   # TOGETHER_API_KEY in .env
```

Caveats (until a live run proves them out):
- Both TogetherLink integrations are marked **Beta**; tool-calling through translation proxies is
  where bugs live, and `skill_opened`/`skill_depth` depend on tool calls surviving translation.
- **Pin `--model` to a Together model id** — the CLIs' vendor defaults don't exist on Together.
- Cost fields will be wrong or empty (pricing tables don't know Together models) — report tokens.
- The wrappers prompt for the key on first launch; the installer only verifies `command -v` so a
  docker build can't hang on the prompt. The key itself is injected per run as usual.
- Gemini CLI has no Together route (it can't speak to non-Google backends) — not supported.
- The judge side needs no wrapper at all: `TOGETHER_API_KEY` is a first-class judge provider
  (OpenAI-compatible, picked last after gemini/anthropic/openai, or pin `provider: together`).
