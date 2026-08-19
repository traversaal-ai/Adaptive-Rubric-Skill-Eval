"""``adarubric init`` — reads your SKILL.md and writes the task config for you.

skillgrade's ``init`` (src/commands/init.ts), ported step for step:

1. refuse if the config already exists (``--force`` overwrites);
2. detect skills (same four locations the runner searches);
3. no skill found → write a commented template and stop;
4. load ``<dir>/.env`` (existing environment wins, like skillgrade);
5. pick the LLM by key — GEMINI first, then ANTHROPIC, then OPENAI (same order, same models,
   temperature 0.3, 120s timeout);
6. send SKILL.md with skillgrade's generation prompt, verbatim — the LLM writes the whole config:
   instruction, workspace files, a deterministic grader, and **the llm rubric**;
7. strip markdown fences, write the file; any failure → fall back to the template.

Three deliberate deltas, all naming: the file written is ``adarubric.yaml`` (in this repo
``eval.yaml`` is the OUTPUT receipt, never an input), the prompt says adarubric.yaml, and the
default agent is ``gemini-cli`` (our registry name; skillgrade calls it ``gemini``).
One safety addition: the generated YAML is test-loaded; if it doesn't parse, you get a warning.
"""

from __future__ import annotations

import re
from pathlib import Path

from adarubric.grading.static_rubric.providers import _post

#: Same models init used in skillgrade.
_INIT_MODELS = {
    "gemini": "gemini-3-flash-preview",
    "anthropic": "claude-sonnet-4-20250514",
    "openai": "gpt-4o",
    "together": "Qwen/Qwen2.5-72B-Instruct-Turbo",
}
_KEY_ORDER = (("gemini", "GEMINI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY"),
              ("openai", "OPENAI_API_KEY"), ("together", "TOGETHER_API_KEY"))

#: skillgrade's generation prompt, verbatim apart from the three naming deltas above.
GENERATION_PROMPT = """You are an expert at creating evaluation tasks for AI agent skills.

Given the following skill definition(s), generate an adarubric.yaml file that defines 1-2 evaluation tasks to test whether an AI agent correctly discovers and uses the skill.

For each task:
- Write a realistic instruction (what a user would ask the agent to do)
- Define workspace files if needed (fixture files the agent works on)
- Write a deterministic grader (shell script that outputs JSON to stdout)

IMPORTANT GRADING RULES:
- Deterministic graders MUST output JSON to stdout: {{"score": 0.0-1.0, "details": "...", "checks": [...]}}
- Do NOT use exit codes for scoring. The grader should always exit 0 and report the score in JSON.
- Use awk for floating point arithmetic (bc is not available in node:20-slim).
- The "checks" array is optional but recommended for per-check breakdown.
- For workspace files, only reference files that exist in the skill directory or that the agent will create.

CRITICAL — FILENAME CONSISTENCY:
- The instruction MUST tell the agent exactly what filenames to create (e.g., "Save the result as output.txt").
- The deterministic grader MUST only check for filenames that are explicitly mentioned in the instruction.
- NEVER check for a hardcoded filename that the instruction does not mention — the agent will choose its own names and the grader will fail.
- Example: if the grader checks for "output.html", the instruction must say "Save the HTML file as output.html".

{skill_summaries}

Respond with ONLY the adarubric.yaml content. Use this exact format:

version: "1"

defaults:
  agent: gemini-cli
  provider: docker
  trials: 5
  timeout: 300
  threshold: 0.8
  docker:
    base: node:20-slim

tasks:
  - name: <descriptive-task-name>
    instruction: |
      <realistic user instruction>
      Save <expected output> as <exact-filename>.
    workspace:
      # Files to copy into the agent's workspace (optional).
      # Use string shorthand or src/dest objects:
      # - fixtures/app.js                    # copies as app.js
      # - src: templates/viewer.html
      #   dest: templates/viewer.html
    graders:
      - type: deterministic
        run: |
          # Check conditions and output JSON
          passed=0
          total=2
          c1_pass=false c1_msg="Check 1 failed"
          c2_pass=false c2_msg="Check 2 failed"

          if <check1>; then
            passed=$((passed + 1))
            c1_pass=true; c1_msg="Check 1 passed"
          fi

          if <check2>; then
            passed=$((passed + 1))
            c2_pass=true; c2_msg="Check 2 passed"
          fi

          score=$(awk "BEGIN {{printf \\"%.2f\\", $passed/$total}}")
          echo "{{\\"score\\":$score,\\"details\\":\\"$passed/$total checks passed\\",\\"checks\\":[{{\\"name\\":\\"check1\\",\\"passed\\":$c1_pass,\\"message\\":\\"$c1_msg\\"}},{{\\"name\\":\\"check2\\",\\"passed\\":$c2_pass,\\"message\\":\\"$c2_msg\\"}}]}}"
        weight: 0.7"""

