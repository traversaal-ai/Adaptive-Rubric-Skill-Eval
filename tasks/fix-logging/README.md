# Example: fix-logging

A code-fixing task. The agent gets a small Python file full of `print()` calls and is told to clean
it up. The skill says how Acme wants logging done.

```
fix-logging/
├─ adarubric.yaml               task + starting files + grader
├─ fixtures/orders.py           the broken file
├─ fixtures/applog.py           the house logger
└─ skills/house-logging/
   ├─ SKILL.md                  use applog, never print
   └─ references/naming.md      how to name events
```

Run it:

```bash
uv run adarubric eval examples/fix-logging --harness claude-code
```

## What it checks

Six things, a sixth each: the file is there, no `print(` left, `applog` imported, at least four
events logged, every event name is `snake_case` like `run_started`, and `python orders.py` still
runs.

The naming rule is only in `references/naming.md`. An agent that reads the front page and stops
gets the tool right and the names wrong — that's the gap between **noticed** and **used**.

## Expected scores

From hand-written answers, checked by the real grader:

| answer | score |
|---|---|
| followed the skill | **1.0** |
| skimmed it, named events `start` / `skipOrder` | **0.83** |
| never saw it, used the `logging` module | **0.5** |
| changed nothing | **0.33** |

The 0.5 row matters: switching to `logging` is a genuinely good fix. It's just not Acme's fix.

## Why a yaml here

This task needs files in the workspace before the agent starts, and the plain
`TASK.md` + `grader.yaml` folder can't ship those.

`adarubric.yaml` is never copied into the workspace, so the agent can't read the grader.

## The LLM judges — this task shows all four scorers

A judge API key in your `.env` (gemini picked first) turns the LLM judges on; each has an
off switch (`--fixed-rubric no`, `--llm-rubric no`, `--adaptive-rubric no`).

| scorer | rubric it reads | weight |
|---|---|---|
| script check | the python one-liner in the yaml | 0.7 |
| **fixed judge** — the baseline | [`../../rubrics/fixed.md`](../../rubrics/fixed.md) — same words for EVERY task; ours by default, edit it freely | 0 (shown only) |
| static judge | this task's own [prompts/quality.md](prompts/quality.md) — workflow, naming, efficiency | 0.3 |
| adaptive rubric | 4 task-specific tests, generated on first run into `rubrics/fix-logging/adaptive.json`, judged blind with quoted evidence | 0 (shown only) |

The run page on the dashboard shows all four sections with each judge's score and reasoning.
Comparing the fixed baseline against the task-specific judges on the same run is the whole point.
