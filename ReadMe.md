# AdaRubric — Skill Eval

A Python harness for evaluating how coding agents (**Claude Code, Gemini CLI, Codex, …**) discover
and use **Agent Skills**. It runs a skill on a chosen harness inside an isolated sandbox (local or
Docker), records rich metrics — including the paper-critical **"did the agent actually open the
skill?"** signal — and then grades the result with deterministic checks.

It accepts **two input shapes**:
- a plain **skill folder** (a `SKILL.md` + resources) plus an instruction, and
- a **[SkillsBench](https://github.com/benchflow-ai/skillsbench)** task package (`task.md` +
  `environment/` + `verifier/` + `oracle/`).

---

## Install

```bash
uv venv
uv pip install -e ".[dev]"
uv run adarubric --help
uv run pytest
```

Provide the harness's API key via a `.env` file (never committed) and `--env-file`:

```
# .env  — only the key for the harness you run is injected into the sandbox
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=...
```

---

## Quickstart — the ways to run

> **Generic mode = your own skill.** You give AdaRubric a folder; it assembles the run and
> **generates** the manifest `eval.yaml` into the output. `eval.yaml` is *never* something you
> write — it's the record of what ran.

### 1. The convention folder (recommended for your own skills)

Put your skill's `SKILL.md` in a folder, and — at the folder root — optionally add a **`TASK.md`**
(the instruction given to the agent) and a **`grader.yaml`** (the deterministic checks). Then point
at the folder:

```
my-skill/
  SKILL.md         # the skill under test (required)
  TASK.md          # the instruction (optional; or pass --instruction)
  grader.yaml      # deterministic checks (optional; omit for an ungraded run)
```

```yaml
# grader.yaml — reward = weighted pass fraction of these checks
graders:
  - run: pytest -q                 # a shell check; exit 0 (or "REWARD SCORE: x") → score
    weight: 1.0
  - run: test -f report.txt
    weight: 0.5
```

```bash
uv run adarubric run ./my-skill --harness claude-code --dataset generic --env-file .env
# (TASK.md supplies the instruction; --instruction overrides it)
```

`TASK.md` and `grader.yaml` are **control files** — they are stripped out before the skill is
injected, so the agent never sees the task's grading.

### 2. Power-user single file (`adarubric.yaml`)

When you need a Docker recipe, workspace input files, or several tasks in one file, drop an
**`adarubric.yaml`** next to `SKILL.md` instead (it wins over the convention files):

```yaml
# adarubric.yaml (all-in-one)
name: my-task
instruction: |
  Solve the task described in the workspace.
workspace:
  - fixtures/input.json           # copied into the workspace (src, or src:dest)
docker:
  base: python:3.12-slim          # synthesized image: FROM base + setup + harness overlay
  setup:
    - pip install pandas
timeout: 600
graders:
  - type: deterministic           # runs AFTER the agent; reads a 0..1 score from stdout
    run: pytest -q && echo "REWARD SCORE: 1.0"
    weight: 1.0
```

```bash
uv run adarubric run ./my-task --harness claude-code --dataset generic --sandbox docker --env-file .env
```

### 3. A SkillsBench benchmark task (skillbench mode, Docker)

**Get the dataset first.** SkillsBench is a separate, large repo — it's **not vendored here** (it's
gitignored under `dataset/`). Clone it yourself:

```bash
git clone https://github.com/benchflow-ai/skillsbench dataset/skillsbench
# tasks then live at dataset/skillsbench/tasks/<task-id>/
```

