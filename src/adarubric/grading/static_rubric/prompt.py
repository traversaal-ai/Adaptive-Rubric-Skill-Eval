"""The judge prompt — ported word for word from skillgrade, so scores stay comparable.

Do not "improve" the wording: any drift changes what the judge rewards, and then numbers graded
before and after the change can't be compared.
"""

from __future__ import annotations

#: skillgrade's judge prompt, verbatim (src/graders/index.ts). {rubric} and {transcript} are the
#: only moving parts.
JUDGE_PROMPT = """You are an evaluation judge. Score the following agent session on a scale from 0.0 to 1.0 based on the rubric below.

IMPORTANT CONTEXT: The agent runs inside a CLI wrapper (e.g., Gemini CLI). The agent's tool calls (file edits, shell commands) appear as text in the "Agent Output" section. This is a real execution trace, not hallucination — the "Commands Executed" section shows the CLI invocation and its captured output. The "Prior Grader Results" section shows objective automated test results that verify the actual filesystem state after the agent ran.

## Rubric
{rubric}

## Session Transcript
{transcript}

Respond with ONLY a JSON object: {{"score": <number>, "reasoning": "<brief explanation>"}}"""


#: Used when the task defines no rubric of its own (the judge runs by default on every graded run;
#: ``--llm-rubric no`` turns it off). Deliberately generic: it must make sense for any task.
DEFAULT_RUBRIC = """Score the agent's work:

Task compliance (0 to 0.5):
- Did the agent do what the instruction asked, completely?
- Is the result correct and usable as-is?

Following provided guidance (0 to 0.3):
- If skills/guides were available in the workspace, did the agent find and follow them?
- Did it use the tools and conventions the guidance prescribes, rather than its own defaults?

Efficiency (0 to 0.2):
- Did it get there directly, without unnecessary trial-and-error or repeated failed commands?
"""


def build_prompt(rubric: str, transcript: str) -> str:
    return JUDGE_PROMPT.format(rubric=rubric, transcript=transcript)
