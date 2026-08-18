#!/usr/bin/env bash
# =============================================================================
# run_tasks.sh — list your tasks here, run them all with ONE command:
#
#     bash run_tasks.sh
#
# Every run happens IN DOCKER: one fresh container per attempt, the agent CLI and
# the skills installed inside it, your machine untouched. Start Docker Desktop
# first. (The first run builds the image — a few minutes; later runs reuse it.)
#
# (Windows: run it from Git Bash. Keys are read from ./.env automatically —
#  copy .env.example to .env first.)
#
# Each line below is one task run. Edit the list: change harnesses, add
# --trials 3, add tasks, comment lines out with '#'. Every run shows its
# full live output (docker build, the agent working, each judge's score).
# A failing task does NOT stop the rest, and every run also lands on the
# dashboard as usual:
#
#     python dashboard/serve.py        ->  http://127.0.0.1:8765
# =============================================================================
set -u
FAILED=0

# Where the runs happen. Change it to "local" here if you ever want to run on your
# own machine instead; a --sandbox written on a run line below wins over this.
#
# --skill/--no-skill is INDEPENDENT of this: in Docker the skills are copied into
# the container's own HOME (/root/.claude/skills, /root/.gemini/skills, …) and
# --no-skill withholds them there, exactly as it does locally. Docker only changes
# WHERE the agent runs, never WHAT it is given.
SANDBOX=docker

run() {                                   # runs one task, keeps going on failure
  cmd="$1"; shift
  sb=""                                   # only eval/check take --sandbox
  case "$cmd" in eval|run|check) sb="--sandbox $SANDBOX" ;; esac
  echo ""
  echo "=== $cmd $sb $* ==="
  # $sb unquoted on purpose: it must split into two words (flag + value).
  uv run adarubric "$cmd" $sb "$@" || { echo "!!! task failed - continuing"; FAILED=1; }
}

# --- sample tasks — edit from here down --------------------------------------

# 1) The built-in example task, in a container. Agent = its default (gemini-cli).
#    tasks/fix-logging has no `docker:` block, so the image is python:3.12-slim
#    with the gemini CLI installed on top.
run eval tasks/fix-logging --skill

# 2) Same task on another agent, repeated (agents are non-deterministic).
#    Note: a different agent = a different image to build the first time.
# run eval tasks/fix-logging --harness claude-code --trials 3

# 3) The control run — same task, skill WITHHELD. The reward gap vs run 1
#    is what the skill is worth:
# run eval tasks/fix-logging --no-skill

# 4) Send ONE line to your own machine instead of a container (the flag on the
#    line wins over SANDBOX above) — handy when Docker isn't running:
# run eval tasks/fix-logging --skill --sandbox local

# 5) A SkillsBench task (needs the dataset cloned, see README step 5). Docker-only
#    by nature. Health-check it for free first, then run it:
# run check dataset/skillsbench/tasks/flood-risk-analysis
# run eval dataset/skillsbench/tasks/flood-risk-analysis --harness gemini-cli

# 6) Your own task (make it with:  uv run adarubric init tasks/my-task):
# run eval tasks/my-task

# ------------------------------------------------------------------------------
echo ""
if [ "$FAILED" -eq 1 ]; then
  echo "done - some tasks FAILED (see above)."
  exit 1
fi
echo "done - all tasks passed."