- **Source:** [github.com/benchflow-ai/skillsbench](https://github.com/benchflow-ai/skillsbench)
  (~87 task packages across 8 domains; paper: arXiv 2602.12670).
- **Layout:** each task is `tasks/<id>/{task.md, environment/{Dockerfile,skills/}, verifier/, oracle/}`.

SkillsBench tasks are Docker-native (they hardcode `/app`, `/verifier`, `/logs`), so run them faithfully
in Docker — the task's own `environment/Dockerfile` is built and the `verifier/` scores the result:

```bash
uv run adarubric run dataset/skillsbench/tasks/dialogue-parser \
    --harness claude-code --dataset skillbench --sandbox docker --env-file .env
```

### Run several harnesses / pick a model per harness / repeat

```bash
# same task on three harnesses (a matrix run) — each lands in its own output folder
uv run adarubric run <task> --harness claude-code,codex,gemini-cli --sandbox docker --env-file .env

# pin a DIFFERENT model per harness with name:model
uv run adarubric run <task> \
    --harness claude-code:claude-opus-4-8,codex:gpt-5-codex,gemini-cli:gemini-2.5-pro \
    --sandbox docker --env-file .env

# one default model for every harness (--model), repeated 3 times ("trials")
uv run adarubric run <task> --harness claude-code,codex --model claude-opus-4-8 --trials 3 --env-file .env
```

### Files you write vs files AdaRubric generates

The three `*.yaml` names are easy to confuse — here's the whole truth:

| File | Written by | In / out | What it holds |
|------|-----------|----------|---------------|
| `grader.yaml` | **you** (optional) | input | *Only* the deterministic checks (`graders:` — commands + weights). Convention mode. |
| `adarubric.yaml` | **you** (optional) | input | The all-in-one config: instruction + workspace + docker + graders + timeout (+ multi-task). Wins over the convention files. |
| `eval.yaml` | **AdaRubric** (always) | **output** | The generated manifest/receipt of a run: harness, model (requested + observed), env, skills, grading pointers. You never author it. |

`grader.yaml` and `adarubric.yaml` are **inputs you author**; `eval.yaml` is the **record we
produce**. All input control files (`grader.yaml`, `adarubric.yaml`, `TASK.md`) are stripped before
the skill is injected, so the agent never sees the task's grading.

---

## CLI reference (`adarubric run`)

| Flag | Default | Meaning |
|------|---------|---------|
| `<path>` | — | A skill folder or a SkillsBench task package. |
| `--harness` | *(required)* | `claude-code` \| `gemini-cli` \| `codex` \| `acp` (generic ACP wrapper, needs `--acp-cmd`), comma-separated for a matrix run. Explicit — **no key auto-detection**. Pin a model per harness with `name:model` (e.g. `claude-code:claude-opus-4-8,codex:gpt-5-codex`). |
| `--acp-cmd` / `--acp-skill-dir` / `--acp-env-key` | — | For `--harness acp`: the agent launch command (e.g. `'gemini --acp'`), the wrapped agent's skill dir, and its required env var(s). `--sandbox local` only for now. |
| `--sandbox` | `local` | `local` (OS temp dir) or `docker` (isolated container; required for faithful SkillsBench runs). |
| `--model` | *(CLI default)* | Default model for **all** harnesses (e.g. `claude-opus-4-8`). Overridden per harness by `name:model` in `--harness`. Recorded in `eval.yaml`. |
| `--dataset` | `auto` | `auto` detects the shape; `skillbench` / `generic` validate or force a pipeline. |
| `--instruction` | — | Overrides `TASK.md` / `task.md` / the config; required if none of them supply one. |
| `--task` | first | Pick a task from a multi-task `adarubric.yaml`. |
| `--output` | `output` | Output root: results land in `<output>/<harness>/<task>/attempt-N/`. |
| `--trials` | `1` | Repetitions inside this launch (agents are non-deterministic). |
| `--timeout` | config/300 | Per-harness timeout in seconds. |
| `--grade / --no-grade` | `--grade` | Run deterministic graders after the agent (verifier / config graders). |
| `--env-file` | — | Load `KEY=VALUE` env vars (API keys) from a file; only the harness's declared key is injected. |

---

## How it works

Clean **Ports & Adapters** design — dependencies only ever point inward, so adding a harness, sandbox,
or grader is one small file + one registry line, never a rewrite.

```
 CLI (cli.py — Typer)            parses flags → builds objects via registries → calls the runner
        │
 ORCHESTRATION (runner.py)       EvalRunner drives a run using ONLY the contracts
        │
 ADAPTERS (harnesses/ sandboxes/ grading/)   concrete plug-ins implementing the contracts
        │
 CORE (core/)                    models.py (pure dataclasses)  ·  contracts.py (Harness·Sandbox·Grader·LLM)
```

A single run, end to end:

```
load_spec(path)  ──►  EvalSpec (normalized: skill folder OR SkillsBench task)
      │
EvalRunner.run(harness, spec, trials):
   create output/<harness>/<task>/attempt-N/   →  write eval.yaml manifest (host-only)
   for each trial:
     prepare  (docker build / no-op)     →  setup (temp ws; copy files; write .adarubric/prompt.md;
                                             inject each skill into the harness's real skill dir)
     snapshot files  →  harness.run(instruction, ws, run_command)  →  snapshot + diff
     export workspace  →  GRADE (only now — the agent is gone; verifier staged AFTER export)
     write run.json / transcript.json / changes.json / grading.json / prompt.md / raw.log
     cleanup
```

**Isolation is guaranteed:** the `verifier/`, `oracle/`, and the `eval.yaml` manifest are **never**
placed in the agent's workspace — the grader is staged and run only *after* the agent finishes and its
output has been captured (regression-tested with a "snoop" harness).

**Prompt delivery:** the instruction is written to `.adarubric/prompt.md`; every harness reads it via
stdin redirection (`cli … < .adarubric/prompt.md`) — cross-platform, container-safe, no shell escaping.

---

## Repository layout

```
AdaRubric-Skill-Eval/
  ReadMe.md
  coding_agent_harness.md        how the harnesses work + how to add your own (incl. ACP)  ← start here for harnesses
  dashboard/                     the live run-tracker UI (outside the package)
    serve.py                     `python dashboard/serve.py` → live dashboard on http://localhost:8765
    dashboard.html               the page (charts/logs/cost, per-task/run pages); polls /api/data
  pyproject.toml                 Typer + uv; console script `adarubric`
  src/adarubric/
    cli.py                       Typer app (thin edge — parse flags, delegate)
    runner.py                    EvalRunner — orchestration
    loading.py                   path → EvalSpec (auto-detects skillbench vs generic)
    core/
      models.py                  pure dataclasses (RunOutput, EvalSpec, Trial, RunMeta, …)
      contracts.py               Harness · Sandbox · Grader · LLM (abstract bases)
      pricing.py                 token → cost estimation (editable table)
    harnesses/                   claude.py  codex.py  gemini.py  + registry.py  (name → Harness)
    sandboxes/                   local.py  docker.py               + registry.py  (name → Sandbox)
    grading/                     deterministic.py                  + registry    (name → Grader)
    reporting/                   terminal.py (live console progress)
  tests/                         smoke + unit + docker-integration (gated by ADARUBRIC_DOCKER_TESTS=1)
  dataset/                       cloned SkillsBench tasks (gitignored — large)
```

> Internal port notes live in `converting/` and are **not tracked** (gitignored). The public,
> maintained docs are this README and `coding_agent_harness.md`.

---

## Output layout — what a run leaves behind

Keyed by **harness → task → attempt → trial**, so comparing harnesses on the same task is trivial.
An **attempt** is one launch of the command; a **trial** is one repetition inside it.

```
output/
  claude-code/
    dialogue-parser/
      attempt-1/
        eval.yaml               # the run definition + which harness/MODEL was used (host-only manifest)
        trial-1/
          run.json              # ALL metrics for the trial (RunMeta)
          grading.json          # reward + per-grader results
          transcript.json       # ordered, structured, secret-redacted event log
          changes.json          # created / modified / deleted (snapshot-diff)
          raw.log               # full raw harness stdout/stderr (redacted)
          prompt.md             # the exact instruction given
          workspace/            # the agent's FINAL, changed files (exported from the sandbox)
        trial-2/ ...
      attempt-2/ ...
```

> Keep a single `output/` — the `.gitignore` matches `output*/`, so it (and any stray `output2/`,
> `output3/` from ad-hoc runs) stay out of git.

### `eval.yaml` (the generated manifest)

**AdaRubric generates this — you never write it** (your input is `adarubric.yaml` or the convention
files). It's written to the attempt folder **before** any trial runs (host-only, never enters the
sandbox) and records the task, mode, instruction, the **harness** (`id`, `cli`, requested `model`,
`skill_dirs`, and env-var **names** — never values), the environment (sandbox, Dockerfile /
base+setup), the injected skills, and grading pointers. After the trials finish, the actually-observed
model(s) are added as `harness.model_observed` — so you can confirm which model really ran (useful
when you let the CLI pick its own default).

