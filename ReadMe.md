# AdaRubric — Skill Eval

A Python harness that measures **whether a coding agent reads the instructions you give it, and
whether that changes its answer.**

You hand it a skill (a folder with a `SKILL.md` guide inside) and a task. It runs a real coding agent
— Claude Code, Gemini CLI, Codex, or anything speaking ACP — inside an isolated container, records
what the agent did, and scores the result with the task's own checks.

It takes two kinds of input:

- **your own skill** — a folder with `SKILL.md` plus an instruction, or
- a **[SkillsBench](https://github.com/benchflow-ai/skillsbench)** task package (`task.md` +
  `environment/` + `verifier/` + `oracle/`).

---

## Getting started (from a fresh clone)

```bash
git clone <this-repo> AdaRubric-Skill-Eval
cd AdaRubric-Skill-Eval

# 1. install (uv — https://docs.astral.sh/uv/)
uv venv
uv pip install -e ".[dev]"

# 2. check it works — no API key, no Docker, no dataset needed
uv run adarubric --help
uv run pytest                # all pass, 1 skipped (the Docker test is opt-in)
```

That `pytest` run is your fastest confidence check. The suite uses fake agents and a mock ACP agent,
so it costs nothing and needs no keys.

**To run a real task** you need an API key in a `.env` file (never committed):

```
ANTHROPIC_API_KEY=sk-ant-...      # claude-code
OPENAI_API_KEY=sk-...             # codex
GEMINI_API_KEY=...                # gemini-cli
```

Only the key belonging to the harness you run is injected into the container.

**Not in the repo** (you fetch or create these): `dataset/` (clone SkillsBench, see §2), `.env`,
`output/` (made on first run). The Docker integration test is skipped unless you set
`ADARUBRIC_DOCKER_TESTS=1`.

---

## Check a task before you spend money on it

```bash
uv run adarubric check dataset/skillsbench/tasks/invoice-fraud-detection
```

Every SkillsBench task ships `oracle/solve.sh` — a worked solution its author wrote. This runs it and
grades it with the task's real grader. A healthy task scores **1.00**.

**This is free** — no model, no API key, no tokens. Just a shell script.

If it scores anything less, the **task** is broken (bad grader, missing dependency, mangled line
endings) and any agent score you collect from it means nothing. Run this on any task that's new to
you, and any time every agent mysteriously scores zero.

> Worth doing. We lost real money on agent runs that all scored 0.0 before discovering the cause was
> Windows line endings breaking the grader script. One free check would have said so immediately.
> When a run ends with every trial at zero, the CLI now points you here.

---

## The ways to run

> **Your own skill?** You give AdaRubric a folder; it writes the record of what ran into
> `eval.yaml`. **You never write `eval.yaml`** — it's the receipt, not the recipe.

### 1. Your own task

A task is always the same three things: **an instruction** (what to do), **a skill** (the help), and
**a grader** (how to score it). You write one file — `adarubric.yaml` — and lay the folder out one of
two ways. The only question that picks between them: *does the agent start with files, or an empty
folder?*

**Layout A — no starting files.** The whole folder is the skill; `SKILL.md` sits at the top:

```
polite-emails/
  adarubric.yaml   # instruction + grader
  SKILL.md         # the skill
  examples.md      # more skill pages, if any
```

**Layout B — the task ships files** (broken code to fix, a CSV to clean). Now the skill needs its
own box, or those files would be copied to the agent *inside the skill*:

```
fix-logging/
  adarubric.yaml           # instruction + file list + grader
  fixtures/orders.py       # what the agent must fix
  skills/
    house-logging/         # the box IS the skill; its name is the skill's name
      SKILL.md
      references/naming.md
```

(`fixtures/` and `references/` are just names — call them anything. `skills/`, `SKILL.md`, and
`adarubric.yaml` are fixed names.)

> ⚠️ **Pick one — don't mix.** A root `SKILL.md` means the **whole folder is the skill**, and all of
> it is copied to the agent (only `adarubric.yaml` / `TASK.md` / `grader.yaml` are removed). Put a
> `fixtures/` or a `check.py` next to a root `SKILL.md` and the agent receives them *inside the
> skill* — worst case, the very script your grader runs. The moment the folder holds anything that
> isn't the skill, switch to Layout B.

The same `adarubric.yaml` works for both — Layout A simply has no `workspace:` list:

```yaml
instruction: |
  orders.py is full of leftover print statements. Clean it up.
workspace:                          # Layout B only: files to place in front of the agent
  - fixtures/orders.py:orders.py    # left = here, right = where the agent sees it
timeout: 300
graders:
  - type: deterministic
    run: python check.py            # prints {"score": 0..1}, or "REWARD SCORE: x",
    weight: 1.0                     # or just exits 0 (pass) / 1 (fail)
```

