"""LLM-as-judge grading against a STATIC rubric — one the user wrote by hand.

("Static" because the rubric is fixed text. Rubrics an LLM writes for you would be a separate,
future thing — nothing here generates rubric text.)

The judge prompt, the transcript sections, the provider order (gemini first), the default judge
models, and the forgiving answer-parsing are ported verbatim from skillgrade's ``LLMGrader`` so
scores stay comparable. The one deliberate difference: when the judge itself can't run (no API key,
API down, unreadable reply), the result is a grading *error*, never a score of 0 — a broken judge
must not read as a failing agent.
"""

from adarubric.grading.static_rubric.grader import LlmRubricGrader
from adarubric.grading.static_rubric.prompt import DEFAULT_RUBRIC
from adarubric.grading.static_rubric.providers import DEFAULT_MODELS, pick_provider

__all__ = ["DEFAULT_MODELS", "DEFAULT_RUBRIC", "LlmRubricGrader", "pick_provider"]