### `run.json` (metrics)

One file, every metric; any field a harness can't report is `null` (never a misleading `false`):
identity/reproducibility (`harness`, `sandbox`, `model`, `platform`, timestamps), outcome
(`success`, `timed_out`, `error`, `graded`, `reward`), **usage** (`input/output/total_tokens`,
`num_turns`, `num_tool_calls`, `cost_usd` reported + `estimated_cost_usd` computed + `cost_source`,
`tool_counts`), **timing** (`total/setup/run/export_ms`), **skill_usage** (`skill_opened`,
`skills_triggered`, `skill_files_read`), and the change summary.

**`skill_opened`** = the agent explicitly invoked the `Skill` tool **or** read a `.../skills/<name>/…`
file during the run — distinct from the harness auto-loading the skill *description* into context.
Its observability differs per harness (Claude Code: definitive; Codex: partial; Gemini: unknown) — see
[`coding_agent_harness.md`](coding_agent_harness.md).

---

## Harnesses

Claude Code, Gemini CLI, and Codex are built in. A **generic ACP harness** (`--harness acp`) runs
**any** [Agent Client Protocol](https://agentclientprotocol.com/) agent (Zed agents, `gemini --acp`,
and others) with no per-agent code — point it at a launch command:

```bash
uv run adarubric run <task> --harness acp --acp-cmd 'gemini --acp' \
    --acp-skill-dir '.gemini/skills' --sandbox local --env-file .env
```