```bash
uv run adarubric run ./fix-logging --harness claude-code --env-file .env
```

**What the agent actually receives** (both layouts end the same way):

```
its workspace/
  orders.py                          ← from workspace:, the job on the desk
  .claude/skills/house-logging/      ← the skill, in the agent's skills drawer
```

Two copies, two doors: `workspace:` files land on the desk, the skill lands in the drawer of
whichever agent is running (`.claude/skills/` for claude-code, `.agents/skills/` for codex, …).
`adarubric.yaml` itself is **never copied in** — the agent can't read its own marking scheme.

Worked, runnable versions of both are in this repo:

| | layout | run it |
|---|---|---|
| [`examples/release-notes/`](examples/release-notes/) | A — empty desk, writing task | `uv run adarubric run examples/release-notes --harness claude-code --env-file .env` |
| [`examples/fix-logging/`](examples/fix-logging/) | B — ships broken code | `uv run adarubric run examples/fix-logging --harness claude-code --env-file .env` |

Each example's README says what score to expect from an agent that follows the skill, one that
skims it, and one that never sees it — so you know what a number means before spending anything.

<details>
<summary>Shortcuts and extras (optional)</summary>

- **`TASK.md` + `grader.yaml`** — instead of `adarubric.yaml` you can put the instruction in a
  `TASK.md` and the checks in a `grader.yaml` (that's what `examples/release-notes/` does). Same
  meaning, no `workspace:` support. If `adarubric.yaml` exists, it wins and these are ignored.
- **No grader at all** — leave `graders:` out and the run still works: you get turns, tool calls,
  cost, and whether the skill was opened. Just no score.
- **`--instruction "..."`** on the command line overrides the file.
- **Skill somewhere unusual?** Point the run path straight at its `SKILL.md`, or add
  `skill: path/to/skill` in `adarubric.yaml`.
- **More than one skill** — put several boxes under `skills/`; all are injected. That's how you
  test whether the agent picks the right one.
- **Docker** — add `docker: {base: ..., setup: ...}` and run with `--sandbox docker`.
- Like the stripped `adarubric.yaml`, `TASK.md` / `grader.yaml` / `eval.yaml` are also removed from
  the skill before it's copied to the agent.

</details>

### 2. A SkillsBench task (needs Docker)

**Get the dataset first.** It's a separate, large repo, not included here:

```bash
git clone https://github.com/benchflow-ai/skillsbench dataset/skillsbench
```

⚠️ **On Windows, check your line endings.** If `git config core.autocrlf` is `true`, the clone
rewrites every grader script to Windows line endings, and Linux inside the container can't run them —
**every task silently scores 0**. AdaRubric now cleans them on the way into the container, so runs
work regardless, but the dataset itself is easier to work with fixed:

```bash
git -C dataset/skillsbench config core.autocrlf false
```

Then:

```bash
uv run adarubric run dataset/skillsbench/tasks/invoice-fraud-detection \
    --harness claude-code --dataset skillbench --sandbox docker --env-file .env
```

These tasks hardcode `/app`, `/verifier`, `/logs`, so Docker isn't optional — the task's own
`environment/Dockerfile` is built and its `verifier/` does the scoring.

### 3. Several harnesses, models, repeats

```bash
# same task on three agents — each gets its own output folder
uv run adarubric run <task> --harness claude-code,codex,gemini-cli --sandbox docker --env-file .env

# a different model per agent
uv run adarubric run <task> \
    --harness claude-code:claude-opus-4-8,codex:gpt-5.6-luna,gemini-cli:gemini-2.5-pro \
    --sandbox docker --env-file .env

# one model for all, run 3 times (agents are non-deterministic)
uv run adarubric run <task> --harness claude-code,codex --model claude-opus-4-8 --trials 3 --env-file .env
```

### 4. Does the skill actually help? Run it without one

```bash
uv run adarubric run <task> --harness codex --inject-skills no --sandbox docker --env-file .env
```

Same task, guidance withheld. Compare its reward against a normal run — **that difference is what a
skill is worth**, and it's the question the benchmark exists to ask.

The run still records *which* skills were withheld (`skills: [...]` plus `skills_injected: false`),
so a control run can never be mistaken for a task that simply has no skills.

### Files you write vs files we generate

| File | Written by | In/out | Holds |
|------|-----------|--------|-------|
| `grader.yaml` | **you** (optional) | input | just the checks (commands + weights) |
| `adarubric.yaml` | **you** (optional) | input | everything: instruction, input files, docker, checks, timeout. Wins over the convention files. |
| `eval.yaml` | **AdaRubric** | **output** | the receipt of a run: harness, model asked for + model actually used, skills, grading pointers |

---

## CLI reference

### `adarubric run`

| Flag | Default | Meaning |
|------|---------|---------|
| `<path>` | — | a skill folder or a SkillsBench task |
| `--harness` | *(required)* | `claude-code` \| `gemini-cli` \| `codex` \| `acp` \| `oracle`. Comma-separate for several. Pin a model with `name:model`. No auto-detection. |
| `--sandbox` | `local` | `local` (temp dir on your machine) or `docker` (container; required for SkillsBench) |
| `--model` | *(agent's own)* | one model for every harness; `name:model` in `--harness` overrides it |
| `--inject-skills` | `yes` | `no` withholds the skills — the control half of "did the skill help?". Takes yes/no, true/false, 1/0, on/off. |
| `--dataset` | `auto` | `auto` detects the shape; `skillbench` / `generic` force or validate it |
| `--instruction` | — | overrides `TASK.md` / `task.md` / the config |
| `--task` | first | pick one task from a multi-task `adarubric.yaml` |
| `--output` | `output` | results go to `<output>/<harness>/<task>/attempt-N/` |
| `--trials` | `1` | repeats inside this launch |
| `--timeout` | config/300 | seconds allowed per agent run |
| `--grade / --no-grade` | `--grade` | run the checks after the agent finishes |
| `--env-file` | — | load `KEY=VALUE` API keys from a file |

**ACP-only flags** (see [`coding_agent_harness.md`](coding_agent_harness.md)):

| Flag | Meaning |
|------|---------|
| `--acp-cmd` | the launch command, e.g. `'gemini --acp'`. Required for `--harness acp`. |
| `--acp-skill-dir` | where the wrapped agent looks for skills, e.g. `.claude/skills`. **Get this wrong and it will never find the skill.** |
| `--acp-env-key` | env var(s) the agent needs, so they're injected and checked up front |
| `--acp-install` | for Docker: a **harness name** to reuse its installer (e.g. `gemini-cli`), or a shell snippet |
| `--acp-name` | the label this run is filed under (default: derived, e.g. `acp-gemini`) |

### `adarubric check`

| Flag | Default | Meaning |
|------|---------|---------|
| `<path>` | — | a SkillsBench task (needs `oracle/solve.sh`) |
| `--sandbox` | `docker` | where to run it |
| `--output` | `output` | where the check's own run lands |
| `--timeout` | config | seconds allowed |

Exits non-zero and says why if the task can't be passed even with the right answer.

---

## How it works

**Ports & Adapters** — dependencies only point inward, so a new harness, sandbox or grader is one
small file plus one registry line.

```
 CLI (cli.py)              parse flags → build objects from registries → call the runner
        │
 ORCHESTRATION (runner.py) EvalRunner, which only ever touches the contracts
        │
 ADAPTERS (harnesses/ sandboxes/ grading/)   the concrete plug-ins
        │
 CORE (core/)              models.py (plain data)  ·  contracts.py (Harness·Sandbox·Grader·LLM)
```

One run, start to finish:

```
load_spec(path)  ──►  EvalSpec (normalized: your skill OR a SkillsBench task)
      │
preflight the sandbox            ← Docker not running? stop here, write nothing
      │
EvalRunner.run(harness, spec, trials):
   make output/<harness>/<task>/attempt-N/  →  write eval.yaml (never enters the container)
   per trial:
     prepare   build the task image, then a thin layer adding the agent's CLI
     setup     fresh container → copy input files → write the prompt →
               copy each skill into the folder THAT agent looks in
     snapshot files  →  the agent runs  →  snapshot again, diff
     copy the agent's files out
     GRADE     only now: the grader is copied in AFTER the agent is gone
     write run.json / grading.json / transcript.json / changes.json / prompt.md / raw.log
     destroy the container
```

**The agent can never see the answers.** `verifier/`, `oracle/` and `eval.yaml` are never in the
container while the agent is alive. The grader arrives afterwards; the container is then destroyed.
A test asserts a normal agent run stages nothing at all.

**Line endings are cleaned** when the grader is copied in, so a dataset cloned on Windows can't
silently break scoring.

---

## Telling *our* problems apart from *the agent's*

The single most important thing this harness gets right: a score of 0 must mean the model got it
wrong, not that your laptop misbehaved.

| What happened | What you get |
|---|---|
| Docker isn't running | one clear line, exit 1, **nothing written** — no phantom failed run |
| Docker dies mid-run | run aborts, the half-written folder is erased, the attempt number isn't used up |
| the grader script crashes | `graded: false` + **"grading failed"**, never `reward: 0` |
| the grader ran and the answer was wrong | a real `reward` — that's a genuine result |
| copying the agent's files out fails | run still counts and is still scored; a note says the local copy is missing |
| the task's own Dockerfile is broken | a real failed trial — that genuinely is the task's fault |

`reward: 0.0` from AdaRubric now always means *the answer was checked and scored zero*.

---

## What a run leaves behind

Filed by **harness → task → attempt → trial**. An *attempt* is one launch; a *trial* is one repeat
inside it.

```
output/
  claude-code/
    invoice-fraud-detection/
      attempt-1/
        eval.yaml               # what was run (host-only; never in the container)
        trial-1/
          run.json              # every metric
          grading.json          # reward + what each check said
          transcript.json       # ordered event log, secrets redacted
          changes.json          # files created / modified / deleted
          raw.log               # the agent's raw output (for ACP: the full protocol transcript)
          prompt.md             # the exact instruction it was given
          workspace/            # the agent's final files, copied out
        trial-2/ ...
      attempt-2/ ...
  status.json                   # live progress, read by the dashboard
```

ACP runs are labelled by the agent they wrap — `output/acp-gemini/`,
`output/acp-claude-code-acp/` — so different agents don't pile into one folder.

### The metrics that matter

**`skill_opened`** — did the agent actually open a skill? `true` / `false` / `null`. `null` means the
agent doesn't tell us enough to know; it is never guessed as `false`.

**`skill_depth`** — *how* it used it. This is the interesting one:

| value | meaning |
|---|---|
| `used` | read past `SKILL.md` into the detail files it links to |
| `noticed` | opened `SKILL.md` only — **skimmed, not used** |
| `not_opened` | skills were there and untouched |
| `null` | this agent can't tell us |

Why it exists: SkillsBench's own audit caught codex reading three `SKILL.md` front pages, never
opening a single linked file, then writing code that ignored the advice — scored 0.45. A yes/no
"opened a skill" calls that a success. `noticed` and `used` are different findings.

**`num_turns`** — how many times the model replied. **One definition for every harness**, because
each CLI means something different by "turn": codex's own counter tracks prompt cycles (always 1 for
us) while claude counts model replies. Comparing those directly was meaningless.

**Cost** — `cost_usd` when the agent reports its real spend (claude does; ACP agents do via the
protocol), `estimated_cost_usd` from tokens × the price table otherwise.

⚠️ Prices in [`core/pricing.py`](src/adarubric/core/pricing.py) are a **cached snapshot, not a live
lookup** — verify before quoting them. Cached input (billed at ~10%) isn't modelled, so a
cache-heavy run reads high. Reported cost always wins over estimated.

**Model** — `model` is what the agent said it ran; `model_requested` is what you pinned. Kept apart
so a pin that silently didn't take effect is visible. Some agents report a *routing mode* rather than
a model (gemini says `auto`, claude-code-acp says `Default (recommended)`) — recorded as-is, but not
priced, since there's no price for a mode.

---

## Harnesses

Claude Code, Gemini CLI and Codex are built in. `--harness acp` drives **any**
[ACP](https://agentclientprotocol.com/) agent with no per-agent code — including in Docker.

```bash
uv run adarubric run <task> --harness acp --acp-cmd 'gemini --acp' \
    --acp-skill-dir '.gemini/skills' --acp-install gemini-cli \
    --dataset skillbench --sandbox docker --env-file .env
```

Full guide, including adding your own: **[`coding_agent_harness.md`](coding_agent_harness.md)**.

### What's actually been run

Honest status — "verified" means a real end-to-end run, not just tests:

| Harness | Verified | Notes |
|---|---|---|
| `claude-code` | ✅ Docker | reports real cost; strongest skill signal (names the `Skill` tool) |
| `codex` | ✅ Docker | reports **no** model and no cost — cost is estimated from the pinned model |
| `gemini-cli` | ✅ Docker | `skill_opened` measured from its tool tally; no cost reported |
| `acp` + gemini | ⚠️ partial | reached the agent and ran; needs one clean end-to-end confirmation |
| `acp` + claude | ⚠️ partial | completed and scored; skill detection not yet confirmed against real traffic |
| `acp` + codex | ❌ not yet | |
| `oracle` | ✅ Docker | used by `adarubric check` |

The ACP client is built from the spec and tested against a mock agent. Real agents have already
surfaced three things a mock can't (a rejected working directory, text split mid-word, tool IDs
mistaken for tool names) — expect the occasional rough edge on a new agent, and check `raw.log`,
which holds the full protocol transcript.

---

## Live dashboard

One command, one page, updating as the run happens.

```bash
# terminal 1 — run
uv run adarubric run <task> --harness gemini-cli --dataset skillbench --sandbox docker --env-file .env

# terminal 2 — watch (opens http://127.0.0.1:8765)
python dashboard/serve.py --output output
```

Every run writes `output/status.json` as it goes; the server rescans `output/` on each request and the
page polls every ~2s, re-rendering **in place** — your scroll position and current page are kept.

**Restart the server after upgrading** — a server left running from before serves the old page.

Shows: runs, mean reward, **skill noticed vs actually used**, total cost and tokens; per-harness
charts (reward, turns-to-answer, cost, tokens); and a runs table with turns, tokens, cost, time, file
changes and **when it ran** (your local time, newest first, running jobs pinned on top).

**Click any run** for its own page: every metric, the created/modified/deleted file lists, which skill
files were read, a live "building / copying to docker / staging" timeline, and the log tail. Click a
**task name** to see every run of that task side by side.

- [`dashboard/serve.py`](dashboard/serve.py) — the server. Stdlib only, localhost by default
  (`--host` / `--port` / `--no-open`).
- [`dashboard/dashboard.html`](dashboard/dashboard.html) — the page. No dependencies, no dummy data,
  light and dark.

---

## Repository layout

```
AdaRubric-Skill-Eval/
  ReadMe.md
  coding_agent_harness.md        the harnesses + how to wire your own (incl. ACP)
  dashboard/
    serve.py                     live dashboard server
    dashboard.html               the page
  pyproject.toml                 console script `adarubric`
  src/adarubric/
    cli.py                       Typer app: `run` and `check`
    runner.py                    EvalRunner — orchestration
    loading.py                   path → EvalSpec
    core/
      models.py                  plain dataclasses
      contracts.py               Harness · Sandbox · Grader · LLM
      errors.py                  SandboxUnavailable — our infra failing, not the agent
      pricing.py                 token → cost (editable table)
      skill_depth.py             noticed vs used
    harnesses/                   claude.py codex.py gemini.py acp.py oracle.py + registry.py
    sandboxes/                   local.py docker.py staging.py (line-ending cleanup) + registry.py
    grading/                     deterministic.py + registry
    reporting/                   terminal.py (console) · status.py (status.json for the dashboard)
  tests/                         run with `uv run pytest` — no keys, no Docker
  dataset/                       cloned SkillsBench tasks (gitignored, large)
```

---

## Known rough edges

Straight, so you're not surprised:

- **One image per task per agent.** 90 tasks × 3 agents = 270 images, ~1.5–1.9 GB each, and the agent
  is re-downloaded for every task. Docker can't share the layer because each task has its own base.
  Clean up as you go, or the disk fills.
- **Prices are a cached snapshot** and don't model cached-input discounts.
- **`skill_depth` measures which files were reached**, not whether the advice was followed. Judging
  that needs a look at the work itself.
- **Gemini can't report depth at all** — it gives tool counts with no file paths, so it maxes out at
  `noticed`.
- **ACP tokens vary by agent.** The protocol defines `usage` and a cost notification, and
  claude-acp/codex-acp follow it; gemini-cli reports tokens off-spec and no cost
  ([gemini-cli#24280](https://github.com/google-gemini/gemini-cli/issues/24280)).
- **Activity feeds are per-invocation.** `status.json` is rewritten by each run, so an earlier run's
  build/copy timeline is gone once you start another.

---

## Status

| Phase | Status |
|-------|--------|
| **1 — Running** | 🟢 two pipelines, local + Docker, four harnesses + oracle, model pinning, skills on/off |
| **2 — Deterministic grading** | 🟢 SkillsBench verifier + your own checks, isolation-guarded, "grading failed" separated from a real zero |
| **5 — Task validation** | 🟢 `adarubric check` runs the reference solution before you spend anything |
| 3 — LLM-rubric grading | ⚪ planned |
| 4 — Aggregation (pass@k) | ⚪ planned |
| 6 — Reporting · 7 — Init | ⚪ planned |

---

## Security

Never commit API keys. Pass them with `--env-file` only. We inject just the one key that harness
declares, redact secrets from logs and transcripts, and strip credential files (e.g.
`.codex/auth.json`) from exported workspaces.

**If a key has ever been pasted into a chat, a commit, or a screenshot, rotate it.** Treat it as
public from that moment.
