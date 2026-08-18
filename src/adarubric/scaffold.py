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


def detect_skills_with_content(d: Path) -> list[tuple[str, str]]:
    """(name, SKILL.md text) for every skill the runner would find — same four locations."""
    from adarubric.loading import _detect_skills, _parse_skill_name

    paths, _ = _detect_skills(d)
    out: list[tuple[str, str]] = []
    for p in paths:
        md = (Path(p) / "SKILL.md").read_text(encoding="utf-8")
        out.append((_parse_skill_name(md) or Path(p).name, md))
    return out
