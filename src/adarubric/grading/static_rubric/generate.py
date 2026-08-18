"""Generate a task-specific rubric for tasks that don't have one — SkillsBench included.

Same treatment `adarubric init` gives your own skills (skillgrade's approach), applied at run
time to tasks whose folder we must not touch: an LLM reads the task instruction + the SKILL.md
front pages and writes the judging criteria. The result is CACHED under ``<output>/rubrics/`` —
generated once per task, then reused by every trial, attempt, and harness, so all agents are
judged against the identical text and the cost is one small LLM call per task.

Blindness rule: the generator sees ONLY the instruction and SKILL.md — never ``verifier/``,
never ``oracle/`` — so the rubric can't leak the answer key.

If generation can't run (no key, API down), the caller falls back to the built-in DEFAULT_RUBRIC.
"""

from __future__ import annotations

import re
from pathlib import Path

from adarubric.core.models import EvalSpec
from adarubric.grading.static_rubric.providers import JudgeError, _post, pick_provider

#: skillgrade's init models, and init's temperature (0.3) — generation, not judging.
_GEN_MODELS = {
    "gemini": "gemini-3-flash-preview",
    "anthropic": "claude-sonnet-4-20250514",
    "openai": "gpt-4o",
    "together": "Qwen/Qwen2.5-72B-Instruct-Turbo",
}

#: The rubric-writing part of skillgrade's init prompt, asked for on its own (init generates a
#: whole eval.yaml; here the task already exists — only the judging criteria are missing).
RUBRIC_PROMPT = """You are an expert at creating evaluation tasks for AI agent skills.

Given the following task instruction and skill definition(s), write an LLM rubric (criteria for the LLM judge) to score an AI agent's session on this task from 0.0 to 1.0.

The rubric must:
- Be specific to THIS task and THIS skill — name the concrete formats, tools, file names, and conventions the skill prescribes, so an agent that ignored the skill scores visibly lower.
- Cover: task compliance (did it do what was asked), skill use (did it follow the skill's specific guidance), and efficiency (did it get there without unnecessary trial-and-error).
- Assign a point range to each criterion so the total is 1.0.
- Judge only what can be seen in a session transcript (commands, outputs, files written).

## Task instruction

{instruction}

{skill_summaries}

Respond with ONLY the rubric text. No preamble, no markdown fences."""


def generated_task_rubric(
    spec: EvalSpec, env: dict[str, str], rubrics_root: str, legacy_root: str | None = None,
    provider: str | None = None, model: str | None = None,
) -> str | None:
    """The task-specific rubric: from the cache, else generated now. ``None`` = couldn't generate.

    Cache: ``<rubrics_root>/<task>/static.md`` — a root-level, user-editable folder shared by all
    harnesses and attempts of the task (an existing file is used AS-IS: edits win, nothing is
    regenerated). ``legacy_root`` migrates pre-existing ``<output>/rubrics/<task>.md`` files in,
    so old generations aren't re-bought.
    """
    cache = Path(rubrics_root) / _slug(spec.name) / "static.md"
    if not cache.is_file() and legacy_root:
        old = Path(legacy_root) / f"{_slug(spec.name)}.md"
        if old.is_file():
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(old.read_text(encoding="utf-8"), encoding="utf-8")
    if cache.is_file():
        text = cache.read_text(encoding="utf-8").strip()
        if text:
            return text
    chosen = pick_provider(provider, env)
    if chosen is None:
        return None
    try:
        text = _generate(spec, chosen, env, model or env.get("JUDGE_MODEL"))
    except (JudgeError, KeyError, IndexError, TypeError):
        return None
    if not text.strip():
        return None
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(text.strip() + "\n", encoding="utf-8")
    return text.strip()


def _generate(spec: EvalSpec, provider: str, env: dict[str, str], model: str | None = None) -> str:
    skills = []
    for p in spec.skill_paths:
        md = Path(p) / "SKILL.md"
        if md.is_file():
            skills.append(f"## Skill: {Path(p).name}\n\n{md.read_text(encoding='utf-8')}")
    summaries = "\n\n---\n\n".join(skills) or "## No skill files available."
    prompt = RUBRIC_PROMPT.format(instruction=spec.instruction, skill_summaries=summaries)
    return _complete(provider, model or _GEN_MODELS[provider], prompt, env)


def _complete(provider: str, model: str, prompt: str, env: dict[str, str]) -> str:
    """One completion at temperature 0.3 — same calls the init scaffolder makes."""
    if provider == "gemini":
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={env['GEMINI_API_KEY']}")
        data = _post(url, {}, {"contents": [{"parts": [{"text": prompt}]}],
                               "generationConfig": {"temperature": 0.3}})
        return data["candidates"][0]["content"]["parts"][0]["text"]
    if provider == "anthropic":
        data = _post("https://api.anthropic.com/v1/messages",
                     {"x-api-key": env["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
                     {"model": model, "max_tokens": 4096, "temperature": 0.3,
                      "messages": [{"role": "user", "content": prompt}]})
        return data["content"][0]["text"]
    if provider == "together":
        data = _post("https://api.together.xyz/v1/chat/completions",
                     {"Authorization": f"Bearer {env['TOGETHER_API_KEY']}"},
                     {"model": model, "max_tokens": 4096, "temperature": 0.3,
                      "messages": [{"role": "user", "content": prompt}]})
        return data["choices"][0]["message"]["content"]
    data = _post("https://api.openai.com/v1/chat/completions",
                 {"Authorization": f"Bearer {env['OPENAI_API_KEY']}"},
                 {"model": model, "max_tokens": 4096, "temperature": 0.3,
                  "messages": [{"role": "user", "content": prompt}]})
    return data["choices"][0]["message"]["content"]


def ensure_fixed_rubric(rubrics_root: str) -> str:
    """The ONE fixed rubric judging every task: <rubrics_root>/fixed.md. Created from the
    built-in default if the user hasn't written one — the text judging runs is always a
    visible, editable file. Returns the text."""
    from adarubric.grading.static_rubric.prompt import DEFAULT_RUBRIC

    path = Path(rubrics_root) / "fixed.md"
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "<!-- The FIXED rubric: judges EVERY task with these same words (the baseline\n"
            "     next to the generated static and adaptive rubrics). Edit freely - your\n"
            "     text is used as-is. Delete the file to restore this default. -->\n\n"
            + DEFAULT_RUBRIC, encoding="utf-8")
    return path.read_text(encoding="utf-8")


def _slug(name: str) -> str:
    return re.sub(r"[^\w.-]+", "-", name).strip("-") or "task"
