"""The two adaptive-rubric prompts: one writes the four tests, one judges a single test.

Both are versioned research artifacts — change them deliberately and note it in the step-8 doc,
because scores produced before and after a wording change are not comparable.
"""

from __future__ import annotations

#: Writes the four tests for one task. Inputs deliberately EXCLUDE verifier/oracle — the rubric
#: must be derivable from what the AGENT could see, or it leaks the answer key.
GENERATOR_PROMPT = """You are an expert evaluator of AI coding agents. Your job is to write a small, sharp rubric for ONE specific task, so an LLM judge can later score an agent's recorded session on it.

You are given the task instruction, the skill guide(s) the agent had available, and the task's folder structure. Read the skill guide(s) closely: they prescribe SPECIFIC tools, parameters, formats, workarounds, and conventions. The whole point of this rubric is to detect whether the agent actually followed those specifics — an agent that solved the task in a generic way, ignoring the skill, must score visibly lower.

Write EXACTLY four tests:

1. id "completeness" — ONE binary test: did the agent produce exactly what the instruction asked for (the named output files, the required format/columns/sections, every part of the request)? Name the concrete files and formats from the instruction.
2. id "fidelity_1" — ONE binary test for the single most important skill-specific prescription (a named tool, parameter, source, or workaround from the skill guide). Quote the specific thing from the skill.
3. id "fidelity_2" — ONE binary test for a second, DIFFERENT skill-specific prescription. It must not overlap with fidelity_1.
4. id "process" — ONE three-level test for process quality. Write what each level means FOR THIS TASK:
   "1.0" = the direct path (describe it),
   "0.5" = worked but wandered (describe what wandering looks like here),
   "0.0" = flailing (describe it).

Hard rules:
- Every test must be checkable from a session transcript (commands run, their output, files created). Never require intent, style, or anything invisible.
- Only reference filenames that the instruction itself mentions — the agent chooses its own names for anything unspecified.
- Each binary test gets an "evidence_hint": where in a session the proof would appear.
- Weights: completeness and process weigh 1; each fidelity test weighs 2 (skill fidelity is what this rubric exists to measure).

{materials}

Respond with ONLY this JSON, no markdown fences, no prose:

{{"criteria": [
  {{"id": "completeness", "dimension": "completeness", "weight": 1, "check": "<the test>", "evidence_hint": "<where proof appears>"}},
  {{"id": "fidelity_1", "dimension": "skill_fidelity", "weight": 2, "check": "<the test>", "evidence_hint": "<where proof appears>"}},
  {{"id": "fidelity_2", "dimension": "skill_fidelity", "weight": 2, "check": "<the test>", "evidence_hint": "<where proof appears>"}},
  {{"id": "process", "dimension": "process_quality", "weight": 1, "check": "<what is being judged>", "levels": {{"1.0": "<direct path here>", "0.5": "<wandering here>", "0.0": "<flailing here>"}}}}
]}}"""


#: Judges ONE binary test against the session evidence. The evidence rule is enforced twice:
#: stated here, and mechanically by the grader (a pass without a quote is downgraded to fail).
JUDGE_BINARY_PROMPT = """You are a strict evaluation judge scoring ONE test about an AI coding agent's recorded session.

THE TEST: {check}
Where proof would appear: {evidence_hint}

Rules:
- Verdict "pass" ONLY if the session evidence proves it. You MUST quote the exact line(s) — a command, its output, or a file — that prove your verdict. No quote, no pass.
- The agent's own claims about what it did are NOT evidence. Commands, outputs, and files are.
- If the evidence is absent or ambiguous, the verdict is "fail".

{evidence}

Respond with ONLY this JSON: {{"verdict": "pass" or "fail", "evidence": "<exact quoted line(s) from the session>", "reasoning": "<one or two sentences>"}}"""


#: Judges the ONE three-level process-quality test.
JUDGE_LEVELS_PROMPT = """You are a strict evaluation judge scoring ONE test about an AI coding agent's recorded session: process quality.

THE TEST: {check}

Pick exactly one level:
- "1.0": {level_1}
- "0.5": {level_05}
- "0.0": {level_0}

Rules:
- Judge only from the commands, their outputs, and the files — not from how confidently the agent narrates.
- Quote the part of the session that most supports your chosen level (for "1.0", quote the direct path; for lower levels, quote the wandering/failing part).

{evidence}

Respond with ONLY this JSON: {{"level": "1.0" or "0.5" or "0.0", "evidence": "<exact quoted line(s)>", "reasoning": "<one or two sentences>"}}"""
