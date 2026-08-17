# AdaRubric — Skill Eval

Measures whether a coding agent **finds, reads, and actually follows** the skill you give it —
and scores the result three ways: the task's own script checks, a static LLM judge, and an
**adaptive rubric** (4 task-specific, evidence-checked tests — this project's research
contribution, see [ADAPTIVE-RUBRIC.md](ADAPTIVE-RUBRIC.md)).

Runs real agents — Claude Code, Gemini CLI, Codex, or anything speaking ACP — in an isolated
sandbox, on **your own tasks** or **[SkillsBench](https://github.com/benchflow-ai/skillsbench)**
benchmark tasks.

The one rule to remember:

> **`tasks/` is yours · `rubrics/` is generated but yours to edit · `output/` is the record ·
> `dataset/` is untouchable.**

---

# Part 1 — The guide

Follow top to bottom. Every step says what you should see.

## Step 0 — Install (once)

```bash
git clone <this-repo> AdaRubric-Skill-Eval
cd AdaRubric-Skill-Eval
uv venv
uv pip install -e ".[dev]"
```

## Step 1 — Prove it works, for free

```bash
uv run adarubric --help     # prints the commands
uv run pytest               # all pass, 1 skipped — no keys, no Docker needed
```

## Step 2 — Add keys

Create `.env` in the repo root (never commit it):

```
ANTHROPIC_API_KEY=sk-ant-...      # runs claude-code
GEMINI_API_KEY=...                # runs gemini-cli; also the default judge
OPENAI_API_KEY=sk-...             # runs codex
```

One key is enough to start. Only the key an agent needs enters its sandbox; judge keys never do.

## Step 3 — Run the example task

```bash
uv run adarubric run tasks/fix-logging --env-file .env
```

You should see: `mode=generic`, both judges `on`, the agent working, then a reward.
Runs locally in a temp folder — no Docker. The agent used is `claude-code` (the task's default);
add `--harness gemini-cli` to use another.

## Step 4 — Watch it

```bash
python dashboard/serve.py           # http://127.0.0.1:8765
```

Click any run → **Score breakdown** shows all three scorers, the adaptive tests with their
quoted evidence, turns, cost, and whether the skill was opened.

## Step 5 — Run a SkillsBench task

Get the dataset (once):

```bash
git clone https://github.com/benchflow-ai/skillsbench dataset/skillsbench
git -C dataset/skillsbench config core.autocrlf false     # Windows: keep Linux line endings
```

Health-check the task first — **free**, runs the author's own solution:

```bash
uv run adarubric check dataset/skillsbench/tasks/flood-risk-analysis
```

Must print `OK: ... scores 1.00`. If not, the task is broken — don't spend agents on it.

**Manual way (recommended): review the judging before spending.**

```bash
uv run adarubric init dataset/skillsbench/tasks/flood-risk-analysis
```

Creates (dataset untouched):

```
tasks/flood-risk-analysis/adarubric.yaml    ← your knobs; `source:` points at the dataset
rubrics/flood-risk-analysis/static.md       ← read/edit the static judge's rubric
rubrics/flood-risk-analysis/adaptive.json   ← read/edit the 4 adaptive tests
```

Edit what you want, then:

```bash
uv run adarubric run tasks/flood-risk-analysis --harness gemini-cli --sandbox docker --env-file .env
```

**Automatic way:** skip init, run the dataset path directly — same rubric files appear on the
first run, you just didn't review them first:

```bash
uv run adarubric run dataset/skillsbench/tasks/flood-risk-analysis --harness claude-code --sandbox docker --env-file .env
```

SkillsBench needs `--sandbox docker` (the tasks hardcode container paths).

## Step 6 — Make your own task

```bash
mkdir -p tasks/my-task/skills/my-skill
# put your SKILL.md in tasks/my-task/skills/my-skill/
uv run adarubric init tasks/my-task        # LLM drafts the yaml + rubrics for you
# review tasks/my-task/adarubric.yaml and rubrics/<task>/, edit, then:
uv run adarubric run tasks/my-task --harness gemini-cli --env-file .env
```

Or write the yaml yourself — the full folder shape:

```
tasks/my-task/
├─ adarubric.yaml            the control file (see Part 2)
├─ skills/my-skill/SKILL.md  REQUIRED — the skill under test (+ optional deeper pages)
├─ fixme.html  image.jpg     starting files — any names, anywhere, LISTED in workspace:
└─ graders/my_tests.py       your check — never shown to the agent
```

```yaml
instruction: |
  Fix fixme.html so it renders; keep all file names unchanged.
workspace:                   # ONLY listed files reach the agent — nothing else is copied
  - fixme.html
  - image.jpg                # short form lands at the top, name kept
  - myfolder/table.jpg:myfolder/table.jpg   # src:dest form keeps the path
graders:
  - type: deterministic
    run: python graders/my_tests.py         # staged AFTER the agent leaves
```

## Step 7 — Run many tasks with one command

Write a batch file once (copy [`batch.example.yaml`](batch.example.yaml)):

```yaml
defaults:                 # applied to every task unless it overrides
  harness: gemini-cli
  sandbox: docker
  env_file: .env
tasks:
  - path: tasks/fix-logging
    sandbox: local
  - path: dataset/skillsbench/tasks/flood-risk-analysis
  - path: tasks/fix-logging
    inject_skills: no     # the control run
```

```bash
uv run adarubric batch my-batch.yaml --dry-run   # see the commands, spend nothing
uv run adarubric batch my-batch.yaml             # run them all, one by one
```

Tasks run in order; a failing one doesn't stop the rest; you get a summary table at the end and
everything appears on the dashboard as usual.

## Step 8 — The two experiments worth running

```bash
# same task, skill withheld — the control. The reward gap = what the skill is worth.
uv run adarubric run tasks/my-task --inject-skills no --env-file .env

# same task on three agents, three repeats each
uv run adarubric run tasks/my-task --harness claude-code,gemini-cli,codex --trials 3 --env-file .env
```

---

# Part 2 — Reference

## The four folders

| folder | who writes | what |
|---|---|---|
| `tasks/` | you (or `init` drafts) | task definitions: yaml, skill, starting files, checks |
| `rubrics/<task>/` | generated, **you may edit** | `static.md` + `adaptive.json`. An existing file is used AS-IS, never regenerated. Delete it to regenerate. |
| `output/` | AdaRubric | per run: `eval.yaml` receipt, `run.json`, `grading.json`, `rubric.md`, `transcript.json`, `changes.json`, `raw.log`, `workspace/` |
| `dataset/` | SkillsBench | never written to |

## adarubric.yaml — every key

```yaml
defaults:                      # flags override each of these for one run
  agent: gemini-cli            # --harness
  trials: 1                    # --trials
instruction: |                 # required (or TASK.md, or --instruction)
  ...
workspace:                     # files copied to the agent. THREE forms:
  - file.txt                   #   src only → lands at top, same name
  - src/a.csv:data/a.csv       #   src:dest → lands at dest path
  - src: x.js                  #   skillgrade dict form also accepted
    dest: x.js
timeout: 300                   # seconds for the agent
docker:                        # only for --sandbox docker on your own tasks
  base: python:3.12-slim
  setup: pip install pandas
inject_skills: no              # control condition; --inject-skills overrides
graders:                       # your script checks (0..n of them)
  - type: deterministic
    run: python graders/check.py
    weight: 0.7
grading:                       # THE SOURCE OF TRUTH for the LLM judges
  static_rubric: yes           # yes | no | a file path (= on, use exactly that file)
  adaptive_rubric: yes         # same. Lines left out = yes. Flags override for one run.
source: ../../dataset/...     # SkillsBench wrapper ONLY — task comes from there; combining
                               # source: with instruction/workspace/graders is an error
```

`TASK.md` + `grader.yaml` work as a simpler substitute for the yaml (no workspace/defaults
support). If `adarubric.yaml` exists, it wins.

## Where the skill may live (exactly four places)

`SKILL.md` at the task root (whole folder becomes the skill) · `skills/<name>/` ·
`.agents/skills/<name>/` · `.claude/skills/<name>/`. Anywhere else: point `skill: <path>` in the
yaml, or run the SKILL.md path directly.

## The no-nos

1. Root `SKILL.md` + anything else in the folder → your files ship to the agent inside the skill.
   Use `skills/<name>/` the moment a second file exists.
2. `SKILL.md` loose inside `skills/` — it needs its own subfolder (the folder is the skill's name).
3. Don't create a file named `eval.yaml` — that's the output receipt's name.
4. Don't have your grader check filenames the instruction never mentions.
5. Never edit `dataset/`; never put `instruction:`/`workspace:` in a `source:` wrapper.
6. Never commit `.env`. A key pasted anywhere public is burned — rotate it.

## Scoring — the three scorers

**Reward = script checks + static judge (0.3), weighted. Adaptive is shown but weight 0.**

1. **Script checks** (deterministic / SkillsBench verifier) — run in the finished workspace,
   AFTER the agent is gone. Score read from: `{"score": 0..1}` JSON → `REWARD SCORE: x` line →
   exit code (0=1, 1=0). Any other exit = **grading failed**, never a zero — a broken check must
   not read as a failing agent.
2. **Static LLM judge** — one call; rubric = the task's own, else the generated
   `rubrics/<task>/static.md`, else a built-in fallback. Sees the whole session including the
   script verdicts (ported behaviour from skillgrade, prompt verbatim).
3. **Adaptive rubric** — 4 generated tests (1 completeness, 2 skill-fidelity ×2 weight,
   1 process-quality with 3 levels), one blind judge call each, **evidence rule**: a pass must
   quote the proving line or it becomes a fail. Not blended into the reward until it beats static
   on correlation / separation / stability ([converting/step-8](converting/step-8-adaptive-rubric.md)).

Judge keys: `GEMINI_API_KEY` → `ANTHROPIC_API_KEY` → `OPENAI_API_KEY`, first found wins. No key →
judges skip quietly, script checks still run.

## CLI — every command, every flag

One rule for all of them: **a flag beats the yaml, the yaml beats the built-in default.**
So the yaml is how a task normally runs; a flag is "just this once, do it differently".

### `adarubric run <path>` — run a task on an agent

`<path>` = a task folder in `tasks/`, a SkillsBench task in `dataset/`, or a bare skill folder.

| Flag | Default | In easy words |
|---|---|---|
| `--harness` | the yaml's `defaults.agent` | Which agent(s) run the task: `claude-code`, `gemini-cli`, `codex`, `acp` (any ACP agent), `oracle`. Comma-separate to run several: `--harness claude-code,codex`. Pin a model per agent with `name:model`. |
| `--sandbox` | `local` | Where the agent works: `local` = a temp folder on your PC; `docker` = a container (required for SkillsBench). |
| `--trials` | yaml, else 1 | How many times to repeat the run (agents are non-deterministic). |
| `--timeout` | yaml, else 300 | Seconds the agent gets before we stop it. |
| `--model` | the agent's own choice | Force one model for every agent in this run. |
| `--inject-skills` | yaml, else yes | `no` = run WITHOUT giving the agent the skill — the control condition. The reward gap vs a normal run is what the skill is worth. |
| `--llm-rubric` | yaml, else yes | `no` = skip the static LLM judge this run. |
| `--adaptive-rubric` | yaml, else yes | `no` = skip the adaptive rubric this run. |
| `--adaptive-provider` | first key found | Which LLM judges/generates the adaptive rubric: `gemini`, `anthropic`, `openai`. |
| `--adaptive-model` | provider's default | Exact model for the adaptive rubric. |
| `--instruction "..."` | the task's own | Replace the instruction for this run. |
| `--task <name>` | first | Pick one task from a multi-task yaml. |
| `--dataset` | `auto` | Force the pipeline: `skillbench` or `generic`. `auto` detects from the folder shape. |
| `--grade` / `--no-grade` | grade | `--no-grade` = run the agent, skip ALL scoring. |
| `--output <dir>` | `output` | Where results are written. |
| `--env-file <file>` | — | File with your `KEY=VALUE` API keys. |

**ACP-only flags** (running any agent that speaks ACP — full guide:
[coding_agent_harness.md](coding_agent_harness.md)):

| Flag | In easy words |
|---|---|
| `--acp-cmd` | How to start the agent, e.g. `'gemini --acp'` or `'claude-code-acp'`. Required with `--harness acp`. |
| `--acp-skill-dir` | Where that agent looks for skills (e.g. `.claude/skills`). Get this wrong and it never finds the skill. |
| `--acp-env-key` | The env var(s) the agent needs, e.g. `GEMINI_API_KEY` — injected and checked up front. |
| `--acp-install` | For docker: how to install the agent into the image — a harness name (`gemini-cli`) reuses that installer, or give a shell snippet. |
| `--acp-name` | The label the run is filed under (default: derived, e.g. `acp-gemini`). |

### `adarubric init <path>` — write the config for you

Point it at your skill folder OR a SkillsBench task. It drafts the `adarubric.yaml` skeleton,
generates the switched-on rubrics into `rubrics/<task>/`, and references them **by path** in the
yaml so you can see and edit them. For SkillsBench it writes a thin wrapper into `tasks/<name>/`
(`source:` points at the dataset — nothing copied, dataset unchanged).

| Flag | Default | In easy words |
|---|---|---|
| `--static-rubric` | yes | `no` = don't generate the static rubric (spends nothing); writes the switch off in the yaml. Flip to `yes` later and the next run generates it. |
| `--adaptive-rubric` | yes | Same, for the 4 adaptive tests. |
| `--force` | off | Overwrite an existing `adarubric.yaml`. |

Needs an API key for the drafting (gemini → anthropic → openai, from `<path>/.env` or your
shell); without one you get a commented template to fill in.

### `adarubric check <task>` — is this task even passable? (free)

Runs the SkillsBench task's **own reference solution** through its real grader — no agent, no
key, no cost. Healthy = `OK: ... scores 1.00`. Anything less = the task is broken; agent scores
from it would be meaningless. Run this before spending money on any unfamiliar task.

| Flag | Default | In easy words |
|---|---|---|
| `--sandbox` | `docker` | Where to run the solution. |
| `--timeout` | task's, else 300 | Seconds allowed — some reference solutions are slow. |
| `--output` | `output` | Where the check's own run is filed (`output/oracle/...`). |

### `adarubric batch <file>` — run many tasks from one yaml

`defaults:` + `tasks:` (each task = `path:` + any run flag as a key + optional raw `flags: [...]`
for exotic ones like ACP). Runs one by one; a failure doesn't stop the rest; summary table at the
end; exit code non-zero if anything failed.

| Flag | Default | In easy words |
|---|---|---|
| `--dry-run` | off | Print the exact commands that would run. Runs nothing, costs nothing. |

### `adarubric recompute` — re-read old runs with today's metrics

After a harness fix (say, turn counting improved), updates the numbers in past `run.json` files
without re-running any agent. `--output <dir>` picks the tree; **`--apply` actually writes** —
without it you get a preview of what would change.

## Editing rules (what regenerates, what doesn't)

- Rubric files are created **only when missing**. Editing the yaml (trials, agent, timeout,
  weights, switches) never touches them. `no` → `yes` reuses the existing file; `yes` → `no`
  leaves it on disk, unused.
- To force a fresh rubric: delete `rubrics/<task>/<file>` and run (or `init --force`).
- Everything in `output/` is append-only history — don't hand-edit it.

## The metrics that matter (in `run.json` / dashboard)

- **`skill_opened`** — true / false / null (null = the agent doesn't report enough to know).
- **`skill_depth`** — `used` (read past the front page) / `noticed` (front page only) /
  `not_opened`. Only `used` is real skill use.
- **turns** — two columns on purpose: *we measured* (model replies, one definition for every
  agent) and *agent claims* (its own number, when it reports one — they disagree).
- **reward + score breakdown** — per grader, with the judges' reasoning and evidence.
- **grading failed ≠ 0.00** — a broken check is our problem and is displayed as such.

## Isolation guarantees

The agent's sandbox receives ONLY: `workspace:` files + the skill (control files stripped) + the
prompt. Graders, verifiers, rubrics, yamls, receipts — never present while the agent is alive;
checks are staged in after export. Judge keys never enter the sandbox. Secrets are redacted from
all logs; credential files are stripped from exported workspaces.

## Known rough edges

- One Docker image per task per agent (~1.5–1.9 GB) — clean up as you go.
- Prices are a cached snapshot; cached-input discounts not modeled.
- Gemini can't report skill depth beyond `noticed` (no file paths in its tool tally).
- ACP token/cost reporting varies by agent (gemini-cli reports tokens off-spec, no cost).
- Judge-on-by-default makes SkillsBench rewards differ slightly from the paper's verifier-only
  numbers — use `--llm-rubric no` for paper-faithful runs. Adaptive never affects the reward.
- Judges cost money: static 1 small call/trial, adaptive 4, generation 1/task (cached).
- codex's installer tracks its "latest" release — a codex release adding new required binaries
  can break docker runs until the installer learns them (happened once; both binaries handled).

## Status

| Phase | Status |
|-------|--------|
| Running (2 pipelines, local+docker, 4 harnesses + oracle, ACP incl. codex) | 🟢 |
| Deterministic grading + isolation | 🟢 |
| Static LLM rubric (skillgrade-verbatim judge, per-task generation) | 🟢 |
| Adaptive rubric (4 tests, blind, evidence rule) | 🔵 built + live-validated; weight 0 until it beats static |
| Task validation (`check`) · Init (both task kinds) | 🟢 |
| Aggregation (pass@k) · Reporting | ⚪ planned |

## Security

Keys via `--env-file` only. Only the running harness's key enters its sandbox; judge keys stay on
the host. Secrets are redacted from logs/transcripts; credential files stripped from exports.
**Any key ever pasted into a chat, commit, or screenshot: rotate it.**