TEMPLATE = """version: "1"

defaults:
  agent: gemini-cli
  provider: docker
  trials: 5
  timeout: 300
  threshold: 0.8
  docker:
    base: node:20-slim

tasks:
  - name: {task_name}
    instruction: |
      {instruction}

    graders:
      - type: deterministic
        run: |
          # Grader must output JSON: {{"score": 0.0-1.0, "details": "...", "checks": [...]}}
          echo '{{"score": 0.0, "details": "TODO: implement grader"}}'
        weight: 0.7

# Run the control condition (skill withheld) by setting this to no; --skill/--no-skill overrides.
# inject_skills: no

# Which LLM judges run (yes | no | a rubric file path). The yaml is the source of truth;
# flags (--llm-rubric / --adaptive-rubric) override for a single run without editing it.
grading:
  static_rubric: yes
  adaptive_rubric: yes
"""


def extract_instruction_hint(skill_md: str) -> str:
    """First paragraph after the main heading, as a TODO hint — skillgrade's logic, ported."""
    found_heading = False
    paragraph: list[str] = []
    for line in skill_md.split("\n"):
        if line.startswith("# ") and not found_heading:
            found_heading = True
            continue
        if found_heading:
            if not line.strip() and paragraph:
                break
            if line.startswith("#"):
                break
            if line.strip():
                paragraph.append(line.strip())
    if paragraph:
        return ("TODO: Write an instruction based on this skill.\n      "
                f"Skill description: {' '.join(paragraph)}")
    return "TODO: Write an instruction for the agent."


def pick_init_llm(env: dict[str, str]) -> str | None:
    """Provider by key presence — gemini first, then anthropic, then openai (skillgrade's order)."""
    return next((p for p, key_name in _KEY_ORDER if env.get(key_name)), None)


def build_prompt(skills: list[tuple[str, str]]) -> str:
    """``skills`` = [(name, SKILL.md content), …] — same summary shape skillgrade sends."""
    summaries = "\n\n---\n\n".join(f"## Skill: {name}\n\n{md}" for name, md in skills)
    return GENERATION_PROMPT.format(skill_summaries=summaries)


def generate_with_llm(skills: list[tuple[str, str]], provider: str, env: dict[str, str]) -> str:
    """Ask the LLM to write the whole config. Raises on any API failure (caller falls back)."""
    text = _complete(provider, _INIT_MODELS[provider], build_prompt(skills), env)
    if not text:
        raise RuntimeError(f"Empty response from {provider} API")
    return strip_fences(text) + "\n"


def strip_fences(text: str) -> str:
    """Remove markdown code fences around the YAML — same regexes as skillgrade."""
    return re.sub(r"```ya?ml\n?", "", text).replace("```", "").strip()


def _complete(provider: str, model: str, prompt: str, env: dict[str, str]) -> str:
    """One completion at temperature 0.3 (init's setting; the judge uses 0)."""
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


def render_template(task_name: str, instruction: str) -> str:
    return TEMPLATE.format(task_name=task_name, instruction=instruction)


# --------------------------------------------------------------- the one-file task (new format)

_TODO_RUN = 'echo \'{"score": 0.0, "details": "TODO: write your check - print {score: 0..1} JSON"}\''


def _block(text: str, indent: str) -> str:
    """Render ``text`` as a YAML block scalar under ``indent`` (instruction / run bodies)."""
    lines = str(text).rstrip().splitlines() or [""]
    return "|\n" + "\n".join(f"{indent}{ln}" if ln.strip() else "" for ln in lines)


def _grader_lines(g: dict, comment: str = "") -> str:
    """One graders: entry — type, include, weight, then run:/rubric: and any provider/model."""
    tail = f"   # {comment}" if comment else ""
    out = [f"  - type: {g['type']}{tail}",
           f"    include: {'yes' if g.get('include', True) else 'no'}"]
    if g.get("weight") is not None:
        out.append(f"    weight: {g['weight']}")
    for key in ("provider", "model"):
        if g.get(key):
            out.append(f"    {key}: {g[key]}")
    if g.get("run"):
        # Always a block scalar: shell commands are full of ': ', '{', '#' — every one of them
        # breaks a plain YAML scalar. A block carries ANY text verbatim (plus a harmless \n).
        out.append(f"    run: {_block(str(g['run']).rstrip(), '      ')}")
    if g.get("rubric"):
        rub = str(g["rubric"])
        if len(rub.splitlines()) > 1:
            out.append(f"    rubric: {_block(rub, '      ')}")
        else:
            out.append(f"    rubric: {rub}")
    return "\n".join(out)


