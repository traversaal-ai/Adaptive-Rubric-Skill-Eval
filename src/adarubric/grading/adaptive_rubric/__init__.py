"""The ADAPTIVE rubric — this project's research contribution (converting/step-8).

Where the static rubric is one fixed text, the adaptive rubric is **built per task**: an LLM reads
the task's instruction, its SKILL.md files, and the task's folder structure (never ``verifier/``,
never ``oracle/``) and writes exactly four tests —

1. Completeness            (binary)   — produced exactly what the instruction asked
2. Skill fidelity          (binary)   — did it do the thing the skill's SPECIFIC way
3. Skill fidelity, another (binary)   — a second, different prescription from the skill
4. Process quality         (3 levels) — direct path vs flailing, levels defined for THIS task

Each test is judged in its own LLM call, **blind** (the judge never sees the deterministic or
static-judge verdicts) and under the **evidence rule**: a pass without a quoted line of proof from
the session is a fail. Score = weighted fraction passed.

The adaptive score is recorded and displayed but carries **weight 0 in the reward** for now — it
must first beat the static rubric on correlation / separation / stability (see the step-8 doc)
before it earns a share of the blend.
"""

from adarubric.grading.adaptive_rubric.generate import generated_adaptive_rubric
from adarubric.grading.adaptive_rubric.grader import AdaptiveRubricGrader

__all__ = ["AdaptiveRubricGrader", "generated_adaptive_rubric"]
