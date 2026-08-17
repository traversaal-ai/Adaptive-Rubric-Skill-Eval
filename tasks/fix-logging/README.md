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
uv run adarubric run examples/fix-logging --harness claude-code --env-file .env
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

## The LLM judge

Besides the script check (weight 0.7), an LLM judge reads the whole session and scores it against
[prompts/quality.md](prompts/quality.md) (weight 0.3) — workflow, naming, efficiency. It runs by
default when a judge API key is in your `--env-file` (gemini picked first); turn it off with
`--llm-rubric no`. The run page on the dashboard shows both scores and the judge's reasoning.
