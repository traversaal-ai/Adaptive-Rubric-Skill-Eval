# The Adaptive Skill Rubric — how it works, in one page

Most LLM judges score an agent's work against one fixed rubric — the same words for every task,
usually without ever reading the skill the agent was supposed to follow. Ours is different in
three ways: **the rubric is written per task**, **the judge must prove every verdict**, and
**the judge is blind** to the other scores.

## 1. Making the rubric (once per task)

An LLM reads three things — the task instruction, the skill guide(s) (`SKILL.md`), and the task's
folder layout. Never the answer key (`verifier/`, `oracle/`).

It writes exactly **four tests**:

| # | test | type | weight |
|---|------|------|--------|
| 1 | **Completeness** — produced exactly what the instruction asked (files, format, all parts) | pass / fail | 1 |
| 2 | **Skill fidelity** — did the most important thing the skill's *specific* way (its named tool, parameter, workaround) | pass / fail | 2 |
| 3 | **Skill fidelity** — a second, different prescription from the skill | pass / fail | 2 |
| 4 | **Process quality** — direct path vs flailing, with what each level means *for this task* | 1.0 / 0.5 / 0.0 | 1 |

Skill fidelity gets half the weight on purpose — "did the agent actually use the skill" is the
research question. The tests are cached (`rubrics/<task>/adaptive.json`), so every agent on
that task is scored against identical tests, for one small LLM call ever.

Real example (generated for a flood-analysis task, no human wrote this):
*"Did the agent aggregate instantaneous data to daily values using the maximum
(`resample('D').max()`), as the flood-detection skill prescribes — not the mean?"*

## 2. Judging (four calls per run)

Each test gets its **own** LLM call. The judge receives the evidence and nothing else:

- the instruction and the skill guide(s)
- every command the agent ran, with its output
- the agent's final message
- the files it created/changed — **with their full contents** (huge files get a marked middle cut)
- the tools it used (measured counts)

Two hard rules:

1. **Evidence or no pass.** The judge must quote the exact line — a command, an output, a file —
   that proves its verdict. A "pass" with no quote is mechanically downgraded to fail. The agent's
   own narration is not evidence.
2. **Blind.** The judge never sees the benchmark verifier's score or the static judge's score, so
   it can't just echo them. (The static judge does see them — that anchoring is one of the flaws
   this design removes.)

Score = weighted fraction of tests passed. It is shown next to the other scores on the dashboard —
each test as a box with its verdict, the quoted evidence, and the reasoning — but it carries
**weight 0 in the reward** until it proves itself (below).

## 3. Why trust it

The evidence rule was validated the hard way, on one real recorded run judged three times:

- judge shown almost no evidence → **0.0** — it refused to pass anything it couldn't see;
- shown the measured file changes → **0.83** — fidelity passed, quoting the agent's actual code;
- shown the files' contents → **1.00** — completeness passed, quoting the output file itself.

Same run, same judge, same rubric — only the evidence changed. The judge never guessed.

## 4. The bar it still has to clear

Adaptive counts as a contribution only if it beats the static rubric on all three, measured
against ground truth the harness already records:

1. **Correlation** with the verifier's score (judged blind);
2. **Separation** between with-skill and without-skill runs of the same task;
3. **Stability** — re-judging the same run moves the score less.

Until then: displayed, compared, never blended.
