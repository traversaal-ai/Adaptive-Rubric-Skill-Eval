"""Input loading — turns a path into a normalized :class:`EvalSpec`.

Accepts several shapes and collapses them into one. ``eval.yaml`` is NOT an input name — it is
reserved for the manifest AdaRubric *generates* into the output folder.

  (a) a SkillsBench ``tasks/<id>/`` package: ``task.md`` (instruction) + ``environment/skills/*``
      + ``environment/Dockerfile`` + ``verifier/`` + ``oracle/solve.sh``  → ``mode="skillbench"``;
  (b) a folder with an ``adarubric.yaml`` config (power users): the file supplies instruction /
      workspace files / docker base+setup / timeout / graders  → ``mode="generic"``;
  (c) the **convention** folder (default for your own skills): a ``SKILL.md`` skill plus, at the
      folder root, an optional ``TASK.md`` (the instruction) and an optional ``grader.yaml``
      (deterministic checks). AdaRubric assembles the run and generates the output ``eval.yaml``.
      → ``mode="generic"``. ``--instruction`` overrides ``TASK.md``.

Both (b) and (c) are supported: an ``adarubric.yaml`` wins when present; otherwise the convention
files are read. The ``TASK.md`` / ``grader.yaml`` control files are stripped from the skill before
injection (see ``SKILL_INJECT_IGNORE``) so the agent never sees the task's grading.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from adarubric.core.models import EvalSpec, GraderSpec

_CONFIG_FILES = ("adarubric.yaml", "adarubric.yml")
_GRADER_FILES = ("grader.yaml", "grader.yml")


def load_spec(path: str, instruction: str | None = None, task: str | None = None) -> EvalSpec:
    """Resolve ``path`` into an :class:`EvalSpec`.

    ``instruction`` overrides whatever the task/config/``TASK.md`` supplies (and is required when
    none of them exist). ``task`` selects a named task from a multi-task ``adarubric.yaml``.
    """
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"Path not found: {p}")
    if p.is_file():
        # Point at a config .yaml → treat as an explicit config (any name, since it's explicit);
        # point at a SKILL.md / TASK.md → use its directory.
        if p.suffix in (".yaml", ".yml"):
            return _load_config(p.parent, p, instruction, task)
        p = p.parent

    if _is_skillsbench_task(p):
        return _load_skillsbench_task(p, instruction)
    for name in _CONFIG_FILES:
        cfg = p / name
        if cfg.is_file():
            return _load_config(p, cfg, instruction, task)
    return _load_convention_folder(p, instruction)


# --------------------------------------------------------------------------- SkillsBench task


def _is_skillsbench_task(d: Path) -> bool:
    return (d / "task.md").is_file() and (d / "environment").is_dir()


def _load_skillsbench_task(d: Path, instruction: str | None) -> EvalSpec:
    task_md = _strip_frontmatter((d / "task.md").read_text(encoding="utf-8")).strip()
    env = d / "environment"

    skills_dir = env / "skills"
    skill_paths: list[str] = []
    if skills_dir.is_dir():
        skill_paths = [str(c) for c in sorted(skills_dir.iterdir()) if c.is_dir()]

    # Everything else under environment/ is a workspace input the agent starts with.
    workspace_files: list[str] = []
    for c in sorted(env.iterdir()):
        if c.name in ("skills", "Dockerfile"):
            continue
        workspace_files.append(str(c))

    dockerfile = env / "Dockerfile"
    verifier = d / "verifier"
    oracle = d / "oracle" / "solve.sh"

    return EvalSpec(
        name=d.name,
        instruction=instruction or task_md,
        mode="skillbench",
        skill_paths=skill_paths,
        workspace_files=workspace_files,
        dockerfile=str(dockerfile) if dockerfile.is_file() else None,
        verifier_path=str(verifier) if verifier.is_dir() else None,
        oracle_path=str(oracle) if oracle.is_file() else None,
    )


# --------------------------------------------------------------------------- config file (yaml)


def _load_config(d: Path, cfg_path: Path, instruction: str | None, task: str | None) -> EvalSpec:
    """Load a generic task from ``adarubric.yaml`` or an ``eval.yaml``.

    Supported (both shapes; a ``defaults`` + ``tasks[]`` layout is also accepted):
      instruction, workspace (list of "src" or "src:dest"), docker: {base, setup}, timeout, skill.
    Grader definitions are carried later (Step 2); here we only read what running needs.
    """
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    defaults = raw.get("defaults") or {}

    # A skillbench WRAPPER: `source:` points at a dataset task folder. The benchmark's own
    # definition (instruction, data, verifier, oracle, Dockerfile, skills) comes from there —
    # this yaml carries only the run/judging knobs. It must not redefine the task itself, or
    # "SkillsBench results" would quietly stop being SkillsBench results.
    source = raw.get("source")
    if source:
        for forbidden in ("instruction", "workspace", "tasks", "graders"):
            if raw.get(forbidden):
                raise ValueError(
                    f"{cfg_path.name}: '{forbidden}:' is not allowed next to 'source:' — the "
                    f"benchmark task defines that itself. Allowed: defaults, timeout, grading.")
        src = (d / str(source)).resolve()
        if not _is_skillsbench_task(src):
            raise ValueError(f"{cfg_path.name}: source does not point at a SkillsBench task: {src}")
        spec = _load_skillsbench_task(src, instruction)
        spec.default_harness = defaults.get("agent") or defaults.get("harness")
        spec.default_trials = _maybe_int(defaults.get("trials"))
        timeout = raw.get("timeout") or defaults.get("timeout")
        if timeout:
            spec.timeout_sec = int(timeout)
        _apply_inject_skills(spec, raw, defaults)
        _apply_grading_switches(spec, raw, d)
        return spec

    # defaults + tasks[] shape: pick the named task, else the first.
    task_def: dict = {}
    tasks = raw.get("tasks")
    if isinstance(tasks, list) and tasks:
        if task:
            match = [t for t in tasks if t.get("name") == task]
            if not match:
                names = ", ".join(str(t.get("name")) for t in tasks)
                raise ValueError(f'Task "{task}" not found in {cfg_path.name}. Available: {names}')
            task_def = match[0]
        else:
            task_def = tasks[0]

    instr = instruction or task_def.get("instruction") or raw.get("instruction")
    if not instr:
        raise ValueError(f"No instruction found in {cfg_path.name} (and none passed via --instruction).")

    # Workspace entries, three accepted shapes: "src" (dest = basename), "src:dest", or the
    # skillgrade dict form {src: ..., dest: ...} so old eval.yaml files drop in unchanged.
    workspace_map: dict[str, str] = {}
    for entry in (task_def.get("workspace") or raw.get("workspace") or []):
        if isinstance(entry, dict):
            src_s, dest = str(entry.get("src", "")), str(entry.get("dest", "") or "")
            if not src_s:
                continue
            src_p = (d / src_s).resolve()
            workspace_map[str(src_p)] = dest or src_p.name
            continue
        entry = str(entry)
        src, sep, dest = entry.partition(":")
        # Guard against Windows drive letters ("C:\...") being read as src:dest.
        if sep and len(src) > 1:
            workspace_map[str((d / src).resolve())] = dest
        else:
            src_p = (d / entry).resolve()
            workspace_map[str(src_p)] = src_p.name

    docker = task_def.get("docker") or raw.get("docker") or defaults.get("docker") or {}
    timeout = task_def.get("timeout") or raw.get("timeout") or defaults.get("timeout")

    # Skill: explicit `skill:` path, else auto-detect in the folder.
    skill_rel = raw.get("skill") or defaults.get("skill")
    if skill_rel:
        sp = (d / str(skill_rel)).resolve()
        skill_paths = [str(sp if sp.is_dir() else sp.parent)]
        name = task_def.get("name") or raw.get("name") or d.name
    else:
        skill_paths, folder_name = _detect_skills(d)
        name = task_def.get("name") or raw.get("name") or folder_name
    if not skill_paths:
        raise ValueError(f"No skill found for {cfg_path} - add a `skill:` path or a SKILL.md.")

    # Graders (compact all-in-one shape): tasks[].graders or top-level graders.
    graders = _parse_graders(task_def.get("graders") or raw.get("graders") or [], base=d)

    # defaults.grader_provider / defaults.grader_model fill in graders that didn't pick their own.
    for g in graders:
        if g.type == "llm_rubric":
            g.provider = g.provider or defaults.get("grader_provider")
            g.model = g.model or defaults.get("grader_model")

    spec = EvalSpec(
        name=str(name),
        instruction=str(instr).strip(),
        mode="generic",
        skill_paths=skill_paths,
        workspace_map=workspace_map,
        docker_base=(docker.get("base") if isinstance(docker, dict) else None),
        docker_setup=(docker.get("setup") if isinstance(docker, dict) else None),
        graders=graders,
        # skillgrade-style defaults, overridable from the command line (CLI > yaml > built-in).
        default_harness=(task_def.get("agent") or defaults.get("agent") or defaults.get("harness")),
        default_trials=_maybe_int(task_def.get("trials") or defaults.get("trials")),
    )
    if timeout:
        spec.timeout_sec = int(timeout)
    _apply_inject_skills(spec, raw, defaults, task_def)
    _apply_grading_switches(spec, raw, d)
    return spec


def _apply_inject_skills(spec: EvalSpec, raw: dict, defaults: dict, task_def: dict | None = None) -> None:
    """``inject_skills: no`` in the yaml runs the task as the CONTROL condition (skill withheld).
    Same value --skill/--no-skill sets; the flag, when passed, wins for that run."""
    for holder in ((task_def or {}), raw, defaults):
        value = holder.get("inject_skills")
        if value is not None:
            on, _ = _switch(value, Path("."))
            spec.inject_skills = on
            return


def _apply_grading_switches(spec: EvalSpec, raw: dict, base: Path) -> None:
    """The yaml's ``grading:`` block — the source of truth for which LLM judges run.

    Each switch is yes/no (default yes = today's behaviour), OR a file path — which means "on,
    and use exactly this file" (static: rubric text; adaptive: the 4-test criteria JSON). A path
    that doesn't exist or doesn't validate is a load error, not a silent fallback: the user named
    a file, so guessing instead would be lying about what judged the run.
    """
    grading = raw.get("grading") or {}
    if not isinstance(grading, dict):
        return

    fixed = grading.get("fixed_rubric")
    on, path = _switch(fixed, base)
    spec.run_fixed_rubric = on
    if path is not None:
        if not path.is_file():
            raise ValueError(f"grading.fixed_rubric points at a missing file: {path}")
        spec.fixed_rubric_text = path.read_text(encoding="utf-8")

    static = grading.get("static_rubric")
    on, path = _switch(static, base)
    spec.run_llm_rubric = on
    if path is not None:
        if not path.is_file():
            raise ValueError(f"grading.static_rubric points at a missing file: {path}")
        spec.static_rubric_text = path.read_text(encoding="utf-8")

    adaptive = grading.get("adaptive_rubric")
    on, path = _switch(adaptive, base)
    spec.run_adaptive_rubric = on
    if path is not None:
        from adarubric.grading.adaptive_rubric.generate import _valid
        if not path.is_file():
            raise ValueError(f"grading.adaptive_rubric points at a missing file: {path}")
        parsed = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        criteria = parsed.get("criteria") if isinstance(parsed, dict) else parsed
        if not _valid(criteria):
            raise ValueError(
                f"grading.adaptive_rubric file {path} is not a valid 4-test criteria JSON "
                f"(ids must be completeness, fidelity_1, fidelity_2, process).")
        import json as _json
        spec.adaptive_criteria_json = _json.dumps({"criteria": criteria})


def _switch(value: object, base: Path) -> tuple[bool, Path | None]:
    """(on?, explicit file path or None). Missing/None = on (default). Strings may be yes/no
    words or a path; anything path-looking is treated as a path."""
    if value is None:
        return True, None
    if isinstance(value, bool):
        return value, None
    text = str(value).strip()
    if text.lower() in ("yes", "true", "1", "on"):
        return True, None
    if text.lower() in ("no", "false", "0", "off"):
        return False, None
    return True, (base / text).resolve()


def _maybe_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- convention folder


def _load_convention_folder(d: Path, instruction: str | None) -> EvalSpec:
    """The default generic shape: a ``SKILL.md`` skill + optional ``TASK.md`` + optional ``grader.yaml``.

    Instruction resolution: ``--instruction`` > ``TASK.md`` (frontmatter stripped) > error.
    Graders: parsed from ``grader.yaml`` if present, else none (ungraded run).
    The control files (``TASK.md`` / ``grader.yaml``) are stripped from the skill at injection time.
    """
    skill_paths, name = _detect_skills(d)
    if not skill_paths:
        raise ValueError(
            f"No skill found in {d} - looked for SKILL.md at the root, or one level under "
            f"skills/, .claude/skills/, .agents/skills/. For a skill somewhere else, point the "
            f"path straight at its SKILL.md, or add `skill: <path>` to an adarubric.yaml."
        )

    task_md = d / "TASK.md"
    if instruction is not None:
        instr = instruction
    elif task_md.is_file():
        instr = _strip_frontmatter(task_md.read_text(encoding="utf-8")).strip()
    else:
        raise ValueError(
            f"No instruction for {d} - add a TASK.md, an adarubric.yaml, or pass --instruction."
        )
    if not instr:
        raise ValueError(f"TASK.md in {d} is empty — provide an instruction.")

    return EvalSpec(
        name=name, instruction=instr, mode="generic",
        skill_paths=skill_paths, graders=_load_grader_file(d),
    )


# --------------------------------------------------------------------------- graders (shared)


def _load_grader_file(d: Path) -> list[GraderSpec]:
    """Parse ``grader.yaml``/``grader.yml`` at the folder root into graders (empty if absent)."""
    for name in _GRADER_FILES:
        gp = d / name
        if gp.is_file():
            raw = yaml.safe_load(gp.read_text(encoding="utf-8")) or {}
            items = raw.get("graders") if isinstance(raw, dict) else raw
            return _parse_graders(items or [], base=d)
    return []


def _parse_graders(items: object, base: Path | None = None) -> list[GraderSpec]:
    """Normalize a list of grader dicts into :class:`GraderSpec`s (``run`` or ``command`` accepted).

    ``base`` (the config file's folder) resolves file references:
    * ``rubric:`` may be a path to a text/markdown file — read here, so the spec carries the TEXT;
    * a deterministic ``run:`` may call files kept next to the config (``run: node graders/check.js``)
      — those are recorded in ``stage_paths`` and copied into the workspace only at grading time,
      AFTER the agent is gone. The agent never sees the checks.
    """
    graders: list[GraderSpec] = []
    for g in items if isinstance(items, list) else []:
        if not isinstance(g, dict):
            continue
        command = g.get("run") or g.get("command")
        graders.append(
            GraderSpec(
                type=str(g.get("type", "deterministic")),
                command=command,
                rubric=_resolve_rubric(g.get("rubric"), base),
                model=g.get("model"),
                provider=g.get("provider"),
                weight=float(g.get("weight", 1.0)),
                stage_paths=_command_file_refs(command, base),
            )
        )
    return graders


def _resolve_rubric(rubric: object, base: Path | None) -> str | None:
    """A rubric is inline text, or a path to a file holding it. Files are read at load time."""
    if not rubric:
        return None
    text = str(rubric)
    if base is not None and len(text.splitlines()) == 1:
        candidate = (base / text.strip()).resolve()
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    return text


#: Path-looking tokens in a grader command ("graders/check.js", "tests/verify.py"). Same idea as
#: skillgrade's reference scan, tightened: must contain a "/" so bare words never match.
_FILE_REF_RE = re.compile(r"[\w.-]+(?:/[\w.-]+)+")


def _command_file_refs(command: str | None, base: Path | None) -> list[tuple[str, str]]:
    """Files/dirs a grader command references, found next to the config file.

    The TOP folder of each reference is staged whole (``graders/check.js`` stages ``graders/``),
    so helpers the script imports come along — skillgrade's behaviour, ported.
    """
    if not command or base is None:
        return []
    staged: list[tuple[str, str]] = []
    seen: set[str] = set()
    for ref in _FILE_REF_RE.findall(command):
        top = ref.split("/", 1)[0]
        src = (base / top).resolve()
        if top in seen or not src.exists() or not (base / ref).exists():
            continue
        seen.add(top)
        staged.append((str(src), top))
    return staged


def _detect_skills(d: Path) -> tuple[list[str], str]:
    """Return (skill directories, a name for the eval)."""
    root_skill = d / "SKILL.md"
    if root_skill.is_file():
        name = _parse_skill_name(root_skill.read_text(encoding="utf-8")) or d.name
        return [str(d)], name

    paths: list[str] = []
    for sub in ("skills", ".agents/skills", ".claude/skills"):
        search = d / sub
        if not search.is_dir():
            continue
        for entry in sorted(search.iterdir()):
            if entry.is_dir() and (entry / "SKILL.md").is_file():
                paths.append(str(entry))
    return paths, d.name


_STRIP_FM_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)


def _strip_frontmatter(text: str) -> str:
    """Drop a leading YAML frontmatter block (SkillsBench task.md metadata) if present."""
    return _STRIP_FM_RE.sub("", text, count=1)


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_NAME_RE = re.compile(r"^name:\s*(.+)$", re.MULTILINE)
_HEADING_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


def _parse_skill_name(content: str) -> str | None:
    """Parse the skill name from SKILL.md — YAML frontmatter ``name:`` or the first ``# Heading``."""
    fm = _FRONTMATTER_RE.match(content)
    if fm:
        m = _NAME_RE.search(fm.group(1))
        if m:
            return m.group(1).strip().strip("'\"")
    h = _HEADING_RE.search(content)
    if h:
        return h.group(1).strip().lower().replace(" ", "-")
    return None