(ACP is `--sandbox local` only for now; a Docker bridge is a follow-up.) To use the built-ins, add
your own adapter, or wire another ACP agent, see **[`coding_agent_harness.md`](coding_agent_harness.md)**.

**Verified so far:** claude-code (local + Docker) and codex (local) have real end-to-end runs.
**gemini-cli** was run once (Docker) which surfaced a headless folder-trust bug (exit 55) — now fixed
(`GEMINI_CLI_TRUST_WORKSPACE=true`); re-run to confirm a clean success. Its `skill_opened` is now
**measured** from `gemini -o json`'s complete tool tally (`stats.tools.byName` → `activate_skill`),
not a design gap. See the verification table in [`coding_agent_harness.md`](coding_agent_harness.md).

---

## Tracking UI

Two UIs, for two moments:

### 1. Live progress (during a run) — terminal

While a run happens, [src/adarubric/reporting/terminal.py](src/adarubric/reporting/terminal.py)
(`TerminalReporter`) prints each stage as it happens (the runner emits a `ProgressEvent` per stage):

```
> claude-code/dialogue-parser attempt 1
  > trial 1
      . preparing → setting_up → running → exporting → grading
  = trial 1: done  reward=1.00
```

The final per-trial summary (score, tokens, cost, time, file changes) is printed by
[cli.py](src/adarubric/cli.py). This stays in-package because the CLI imports it at runtime.

### 2. The dashboard — one live server on a port — [`dashboard/`](dashboard/)

One command, one live dashboard. Every `adarubric run` writes `output/status.json` as it happens
(stages + a "what the sandbox is doing" activity feed); the dashboard server scans `output/` on every
request and the page polls it, so you watch docker build, files copied to the container, the current
stage, accuracy, turns, cost, and each trial finishing **live**.

```bash
# terminal 1 — run
uv run adarubric run <task> --harness gemini-cli --dataset skillbench --sandbox docker --env-file .env

# terminal 2 — start the live dashboard (opens your browser at http://127.0.0.1:8765)
python dashboard/serve.py --output output
```

The page polls `/api/data` (a fresh scan of `output/`) every ~2s and re-renders **in place** — no
reload, no dummy data — keeping your scroll position and whatever task/run page you're on.

- **[dashboard/serve.py](dashboard/serve.py)** — the server: scans `output/` (handles both the
  `attempt-N/trial-T/` and older `attempt-N/` layouts) and serves the page + the live `/api/data`.
  Stdlib only, localhost-only by default (`--host`/`--port`/`--no-open` to change).
- **[dashboard/dashboard.html](dashboard/dashboard.html)** — the UI (inline CSS/JS, no dependencies,
  theme-aware); starts empty and fills in from `/api/data`.

What it shows: KPI strip (runs, mean reward, skill-opened rate, total cost, total tokens),
per-harness bar charts (mean reward, **mean turns-to-answer**, total cost, total tokens), and a
filterable runs table with a **Turns** column.

**Dedicated pages (click-through):** click a **run row** to open its own page — status, all metrics
(turns / tool calls / commands, tokens, cost, reward + skill-usage), the **created / modified /
deleted file lists**, a **"built / copied to docker / staged" activity timeline** for that run, and
the `raw.log` excerpt; if it's still running, a live stage strip. Click a **task name** to open the
task page listing every run/trial of that task (live + finished). Client-side routed via the URL hash.

> The live *reporter* stays under `src/adarubric/` (the runner imports it at runtime to write
> `status.json`); the *dashboard* that reads it is a standalone tool, so it lives outside the package.

---

## Status

Built piece by piece:

| Phase | Status |
|-------|--------|
| **1 — Scaffold + Running** | 🟢 Complete — two pipelines (skillbench/generic), local + Docker sandboxes, claude/codex/gemini adapters, `--model` pin, YAML config, live terminal progress. |
| **2 — Deterministic grading** | 🟢 Core built — SkillsBench verifier + config graders, isolation-guarded, weighted reward. |
| 3 — LLM-rubric grading | ⚪ Planned |
| 4 — Trials + aggregation (pass@k) | ⚪ Planned |
| 5 — Validation (oracle) · 6 — Reporting · 7 — Init | ⚪ Planned |

---

## Security note

Never commit API keys. Pass them only via `--env-file`; the runner injects **only** the chosen
harness's declared key into the sandbox, redacts secrets from logs/transcripts, and scrubs credential
files (e.g. `.codex/auth.json`) from exported workspaces. If a key has ever been committed to any
repo, rotate it.
```
