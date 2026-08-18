"""Builds one task's four adaptive tests and caches them — the generation half of step 8.

Inputs: the instruction, every SKILL.md (full text), and the task's folder structure — the parts
an agent could see. NEVER ``verifier/`` or ``oracle/``: a rubric derived from the answer key is a
leaked exam. Cached at ``<output>/rubrics/<task>.adaptive.json`` so every trial, attempt, and
harness of a task is scored against identical tests (and generation is paid for once).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from adarubric.core.models import EvalSpec
from adarubric.grading.adaptive_rubric.prompts import GENERATOR_PROMPT
from adarubric.grading.static_rubric.generate import _complete
from adarubric.grading.static_rubric.providers import JudgeError, pick_provider

#: Same defaults as everything else; ``--adaptive-provider`` / ``--adaptive-model`` override.
_GEN_MODELS = {
    "gemini": "gemini-3-flash-preview",
    "anthropic": "claude-sonnet-4-20250514",
    "openai": "gpt-4o",
    "together": "Qwen/Qwen2.5-72B-Instruct-Turbo",
}

_REQUIRED_IDS = ("completeness", "fidelity_1", "fidelity_2", "process")
_EXCLUDED_DIRS = {"verifier", "oracle", ".git", "__pycache__", "node_modules"}


def generated_adaptive_rubric(
    spec: EvalSpec,
    env: dict[str, str],
    rubrics_root: str,
    provider: str | None = None,
    model: str | None = None,
    legacy_root: str | None = None,
) -> list[dict] | None:
    """This task's four tests: from the cache, else generated now. ``None`` = couldn't generate
    (no key, API down, or the LLM's JSON never validated) — the caller then SKIPS adaptive
    scoring; there is deliberately no generic fallback, an adaptive rubric is task-specific or
    it is nothing.

    Cache: ``<rubrics_root>/<task>/adaptive.json`` — root-level and user-editable; an existing
    file is used as-is. ``legacy_root`` migrates old ``<output>/rubrics/*.adaptive.json`` in."""
    cache = Path(rubrics_root) / _slug(spec.name) / "adaptive.json"
    if not cache.is_file() and legacy_root:
        old = Path(legacy_root) / f"{_slug(spec.name)}.adaptive.json"
        if old.is_file():
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(old.read_text(encoding="utf-8"), encoding="utf-8")
    if cache.is_file():
        try:
            criteria = json.loads(cache.read_text(encoding="utf-8"))["criteria"]
            if _valid(criteria):
                return criteria
        except (ValueError, KeyError, OSError):
            pass  # unreadable cache → regenerate below
    chosen = pick_provider(provider, env)
    if chosen is None:
        return None
    try:
        raw = _complete(chosen, model or env.get("JUDGE_MODEL") or _GEN_MODELS[chosen],
                        _prompt(spec), env)
    except (JudgeError, KeyError):
        return None
    criteria = _parse(raw)
    if criteria is None:
        return None
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        json.dumps({"task": spec.name, "generator": f"{chosen}/{model or _GEN_MODELS[chosen]}",
                    "criteria": criteria}, indent=2),
        encoding="utf-8")
    return criteria


def _prompt(spec: EvalSpec) -> str:
    parts = [f"## Task instruction\n\n{spec.instruction}"]
    for p in spec.skill_paths:
        md = Path(p) / "SKILL.md"
        if md.is_file():
            parts.append(f"## Skill guide: {Path(p).name}\n\n{md.read_text(encoding='utf-8')}")
    tree = _folder_structure(spec)
    if tree:
        parts.append(f"## Task folder structure (what the agent starts with)\n\n{tree}")
    return GENERATOR_PROMPT.format(materials="\n\n".join(parts))


def _folder_structure(spec: EvalSpec, cap: int = 120) -> str:
    """A flat listing of the task's files as the agent would meet them. Skill folders are listed
    (their internal pages matter — reading past SKILL.md is the 'used' signal); verifier/oracle
    and junk dirs are excluded by rule."""
    lines: list[str] = []
    roots = [Path(p) for p in spec.workspace_files] + [Path(s) for s in spec.workspace_map] \
        + [Path(p) for p in spec.skill_paths]
    seen: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            entries = [root]
            base = root.parent
        else:
            entries = sorted(x for x in root.rglob("*") if x.is_file())
            base = root.parent
        for f in entries:
            if any(part in _EXCLUDED_DIRS for part in f.parts):
                continue
            rel = f"{root.name}/{f.relative_to(root).as_posix()}" if root.is_dir() else f.name
            rel = rel.replace(f"{root.name}//", f"{root.name}/")
            if rel in seen:
                continue
            seen.add(rel)
            lines.append(rel)
            if len(lines) >= cap:
                lines.append(f"... (more files, list capped at {cap})")
                return "\n".join(lines)
    return "\n".join(lines)


def _parse(raw: str) -> list[dict] | None:
    """The generator's JSON, validated hard: exactly the four required ids, right shapes."""
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).replace("```", "").strip()
    m = re.search(r"\{[\s\S]*\}", cleaned)
    if not m:
        return None
    try:
        criteria = json.loads(m.group(0)).get("criteria")
    except ValueError:
        return None
    return criteria if _valid(criteria) else None


def _valid(criteria: object) -> bool:
    if not isinstance(criteria, list) or len(criteria) != 4:
        return False
    by_id = {c.get("id"): c for c in criteria if isinstance(c, dict)}
    if set(by_id) != set(_REQUIRED_IDS):
        return False
    for cid in ("completeness", "fidelity_1", "fidelity_2"):
        if not str(by_id[cid].get("check") or "").strip():
            return False
    levels = by_id["process"].get("levels")
    if not isinstance(levels, dict) or not {"1.0", "0.5", "0.0"} <= set(levels):
        return False
    return True


def _slug(name: str) -> str:
    return re.sub(r"[^\w.-]+", "-", name).strip("-") or "task"