def render_task_yaml(values: dict) -> str:
    """The whole task in ONE visible file — what ``init`` writes (and what merge-fill re-renders).

    ``values``: agent, trials, timeout, instruction, skills (list of rel paths), workspace (list
    of "src:dest" strings), graders (list of dicts with type/include/weight/run/rubric/…),
    inject_skills (bool | None). Every scorer appears with an explicit ``include:`` so the reader
    sees what runs and what doesn't — nothing hidden behind a default.
    """
    skills = values.get("skills") or []
    skills_lines = "\n".join(f"  - {s}" for s in skills) if skills else \
        "  # - skills/<name>        # TODO: no SKILL.md found - create one and list it here"
    workspace = values.get("workspace") or []
    ws_lines = "\n".join(f"  - {w}" for w in workspace) if workspace else \
        "  # - fixtures/data.csv:data.csv   # TODO: list every file the agent starts with"
    graders = "\n\n".join(
        _grader_lines(g, _GRADER_COMMENTS.get(str(g.get("type")), ""))
        for g in values.get("graders") or [])
    inject = values.get("inject_skills")
    inject_line = ("inject_skills: no\n\n" if inject is False else "")
    instr = values.get("instruction")
    instr_section = (f"instruction: {_block(instr, '  ')}" if instr else
                     "# WRITE THE TASK: what should the agent do? Name the exact output files.\n"
                     "instruction: |\n")

    return f"""# adarubric.yaml - the WHOLE task in one file: what the agent gets, and every scorer
# that judges it. `include: no` switches a scorer off entirely; flags override for one run.

defaults:
  agent: {values.get('agent') or 'gemini-cli'}
  trials: {values.get('trials') or 1}

{instr_section}

# The skill(s) under test - injected for the agent to discover (control files stripped).
skills:
{skills_lines}

# Files copied into the workspace before the agent starts (left: relative to this file).
workspace:
{ws_lines}

timeout: {values.get('timeout') or 300}

{inject_line}# Every scorer, visible. weight = share of the reward (fixed/adaptive are always 0:
# scored and shown, never blended). rubric paths live in rubrics/ - edit those files freely.
graders:
{graders}
"""


_GRADER_COMMENTS = {
    "deterministic": "YOUR check - the one thing left to write",
    "llm_rubric": "static judge: THIS task's rubric",
    "fixed_rubric": "baseline judge: same rubric for every task",
    "adaptive_rubric": "4 task-specific tests, judged blind",
}


def default_graders(rubrics_rel: str, slug: str, det_run: str | None = None,
                    det_weight: float = 0.7, include_static: bool = True,
                    include_adaptive: bool = True, include_fixed: bool = True) -> list[dict]:
    """The four-scorer skeleton with real paths — deterministic first, judges after.

    With no LLM key on the machine the judge entries are written ``include: no``: declared so
    the user SEES them, deliberately off until a key exists (flip to yes, or re-run init).
    An off entry is BARE — type + include only. No path hint for a rubric that was never made;
    flipping it to yes later generates the file at the standard rubrics/ spot automatically."""
    graders = [
        {"type": "deterministic", "include": True, "weight": det_weight,
         "run": det_run or _TODO_RUN},
        {"type": "llm_rubric", "include": include_static, "weight": 0.3,
         "rubric": f"{rubrics_rel}/{slug}/static.md"},
        {"type": "fixed_rubric", "include": include_fixed, "weight": 0.0,
         "rubric": f"{rubrics_rel}/fixed.md"},
        {"type": "adaptive_rubric", "include": include_adaptive, "weight": 0.0,
         "rubric": f"{rubrics_rel}/{slug}/adaptive.json"},
    ]
    for g in graders:
        if not g["include"]:
            g.pop("rubric", None)
            g["weight"] = None  # rendered without a weight line — nothing to weigh
    return graders


def parse_llm_draft(text: str) -> dict:
    """Pull instruction / workspace / deterministic check out of the LLM's draft yaml (the
    skillgrade-shaped ``tasks:[...]`` document). Anything unreadable → just missing."""
    import yaml as _yaml

    out: dict = {}
    try:
        raw = _yaml.safe_load(strip_fences(text)) or {}
    except _yaml.YAMLError:
        return out
    if not isinstance(raw, dict):
        return out
    task = (raw.get("tasks") or [{}])[0] if isinstance(raw.get("tasks"), list) else raw
    if not isinstance(task, dict):
        return out
    if task.get("instruction"):
        out["instruction"] = str(task["instruction"]).strip()
    ws: list[str] = []
    for entry in task.get("workspace") or []:
        if isinstance(entry, dict) and entry.get("src"):
            ws.append(f"{entry['src']}:{entry.get('dest') or Path(str(entry['src'])).name}")
        elif entry:
            ws.append(str(entry))
    if ws:
        out["workspace"] = ws
    for g in task.get("graders") or []:
        if isinstance(g, dict) and g.get("type", "deterministic") == "deterministic" and g.get("run"):
            out["det_run"] = str(g["run"]).rstrip()
            if g.get("weight") is not None:
                out["det_weight"] = float(g["weight"])
            break
    return out


def detect_skills_with_content(d: Path) -> list[tuple[str, str]]:
    """(name, SKILL.md text) for every skill the runner would find — same four locations."""
    from adarubric.loading import _detect_skills, _parse_skill_name

    paths, _ = _detect_skills(d)
    out: list[tuple[str, str]] = []
    for p in paths:
        md = (Path(p) / "SKILL.md").read_text(encoding="utf-8")
        out.append((_parse_skill_name(md) or Path(p).name, md))
    return out
