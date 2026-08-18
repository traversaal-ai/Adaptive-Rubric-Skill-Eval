#!/usr/bin/env bash
# =============================================================================
# run_tasks.sh — list your tasks here, run them all with ONE command:
#
#     bash run_tasks.sh
#
# (Windows: run it from Git Bash. Keys are read from ./.env automatically —
#  copy .env.example to .env first.)
#
# Each line below is one task run. Edit the list: change harnesses, add
# --trials 3, add tasks, comment lines out with '#'. A failing task does NOT
# stop the rest, and every run shows up on the dashboard as usual:
#
#     python dashboard/serve.py        ->  http://127.0.0.1:8765
# =============================================================================
set -u
FAILED=0

run() {                                   # runs one task, keeps going on failure
  echo ""
  echo "=== $* ==="
  uv run adarubric "$@" || { echo "!!! task failed - continuing"; FAILED=1; }
}

# --- sample tasks — edit from here down --------------------------------------

# 1) The built-in example task (local, no Docker). Agent = its default (gemini-cli).
run eval tasks/fix-logging

# 2) Same task on another agent, repeated (agents are non-deterministic):
# run eval tasks/fix-logging --harness claude-code --trials 3

# 3) The control run — same task, skill WITHHELD. The reward gap vs run 1
#    is what the skill is worth:
# run eval tasks/fix-logging --inject-skills no-skill

# 4) A SkillsBench task (needs Docker + the dataset cloned, see README step 5).
#    Health-check it for free first, then run it:
# run check dataset/skillsbench/tasks/flood-risk-analysis
# run eval dataset/skillsbench/tasks/flood-risk-analysis --harness gemini-cli --sandbox docker

# 5) Your own task (make it with:  uv run adarubric init tasks/my-task):
# run eval tasks/my-task

# ------------------------------------------------------------------------------
echo ""
if [ "$FAILED" -eq 1 ]; then
  echo "done - some tasks FAILED (see above)."
  exit 1
fi
echo "done - all tasks passed."
