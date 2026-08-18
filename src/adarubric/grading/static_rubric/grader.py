"""The llm_rubric grader — sends rubric + session transcript to a judge model, reads back a score.

The answer-reading is skillgrade's, ported: clean JSON first, then a score dug out of broken JSON,
then a score found in plain text. Where skillgrade returned score 0 for "the judge never answered
usably", this returns a grading error instead — the run shows "grading failed", not "agent got 0".
"""

from __future__ import annotations

import json
import re

from adarubric.core.contracts import Grader, Sandbox
from adarubric.core.models import EvalSpec, GraderResult, GraderSpec, TranscriptEntry
from adarubric.grading.static_rubric.prompt import DEFAULT_RUBRIC, build_prompt
from adarubric.grading.static_rubric.providers import (
    DEFAULT_MODELS,
    JudgeError,
    call_judge,
    pick_provider,
)
from adarubric.grading.static_rubric.transcript import build_transcript

_TYPE = "llm_rubric"


class LlmRubricGrader(Grader):
    """LLM-as-judge against a static rubric (the task's own, or the built-in default)."""

    name = _TYPE
    #: Which earlier verdicts this judge may see. True to skillgrade: the AUTOMATED script checks
    #: only — never another LLM judge's opinion (judges echoing judges is noise, not evidence).
    prior_grader_types: tuple[str, ...] = ("deterministic", "skillbench_verifier")

    def grade(
        self,
        workspace: str,
        sandbox: Sandbox,
        grader_spec: GraderSpec,
        spec: EvalSpec,
        transcript: list[TranscriptEntry],
        env: dict[str, str] | None = None,
    ) -> GraderResult:
        rubric = (grader_spec.rubric or "").strip() or DEFAULT_RUBRIC
        provider = pick_provider(grader_spec.provider, env)
        if provider is None:
            return GraderResult(
                self.name, 0.0, grader_spec.weight,
                "no judge API key found (GEMINI_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY)",
                error="llm rubric needs a judge API key - none found in .env or the environment",
            )
        model = grader_spec.model or (env or {}).get("JUDGE_MODEL") or DEFAULT_MODELS.get(provider, "")
        prompt = build_prompt(rubric, build_transcript(transcript, self.prior_grader_types))
        try:
            reply = call_judge(provider, model, prompt, env)
        except JudgeError as e:
            return GraderResult(self.name, 0.0, grader_spec.weight, str(e), error=str(e))
        score, reasoning = parse_judge_reply(reply)
        detail_prefix = f"judge: {provider}/{model}\n"
        if score is None:
            return GraderResult(
                self.name, 0.0, grader_spec.weight,
                f"{detail_prefix}unreadable judge reply: {reply[:200]}",
                error="the judge replied but no score could be read from its answer",
            )
        return GraderResult(self.name, score, grader_spec.weight, detail_prefix + reasoning)


class FixedRubricGrader(LlmRubricGrader):
    """The FIXED-rubric judge: same one-call prompt shell as the static judge, but STANDALONE —
    it sees no other scorer's verdict at all, and its rubric is the SAME text for every task
    (rubrics/fixed.md, or the built-in default). The baseline rung of the comparison ladder:
    fixed -> generated static -> adaptive. Weight 0 in the reward, like adaptive."""

    name = "fixed_rubric"
    prior_grader_types: tuple[str, ...] = ()  # standalone: no verifier, no other judges


def parse_judge_reply(text: str) -> tuple[float | None, str]:
    """Read (score, reasoning) out of the judge's reply — skillgrade's forgiving chain, ported.

    Returns ``(None, "")`` only when no score can be found anywhere; the caller reports that as a
    grading error. Pure function, unit-tested.
    """
    cleaned = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()

    m = re.search(r"\{[\s\S]*\}", cleaned)
    if m:
        try:
            parsed = json.loads(m.group(0))
            score = _clamp(float(parsed.get("score") or 0))
            return score, str(parsed.get("reasoning") or "No reasoning provided")
        except (ValueError, TypeError):
            # Broken/truncated JSON — dig the score out anyway, like skillgrade does.
            sm = re.search(r'"score"\s*:\s*([\d.]+)', m.group(0))
            if sm:
                rm = re.search(r'"reasoning"\s*:\s*"([^"]*)', m.group(0))
                reasoning = (rm.group(1) + "... (response truncated)") if rm \
                    else "Score extracted from incomplete LLM response"
                return _clamp(float(sm.group(1))), reasoning

    # No JSON at all — a score mentioned in plain text still counts.
    sm = re.search(r'"score"\s*:\s*([\d.]+)|score[:\s]+(\d+\.?\d*)', text, re.IGNORECASE)
    if sm:
        return _clamp(float(sm.group(1) or sm.group(2))), "Score extracted from malformed LLM response"

    return None, ""


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))
