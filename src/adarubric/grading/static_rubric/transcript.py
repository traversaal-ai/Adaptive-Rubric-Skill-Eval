"""Builds the session transcript the judge reads — the same four sections skillgrade sends.

Section order and headings are part of the ported prompt contract: Task Instruction, Commands
Executed, Agent Output, Prior Grader Results. The judge sees earlier graders' scores (the
deterministic checks run first), exactly as in skillgrade.
"""

from __future__ import annotations

from adarubric.core.models import TranscriptEntry


def build_transcript(entries: list[TranscriptEntry]) -> str:
    sections: list[str] = []

    instruction = next((e.instruction for e in entries if e.type == "run_start" and e.instruction), None)
    if instruction:
        sections.append(f"## Task Instruction\n{instruction}")

    commands = [e for e in entries if e.type == "command"]
    if commands:
        cmds = "\n\n".join(
            f"$ {e.command}\n{e.stdout or ''}"
            + (f"\nSTDERR: {e.stderr}" if e.stderr else "")
            + f"\n[exit code: {e.exit_code if e.exit_code is not None else 'unknown'}]"
            for e in commands
        )
        sections.append(f"## Commands Executed\n{cmds}")

    output = next((e.output for e in entries if e.type == "run_output" and e.output), None)
    if output:
        sections.append(f"## Agent Output\n{output}")

    prior = [e.grader_result for e in entries if e.type == "grader" and e.grader_result]
    if prior:
        results = "\n".join(
            f"- {g.grader_type}: score={g.score:.2f} — {g.details}" for g in prior
        )
        sections.append(f"## Prior Grader Results (automated tests)\n{results}")

    return "\n\n".join(sections)
