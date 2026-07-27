"""Grading adapters — deterministic (Step 2) and llm_rubric (Step 3), plus a registry.

The ``Grader`` contract lives in ``core/contracts.py``. Graders run AFTER the agent finishes,
against the final workspace — never visible to the agent.
"""

from __future__ import annotations

from typing import Callable

from adarubric.core.contracts import Grader
from adarubric.grading.deterministic import DeterministicGrader, SkillsBenchVerifier

_REGISTRY: dict[str, Callable[[], Grader]] = {
    "deterministic": DeterministicGrader,
    "skillbench_verifier": SkillsBenchVerifier,
    # "llm_rubric" arrives in Step 3.
}


def grader_names() -> list[str]:
    return list(_REGISTRY)


def create_grader(grader_type: str) -> Grader:
    factory = _REGISTRY.get(grader_type)
    if factory is None:
        available = ", ".join(grader_names())
        raise ValueError(f'Unknown grader "{grader_type}". Available: {available}')
    return factory()
