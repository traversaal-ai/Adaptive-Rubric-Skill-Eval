"""Input loading — turns a path into a normalized :class:`EvalSpec`.

Accepts three shapes and collapses them into one:
  (a) a SkillsBench ``tasks/<id>/`` package: ``task.md`` (instruction) + ``environment/skills/*``
      + ``environment/Dockerfile`` + ``verifier/`` + ``oracle/solve.sh``  → ``mode="skillbench"``;
  (b) a folder with a config file (``adarubric.yaml`` or skillgrade-style ``eval.yaml``): the file
      supplies instruction / workspace files / docker base+setup / timeout  → ``mode="generic"``;
  (c) a plain skill folder (``SKILL.md`` at root, or ``skills/*/SKILL.md``) + an explicit
      instruction  → ``mode="generic"``.

Ported from skillgrade `src/core/skills.ts`/`config.ts` plus SkillsBench layout handling.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from adarubric.core.models import EvalSpec, GraderSpec

_CONFIG_FILES = ("adarubric.yaml", "adarubric.yml", "eval.yaml", "eval.yml")


def load_spec(path: str, instruction: str | None = None, task: str | None = None) -> EvalSpec:
    """Resolve ``path`` into an :class:`EvalSpec`.

    ``instruction`` overrides whatever the task/config supplies (and is required when there is no
    task file or config). ``task`` selects a named task from a multi-task eval.yaml.
    """
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"Path not found: {p}")
    if p.is_file():
        # Point at a SKILL.md / task.md / eval.yaml → use its directory (a yaml keeps priority).
        if p.suffix in (".yaml", ".yml"):
            return _load_config(p.parent, p, instruction, task)
        p = p.parent

    if _is_skillsbench_task(p):
        return _load_skillsbench_task(p, instruction)
    for name in _CONFIG_FILES:
        cfg = p / name
        if cfg.is_file():
            return _load_config(p, cfg, instruction, task)
    return _load_skill_folder(p, instruction)


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
    """Load a generic task from ``adarubric.yaml`` or a skillgrade-style ``eval.yaml``.

    Supported (both shapes; skillgrade compat reads defaults + tasks[]):
      instruction, workspace (list of "src" or "src:dest"), docker: {base, setup}, timeout, skill.
    Grader definitions are carried later (Step 2); here we only read what running needs.
    """
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    defaults = raw.get("defaults") or {}

    # skillgrade shape: pick the named task, else the first.
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

    # Workspace entries: "src" (dest = basename) or "src:dest" (explicit relative dest).
    workspace_map: dict[str, str] = {}
    for entry in (task_def.get("workspace") or raw.get("workspace") or []):
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
        raise ValueError(f"No skill found for {cfg_path} — add a `skill:` path or a SKILL.md.")

    # Graders (compact "all-in-one" shape, option B): tasks[].graders or top-level graders.
    graders: list[GraderSpec] = []
    for g in (task_def.get("graders") or raw.get("graders") or []):
        if not isinstance(g, dict):
            continue
        graders.append(
            GraderSpec(
                type=str(g.get("type", "deterministic")),
                command=g.get("run") or g.get("command"),
                rubric=g.get("rubric"),
                model=g.get("model"),
                provider=g.get("provider"),
                weight=float(g.get("weight", 1.0)),
            )
        )

    spec = EvalSpec(
        name=str(name),
        instruction=str(instr).strip(),
        mode="generic",
        skill_paths=skill_paths,
        workspace_map=workspace_map,
        docker_base=(docker.get("base") if isinstance(docker, dict) else None),
        docker_setup=(docker.get("setup") if isinstance(docker, dict) else None),
        graders=graders,
    )
    if timeout:
        spec.timeout_sec = int(timeout)
    return spec


# --------------------------------------------------------------------------- plain skill folder


def _load_skill_folder(d: Path, instruction: str | None) -> EvalSpec:
    if instruction is None:
        raise ValueError(
            "An instruction is required when running a plain skill folder "
            "(pass --instruction / the `instruction` argument)."
        )

    skill_paths, name = _detect_skills(d)
    if not skill_paths:
        raise ValueError(
            f"No skill found in {d} — looked for SKILL.md at the root or under "
            f"skills/, .claude/skills/, .agents/skills/."
        )

    return EvalSpec(name=name, instruction=instruction, skill_paths=skill_paths)


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
