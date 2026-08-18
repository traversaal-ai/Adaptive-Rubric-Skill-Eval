# AdaRubric — Skill Eval

Measures whether a coding agent **finds, reads, and actually follows** the skill you give it.
Every run is scored four ways: the task's own **script checks**, a **fixed** baseline judge (same
rubric for every task), a **static** LLM judge (this task's rubric), and the **adaptive rubric**
(4 task-specific, evidence-checked tests — this project's research contribution:
[ADAPTIVE-RUBRIC.md](ADAPTIVE-RUBRIC.md)).

Agents: Claude Code, Gemini CLI, Codex, anything speaking ACP, and (Beta) open models on
Together AI. Tasks: your own, or [SkillsBench](https://github.com/benchflow-ai/skillsbench).

> The one rule: **`tasks/` is yours · `rubrics/` is generated but yours to edit ·
> `output/` is the record · `dataset/` is untouchable.**

---

# Part 1 — Setup and running

## 1. Install

```bash
git clone <this-repo> AdaRubric-Skill-Eval
cd AdaRubric-Skill-Eval
uv venv
uv pip install -e ".[dev]"
```

Optional sanity check (free — no keys, no Docker): `uv run pytest`. All pass, 1 skipped.

Install Docker and have it running — every run happens in a container by default (the agent's CLI
is installed inside it automatically, so there is nothing else to set up). On Linux:
`sudo apt install docker.io` and add yourself to the docker group.

Prefer running on your own machine instead? That's `--local`, and only then do you need the
agent's own CLI installed (they're separate programs — pip can't install them; any one is enough):

```bash
npm install -g @google/gemini-cli            # gemini-cli
npm install -g @anthropic-ai/claude-code     # claude-code
npm install -g @openai/codex                 # codex
```

`--harness acp` has no CLI of its own — it drives whatever agent you name in `--acp-cmd`
(e.g. `gemini --acp`), so install *that* agent's CLI for `--local`, or pass
`--acp-install <harness-name>` for docker runs.

## 2. Keys

Copy [`.env.example`](.env.example) to `.env` in the project root, fill in what you have. One
key is enough. Every command reads it from there automatically — no flag to pass, nothing to
export. Never commit `.env`.

```
ANTHROPIC_API_KEY=      # claude-code          GEMINI_API_KEY=    # gemini-cli
OPENAI_API_KEY=         # codex                TOGETHER_API_KEY=  # *-together (Beta)

JUDGE_LLM_PROVIDER=     # optional: who judges (gemini|anthropic|openai|together),
JUDGE_API_KEY=          # with its own key — independent of which agent runs.
JUDGE_MODEL=            # Without these: first key above judges (gemini first).
```

## 3. Run a task

Two runs. The first gives the agent the skill, the second withholds it. That pair IS the
measurement — the gap between the two scores is what the skill was worth.

```bash
uv run adarubric eval tasks/fix-logging --skill      # with the skill
uv run adarubric eval tasks/fix-logging --no-skill   # without it
```

That's the whole command. It runs **in Docker by default** — one fresh container per run, agent
CLI installed inside it, your machine untouched; just have Docker running (the first run builds
the image, a few minutes; later runs reuse it). Add `--local` to run on your own machine instead
(needs the agent's CLI from step 1).

Everything else (which agent, how many trials, which judges) is already set in the task's
`adarubric.yaml`; flags to override it for one run are in the [CLI](#cli) section further down,
and you can ignore them until you need them.

The terminal shows everything live while it runs — docker building, files being copied, the
agent's own output line by line, and each judge's score the moment it lands. No flag needed.

Several runs in one go? [`run_tasks.sh`](run_tasks.sh) is a ready-made, commented list of runs —
edit it and `bash run_tasks.sh`. A failing task never stops the rest.
Its runs go through Docker (one container per attempt, Docker Desktop must be running); the
`SANDBOX=` line at the top of the file switches the whole list back to `local`.

## 4. Watch it

```bash
python dashboard/serve.py        # http://127.0.0.1:8765
```

Click a run → **Score breakdown**: all four scorers with reasoning and evidence, turns, cost,
whether the skill was opened.

## 5. Run a SkillsBench task (needs Docker)

```bash
git clone https://github.com/benchflow-ai/skillsbench dataset/skillsbench

uv run adarubric check dataset/skillsbench/tasks/flood-risk-analysis   # FREE health check
```

`check` must print `OK: ... 1.00` — otherwise the task itself is broken, don't spend agents on it.

**Manual way (review the judging before spending):**

```bash
uv run adarubric init dataset/skillsbench/tasks/flood-risk-analysis
# creates tasks/flood-risk-analysis/adarubric.yaml (knobs; source: points at the dataset)
#     and rubrics/flood-risk-analysis/{static.md, adaptive.json}  ← read/edit these
uv run adarubric eval tasks/flood-risk-analysis --harness gemini-cli
```

**Automatic way:** run the dataset path directly — the same rubric files appear on first run:

```bash
uv run adarubric eval dataset/skillsbench/tasks/flood-risk-analysis --harness claude-code
```

## 6. Make your own task

```bash
mkdir -p tasks/my-task/skills/my-skill      # put your SKILL.md in there
uv run adarubric init tasks/my-task         # LLM drafts the yaml + rubrics — review, edit
uv run adarubric eval tasks/my-task --harness gemini-cli
```

Or by hand — the folder and its yaml:

```
tasks/my-task/
├─ adarubric.yaml            the control file (all keys in Part 2)
├─ skills/my-skill/SKILL.md  REQUIRED — the skill under test
├─ fixme.html  image.jpg     starting files — any names, LISTED under workspace:
└─ graders/my_tests.py       your check — staged in AFTER the agent leaves, never seen by it
```

```yaml
instruction: |
  Fix fixme.html so it renders; keep all file names unchanged.
workspace:                   # ONLY listed files reach the agent
  - fixme.html
  - image.jpg
graders:
  - type: deterministic
    run: python graders/my_tests.py
```

## 7. Run many tasks with one command

Open [`run_tasks.sh`](run_tasks.sh) — it's a plain list of task runs with comments. Edit the
list (add tasks, change agents, uncomment the samples), then:

```bash
bash run_tasks.sh         
```

Tasks run one by one; a failing one never stops the rest; everything lands on the dashboard.


## Dos and don'ts

**Do**
- Run `adarubric check` on any unfamiliar SkillsBench task before spending agents — it's free.
- Put each skill in its own folder: `skills/<name>/SKILL.md`.
- Edit anything in `rubrics/` — your text is used as-is, never regenerated.
- Name output files in the instruction if your grader checks them.
- Use `--no-skill` runs as the control; the reward gap is what the skill is worth.

**Don't**
- Don't put a root `SKILL.md` next to other files — the whole folder becomes the skill and ships
  to the agent, grader scripts included. Use `skills/<name>/` instead.
- Don't drop `SKILL.md` loose inside `skills/` — it needs its own subfolder.
- Don't create a file named `eval.yaml` — that name is the output receipt.
- Don't grade filenames the instruction never mentions.
- Don't edit `dataset/` or `output/`, and never put `instruction:`/`workspace:` in a `source:`
  wrapper (the loader refuses).
- Don't commit `.env`. A key pasted anywhere public is burned — rotate it.

---

# Part 2 — Reference

## The folders

| folder | who writes | what |
|---|---|---|
| `tasks/` | you (or `init` drafts) | task definitions: yaml, skill, starting files, checks |
| `rubrics/<task>/` | generated, yours to edit | `static.md` + `adaptive.json` — existing files are used as-is; delete to regenerate |
| `rubrics/fixed.md` | ours by default, yours to edit | the ONE rubric judging every task the same way (the baseline) |
| `output/` | AdaRubric | per run: `eval.yaml` receipt, `run.json`, `grading.json`, `rubric.md`, `transcript.json`, `changes.json`, `raw.log`, `workspace/` |
| `dataset/` | SkillsBench | never written to |

## adarubric.yaml — every key

```yaml
defaults:                      # flags override each for one run
  agent: gemini-cli            # --harness
  trials: 1                    # --trials
instruction: |                 # required (or TASK.md, or --instruction)
  ...
workspace:                     # files copied to the agent
  - file.txt                   #   lands at top, same name
  - src/a.csv:data/a.csv       #   src:dest keeps/changes the path
timeout: 300
docker:                        # only for --sandbox docker on your own tasks
  base: python:3.12-slim
  setup: pip install pandas
inject_skills: no              # control condition; --skill/--no-skill overrides
graders:
  - type: deterministic
    run: python graders/check.py
    weight: 0.7
grading:                       # which LLM judges run — the yaml is the source of truth
  fixed_rubric: yes            # yes | no | a file path (= on, use exactly that file)
  static_rubric: yes           # lines left out = yes; flags override for one run
  adaptive_rubric: yes
source: ../../dataset/...     # SkillsBench wrapper ONLY; combining with instruction/
                               # workspace/graders is an error
```

`TASK.md` + `grader.yaml` work as a simpler substitute (no workspace/defaults). The skill must
live at the task root (whole folder = skill, only for one-skill-and-nothing-else) or under
`skills/`, `.agents/skills/`, `.claude/skills/` — or be pointed at with `skill: <path>`.

## Scoring — the four scorers

**Reward = script checks + static judge (0.3), weighted. Fixed and adaptive: shown, weight 0.**

The ladder: script checks (ground truth) → **fixed** (same rubric every task — the baseline,
standalone, sees no other verdict) → **static** (this task's rubric; sees the automated checks'
verdicts, as skillgrade's judge did) → **adaptive** (4 generated tests, judged blind, a pass must
quote its proof or becomes a fail). Each rung isolates one question; adaptive earns reward weight
only if it beats static on the metrics in
[converting/step-8](converting/step-8-adaptive-rubric.md).

Script-check score reading: `{"score": 0..1}` JSON → `REWARD SCORE: x` line → exit code (0/1).
Any other exit = **grading failed**, shown as our problem, never as a zero.

Judge selection: `JUDGE_LLM_PROVIDER`/`JUDGE_API_KEY`/`JUDGE_MODEL` in `.env` — independent of
the agent. Unset → first key found (gemini → anthropic → openai → together). No key → judges skip
quietly, script checks still run.

## CLI

Precedence everywhere: **flag > yaml > built-in default.**

### `adarubric eval <path>`

| Flag | Default | Meaning |
|---|---|---|
| `--harness` | yaml `defaults.agent` | `claude-code` \| `gemini-cli` \| `codex` \| `acp` \| `oracle` \| `claude-code-together` \| `codex-together` (Beta). Comma-separate for several; pin models with `name:model`. |
| `--sandbox` | `docker` | `docker` (isolated container, CLI auto-installed) or `local` |
| `--local` | off | shortcut for `--sandbox local`: run on this machine — needs the agent's CLI installed |
| `--trials` | yaml, else 1 | repeats per launch |
| `--timeout` | yaml, else 300 | seconds per agent run |
| `--model` | agent's own | one model for all harnesses (required for `*-together`: a Together model id) |
| `--skill` / `--no-skill` | yaml, else `--skill` | `--no-skill` = the control run, skill withheld |
| `--fixed-rubric` / `--llm-rubric` / `--adaptive-rubric` | yaml, else yes | switch each judge for this run |
| `--adaptive-provider` / `--adaptive-model` | judge env / defaults | adaptive's own LLM |
| `--instruction` / `--task` / `--dataset` / `--output` / `--grade/--no-grade` | — | as named |

Keys are not a flag: `.env` in the folder you run from is loaded on every command, and your shell
environment fills in the rest.

ACP flags (`--acp-cmd`, `--acp-skill-dir`, `--acp-env-key`, `--acp-install`, `--acp-name`):
see [coding_agent_harness.md](coding_agent_harness.md).

### `adarubric init <path>` — drafts the config

Your skill folder or a SkillsBench task. Writes the yaml skeleton, generates the switched-on
rubrics into `rubrics/<task>/`, references them by path. `--static-rubric no` /
`--adaptive-rubric no` skip generation (no LLM spend); `--force` overwrites. SkillsBench gets a
thin `tasks/<name>/` wrapper — the dataset is never touched.

### `adarubric check <task>` — free health check

Runs the SkillsBench task's own reference solution through its real grader. Healthy = 1.00.

### `adarubric recompute` — refresh past runs' metrics after harness fixes (`--apply` to write).

## Editing rules

Rubric files are created only when missing; editing the yaml never touches them; switch `no`→`yes`
reuses the existing file; delete a rubric file (or `init --force`) to regenerate. `output/` is
append-only history.

## Metrics that matter

- **`skill_opened`** true/false/null (null = the agent doesn't report enough — never guessed).
- **`skill_depth`** — `used` (read past the front page) / `noticed` / `not_opened`. Only `used`
  is real skill use. Gemini maxes out at `noticed` (it reports no file paths).
- **turns** — two columns: *we measured* (model replies, one definition for all agents) and
  *agent claims* (its own number; they disagree).
- **grading failed ≠ 0.00** — a broken check is our problem and displays as such.

## Isolation guarantees

The sandbox receives ONLY `workspace:` files + the skill (control files stripped) + the prompt.
Graders, verifiers, rubrics, yamls, receipts are never present while the agent is alive; checks
stage in after export; judge keys never enter the sandbox; secrets are redacted from logs and
credential files stripped from exports.

## Together AI — what works right now

One `TOGETHER_API_KEY` (open models: Kimi, GLM, Qwen, DeepSeek, …). Status per piece:

| use | status | how |
|---|---|---|
| **Judge** (fixed / static / adaptive + rubric generation) | ✅ works today | `JUDGE_LLM_PROVIDER=together` + the key — or just have it as your only key |
| **Agent: Claude Code on Together** | ⚠️ Beta, unverified | `--harness claude-code-together --model <together-model-id>` (TogetherLink's `tclaude`) |
| **Agent: Codex on Together** | ⚠️ Beta, unverified | `--harness codex-together --model <together-model-id>` (TogetherLink's `tcodex`) |
| **Agent: ACP + Claude bridge on Together** | possible, undocumented territory | inject `ANTHROPIC_BASE_URL` etc. via `--acp-env-key` — see [coding_agent_harness.md](coding_agent_harness.md) |
| **Agent: Gemini CLI on Together** | ❌ not possible | gemini-cli can't talk to non-Google backends |

"Beta, unverified" means: the wiring is tested offline, but no live run has proven the
TogetherLink translation proxy end to end — validate one run per harness before trusting
`skill_opened` or cost from them. Always pin `--model` to a Together model id.

## Known rough edges

- One Docker image per task per agent (~1.5–1.9 GB) — clean up as you go.
- Judges cost money: fixed 1 + static 1 + adaptive 4 small calls per trial; generation 1 per task
  (cached). No key → they skip quietly.
- Judge-on-by-default makes SkillsBench rewards differ slightly from the paper's verifier-only
  numbers — `--llm-rubric no` for paper-faithful runs. Fixed/adaptive never affect the reward.
- Together harnesses are **Beta and unverified** (no live run yet) — validate one run before
  trusting `skill_opened`/cost; gemini has no Together route.
- codex's installer tracks its "latest" release — a new codex release can break docker runs until
  the installer learns new binaries (happened once; handled).
- Prices are a cached snapshot; ACP token/cost reporting varies by agent.

## Status

| Piece | Status |
|-------|--------|
| Running: local + Docker, 4 harnesses direct + all 3 over ACP, oracle, model pinning | 🟢 verified live |
| Deterministic grading + isolation, grading-failed vs zero | 🟢 verified live |
| Static LLM judge (skillgrade-verbatim) + per-task generated rubrics | 🟢 verified live |
| Fixed baseline judge (standalone, `rubrics/fixed.md`) | 🟢 built, live-run once |
| Adaptive rubric (4 tests, blind, evidence rule, weight 0) | 🟢 built + live-validated; comparison harness pending |
| `init` (both task kinds) · `batch` · `check` · `recompute` · dashboard | 🟢 |
| Judge/agent separation via `JUDGE_*` env vars | 🟢 tested offline |
| Together harnesses (`*-together` via TogetherLink) | ⚠️ Beta, wiring tested, no live run |
| Aggregation (pass@k) · reporting · step-8 comparison metrics | ⚪ planned |

## Security

Keys via `.env` (or your shell env) only, never on the command line; only the running agent's
key enters its sandbox; judge keys stay on
the host. **Any key ever pasted into a chat, commit, or screenshot: rotate it.**

## License

Apache License 2.0 — see [LICENSE](LICENSE).
