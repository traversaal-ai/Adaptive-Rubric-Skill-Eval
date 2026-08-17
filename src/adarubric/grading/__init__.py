"""Grading adapters — deterministic and llm_rubric, plus a registry.

The ``Grader`` contract lives in ``core/contracts.py``. Graders run AFTER the agent finishes,
against the final workspace — never visible to the agent.
"""

from __future__ import annotations

from typing import Callable

from adarubric.core.contracts import Grader
from adarubric.grading.adaptive_rubric import AdaptiveRubricGrader
from adarubric.grading.deterministic import DeterministicGrader, SkillsBenchVerifier
from adarubric.grading.static_rubric.grader import FixedRubricGrader, LlmRubricGrader

_REGISTRY: dict[str, Callable[[], Grader]] = {
    "deterministic": DeterministicGrader,
    "skillbench_verifier": SkillsBenchVerifier,
    "llm_rubric": LlmRubricGrader,
    "fixed_rubric": FixedRubricGrader,
    "adaptive_rubric": AdaptiveRubricGrader,
}


def grader_names() -> list[str]:
    return list(_REGISTRY)


def create_grader(grader_type: str) -> Grader:
    factory = _REGISTRY.get(grader_type)
    if factory is None:
        available = ", ".join(grader_names())
        raise ValueError(f'Unknown grader "{grader_type}". Available: {available}')
    return factory()
