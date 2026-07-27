# AdaRubric — Skill Eval

A Python harness for evaluating how coding agents (**Claude Code, Gemini CLI, Codex, …**) discover
and use **Agent Skills**. It runs a skill on a chosen harness inside an isolated sandbox (local or
Docker), records rich metrics — including the paper-critical **"did the agent actually open the
skill?"** signal — and then grades the result with deterministic checks.

It accepts **two input shapes**:
- a plain **skill folder** (a `SKILL.md` + resources) plus an instruction, and
- a **[SkillsBench](https://github.com/benchflow-ai/skillsbench)** task package (`task.md` +
  `environment/` + `verifier/` + `oracle/`).

> This is a Python port of [skillgrade](https://github.com/mgechev/skillgrade), built piece by piece.

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

## Quickstart — the three ways to run

### 1. A plain skill folder (generic mode, local)

```bash
uv run adarubric run ./path/to/my-skill \
    --harness claude-code \
    --instruction "Refactor utils.py to remove duplication." \
    --env-file .env
```

### 2. Your own skill/task with a config file (generic mode)

Drop an `adarubric.yaml` (or a skillgrade-style `eval.yaml`) next to the skill to supply the
instruction, workspace files, and an optional Docker recipe — then just point at it:

```bash
uv run adarubric run ./my-task --harness claude-code --sandbox docker --env-file .env
```

```yaml
# adarubric.yaml (generic mode, all-in-one)
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

### 3. A SkillsBench benchmark task (skillbench mode, Docker)

SkillsBench tasks are Docker-native (they hardcode `/app`, `/verifier`, `/logs`), so run them faithfully
in Docker — the task's own `environment/Dockerfile` is built and the `verifier/` scores the result:

```bash
uv run adarubric run dataset/skillsbench/tasks/dialogue-parser \
    --harness claude-code --sandbox docker --env-file .env
```

### Run several harnesses / repetitions

```bash
# same task on three harnesses (a matrix run) — each lands in its own output folder
uv run adarubric run <task> --harness claude-code,codex,gemini-cli --sandbox docker --env-file .env

# 3 repetitions ("trials") inside one launch, pinning the model
uv run adarubric run <task> --harness claude-code --model claude-opus-4-8 --trials 3 --env-file .env
```

---

## CLI reference (`adarubric run`)

| Flag | Default | Meaning |
|------|---------|---------|
| `<path>` | — | A skill folder or a SkillsBench task package. |
| `--harness` | *(required)* | `claude-code` \| `gemini-cli` \| `codex`, comma-separated for a matrix run. Explicit — **no key auto-detection**. |
| `--sandbox` | `local` | `local` (OS temp dir) or `docker` (isolated container; required for faithful SkillsBench runs). |
| `--model` | *(CLI default)* | Pin the harness model (e.g. `claude-opus-4-8`, `gpt-5-codex`, `gemini-2.5-pro`). Recorded in `eval.yaml`. |
| `--dataset` | `auto` | `auto` detects the shape; `skillbench` / `generic` validate or force a pipeline. |
| `--instruction` | — | Overrides `task.md` / the config; required for a bare skill folder. |
| `--task` | first | Pick a task from a multi-task `eval.yaml`. |
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

### `eval.yaml` (the manifest)

Written to the attempt folder **before** any trial runs (host-only, never enters the sandbox). It
records the task, mode, instruction, the **harness** (`id`, `cli`, requested `model`, `skill_dirs`,
and env-var **names** — never values), the environment (sandbox, Dockerfile / base+setup), the injected
skills, and grading pointers. After the trials finish, the actually-observed model(s) are added as
`harness.model_observed` — so you can confirm which model really ran (useful when you let the CLI pick
its own default).

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

Claude Code, Gemini CLI, and Codex are built in. To use them, add your own, or attach an
**ACP-compatible** agent, see **[`coding_agent_harness.md`](coding_agent_harness.md)**.

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
