"""Data types that flow through the harness — results, transcripts, and the normalized eval spec.

Plain dataclasses only: the common language every layer speaks. No logic, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# --------------------------------------------------------------------------- results & transcript


@dataclass
class ShellResult:
    """Outcome of a single shell command run inside a sandbox workspace."""

    stdout: str
    stderr: str
    exit_code: int


class TriggerSource(str, Enum):
    """How we detected that a skill was triggered during a run."""

    TOOL_USE = "tool_use"
    FILE_READ = "file_read"
    INIT_LIST = "init_list"


@dataclass
class SkillTrigger:
    """A single observed skill activation during a run."""

    name: str
    source: TriggerSource
    timestamp: str | None = None
    details: str | None = None


@dataclass
class RunOutput:
    """Structured output of running one harness on an instruction.

    Harness adapters (piece 1.4) fill in whatever the CLI exposes; the runner maps these onto the
    ``RunMeta`` metrics. Fields a harness can't report stay ``None`` / empty.
    """

    output: str
    raw_output: str | None = None
    skills_triggered: list[SkillTrigger] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    tool_counts: dict[str, int] = field(default_factory=dict)
    num_turns: int | None = None            # computed by the adapter (model replies)
    num_turns_reported: int | None = None   # what the CLI said, verbatim
    duration_api_ms: float | None = None
    cost_usd: float | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int | None = None  # input served from the prompt cache, when reported
    # Measured skill-usage signal (set when the harness can parse its trajectory, e.g. stream-json).
    # None = the harness couldn't determine it; True/False = definitively measured.
    skill_opened: bool | None = None
    skill_files_read: list[str] = field(default_factory=list)
    # A harness-level failure (e.g. the agent CLI errored mid-run). The runner marks the attempt
    # unsuccessful when set — a run that errored must never be reported as success.
    error: str | None = None


@dataclass
class GraderResult:
    """Score produced by one grader. (Populated from Step 2 onward.)"""

    grader_type: str
    score: float  # 0.0 - 1.0
    weight: float
    details: str = ""
    #: Set when the grader ITSELF failed to reach a verdict (check script crashed, wrong sandbox, no
    #: verifier present). Distinct from ``score=0.0``, which means "the answer was checked and got
    #: nothing right". A result carrying an ``error`` is excluded from the reward average — a broken
    #: check must never be reported as a failing agent.
    error: str | None = None


@dataclass
class TranscriptEntry:
    """One timestamped event in an attempt's transcript.

    ``type`` is one of: ``run_start`` | ``command`` | ``run_output`` | ``grader`` | ``reward``.
    """

    type: str
    timestamp: str
    instruction: str | None = None
    command: str | None = None
    stdout: str | None = None
    stderr: str | None = None
    exit_code: int | None = None
    output: str | None = None
    value: float | None = None
    grader_result: GraderResult | None = None


@dataclass
class WorkspaceChanges:
    """What the harness did to the workspace — created / modified / deleted files.

    Computed by diffing a file snapshot taken after ``setup`` against one taken after ``run``.
    Written to ``changes.json`` in the output folder.
    """

    created: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)


@dataclass
class Usage:
    """Cost & efficiency counters. Any field is ``None`` when the harness doesn't report it."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    #: Input tokens served from the provider's prompt cache, when reported. Cached input bills at a
    #: fraction of the normal rate, so a run with heavy caching costs far less than input_tokens
    #: alone suggests — recorded here so the estimate can be judged (or corrected) rather than
    #: silently reading high.
    cached_input_tokens: int | None = None
    #: Model replies, computed by US with one definition for every harness (core/turns.py). This is
    #: the comparable number. `None` only when the agent's output shows nothing to count.
    num_turns: int | None = None
    #: What the agent CLAIMED, in its own words — kept because it is a different fact, not a worse one.
    #: They disagree: on one run claude reported 20 while the real reply count was 15, and codex
    #: reported 1 because its counter tracks prompt cycles. Recording both makes that visible instead
    #: of forcing a choice between two numbers that measure different things.
    num_turns_reported: int | None = None
    num_tool_calls: int | None = None
    num_commands: int = 0
    cost_usd: float | None = None  # reported by the harness, when available
    estimated_cost_usd: float | None = None  # computed from tokens x model price (core/pricing.py)
    cost_source: str | None = None  # "reported" | "estimated" | None
    tools_used: list[str] = field(default_factory=list)  # e.g. ["Read", "Bash", "Edit"]
    tool_counts: dict[str, int] = field(default_factory=dict)  # per-tool call counts


@dataclass
class Timing:
    """Wall-clock breakdown (milliseconds), so agent time is separable from harness overhead."""

    total_ms: float = 0.0
    setup_ms: float | None = None
    run_ms: float | None = None
    export_ms: float | None = None


@dataclass
class SkillUsage:
    """Did the skill get discovered and used, and how early?

    ``skill_opened`` is ``None`` (not ``False``) when the harness output can't tell us either way.
    """

    skill_opened: bool | None = None
    #: How deeply: "used" (went past the front page) | "noticed" (front page only) | "not_opened" |
    #: None (harness can't tell). ``skill_opened`` alone can't separate skimming the headings from
    #: working from the detail, and only the second is skill use — see ``core/skill_depth.py``.
    skill_depth: str | None = None
    skills_triggered: list[SkillTrigger] = field(default_factory=list)
    skill_files_read: list[str] = field(default_factory=list)  # SKILL.md, references/*, …
    num_skill_files_read: int = 0
    time_to_first_skill_ms: float | None = None
    first_skill_turn: int | None = None


@dataclass
class RunMeta:
    """All metrics for one attempt, written to ``run.json``. Never stores secret values.

    ``env_key_used`` records the *name* of the env var supplied to the harness, not its value.
    """

    # identity / reproducibility
    harness: str
    sandbox: str
    task: str
    env_key_used: str | None = None
    harness_version: str | None = None
    #: The model the harness actually REPORTED running (read back from its own output). This is the
    #: ground truth for "which model produced this result" and is what reports should quote.
    #: ``None`` means the CLI exposes no model in its output — not that no model was used.
    model: str | None = None
    #: The model we ASKED for via ``--model`` / ``--harness name:model``. ``None`` = we pinned
    #: nothing and let the CLI pick. Kept separate from ``model`` so a pin that silently didn't take
    #: effect is visible instead of being papered over.
    model_requested: str | None = None
    base_image: str | None = None  # docker only
    platform: str | None = None
    adarubric_version: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    # outcome
    exit_code: int | None = None
    success: bool = False  # the RUN completed (agent finished without error/timeout)
    timed_out: bool = False
    error: str | None = None
    graded: bool = False  # a grader reached a VERDICT (false when grading itself broke)
    reward: float = 0.0  # weighted grader score 0..1 (0 and meaningless when graded is false)
    #: Why grading produced no verdict, when it didn't. ``graded=False`` + this set means "we could
    #: not score this run", which is a different fact from "the agent scored zero" and must be
    #: reported differently — a broken check script is our problem, not the model's.
    grading_error: str | None = None
    #: Set when copying the agent's files out to ``workspace/`` failed. Archival only — the run
    #: itself still happened and is still scored, because the grader reads the live container, not
    #: the export. A failed copy must never discard a completed run.
    export_error: str | None = None
    #: False when ``--inject-skills no`` withheld the task's skills. Essential context for the score:
    #: a low reward means something completely different depending on whether the agent was given the
    #: guidance. Comparing the two conditions is what measures a skill's worth.
    skills_injected: bool = True
    # analysis
    usage: Usage = field(default_factory=Usage)
    timing: Timing = field(default_factory=Timing)
    skill_usage: SkillUsage = field(default_factory=SkillUsage)
    files_created: int = 0
    files_modified: int = 0
    files_deleted: int = 0


class TrialStage(str, Enum):
    """The lifecycle state of one trial — what a live dashboard renders per cell."""

    QUEUED = "queued"
    PREPARING = "preparing"  # docker image build (no-op for local)
    SETTING_UP = "setting_up"  # workspace + skill injection
    RUNNING = "running"  # harness executing
    EXPORTING = "exporting"  # copying final workspace + computing changes
    GRADING = "grading"  # deterministic / llm graders
    DONE = "done"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


@dataclass
class ProgressEvent:
    """A lifecycle event emitted by the runner for live tracking (see ProgressReporter).

    ``type`` is one of: ``attempt_started`` | ``trial_started`` | ``stage_changed`` |
    ``trial_finished`` | ``attempt_finished``. An *attempt* is one launch of the command; a *trial*
    is one repetition inside it.
    """

    type: str
    timestamp: str
    harness: str | None = None
    task: str | None = None
    attempt: int | None = None  # which launch (batch)
    trial: int | None = None  # which repetition inside the attempt
    stage: TrialStage | None = None
    reward: float | None = None
    detail: str | None = None
    meta: "RunMeta | None" = None


@dataclass
class Trial:
    """Everything captured from a single run of the eval (one *trial*).

    Points at the output written on disk (``run.json`` = ``meta``, ``changes.json`` = ``changes``,
    ``transcript.json`` = ``transcript``, ``raw.log`` = the harness's raw working log,
    ``grading.json`` = ``grader_results`` + ``reward``).
    """

    trial_id: int
    meta: RunMeta | None = None
    changes: WorkspaceChanges | None = None
    transcript: list[TranscriptEntry] = field(default_factory=list)
    raw_log: str | None = None
    reward: float = 0.0
    graded: bool = False
    grader_results: list[GraderResult] = field(default_factory=list)
    output_dir: str | None = None  # output/<harness>/<task>/attempt-N/trial-M


@dataclass
class EvalReport:
    """Aggregate over all trials of one attempt (launch). Full aggregation lands in Step 4."""

    task: str
    harness: str = ""
    attempt: int = 1
    trials: list[Trial] = field(default_factory=list)
    skills_used: list[str] = field(default_factory=list)
    output_dir: str | None = None
    pass_rate: float = 0.0
    pass_at_k: float = 0.0
    pass_pow_k: float = 0.0


# --------------------------------------------------------------------------- normalized input


@dataclass
class GraderSpec:
    """Description of one grader. Consumed from Step 2 (deterministic) / Step 3 (llm_rubric)."""

    type: str  # "deterministic" | "llm_rubric"
    command: str | None = None  # deterministic: shell command to run
    rubric: str | None = None  # llm_rubric: rubric TEXT (file paths are resolved at load time)
    model: str | None = None  # llm_rubric: model override
    provider: str | None = None  # llm_rubric: "gemini" | "anthropic" | "openai"
    weight: float = 1.0
    #: deterministic only: files/dirs the command needs (e.g. ``run: node graders/check.js``),
    #: found next to the config at load time. Staged into the workspace AFTER the agent is gone,
    #: right before the command runs — the agent never sees them. (host src, workspace-relative dest)
    stage_paths: list[tuple[str, str]] = field(default_factory=list)
    #: True when WE added this grader (the default llm rubric), not the task's author. An auto
    #: grader that can't run is silently skipped; one the author asked for reports a grading error.
    auto: bool = False


@dataclass
class EvalSpec:
    """A harness-agnostic description of one eval to run.

    Both supported inputs — a plain skill folder (+ instruction) and a SkillsBench ``tasks/<id>/``
    package — are resolved into this one object, so the rest of the harness has a single code path.
    Step 1 uses: ``name``, ``instruction``, ``skill_paths``, ``workspace_files``,
    ``docker_base`` / ``dockerfile``, ``attempts``, ``timeout_sec``. The rest is wired in later phases.
    """

    name: str
    instruction: str
    skill_paths: list[str] = field(default_factory=list)  # skill dirs to inject
    #: Whether to actually place those skills where the agent can find them. ``--inject-skills no``
    #: sets this False to run the SAME task with the guidance withheld, which is the control half of
    #: "did the skill help?". ``skill_paths`` is deliberately left populated so the record still shows
    #: WHICH skills were withheld — a no-skill run must not look like a task that has no skills.
    inject_skills: bool = True
    workspace_files: list[str] = field(default_factory=list)  # files/dirs copied in (dest = basename)
    workspace_map: dict[str, str] = field(default_factory=dict)  # src -> explicit relative dest

    # Which pipeline this spec runs through:
    #   "skillbench" — task package with its own environment/Dockerfile (faithful benchmark mode)
    #   "generic"    — user skill/task; shared recipe (docker_base + docker_setup) or local
    mode: str = "generic"

    # Docker sandbox config (skillbench: per-task Dockerfile; generic: base image + setup script)
    docker_base: str | None = None
    docker_setup: str | None = None
    dockerfile: str | None = None

    # Grading / validation inputs (later phases)
    graders: list[GraderSpec] = field(default_factory=list)  # Step 2-3
    verifier_path: str | None = None  # Step 2 (SkillsBench verifier/)
    oracle_path: str | None = None  # Step 5 (SkillsBench oracle/solve.sh)
    #: Run the LLM judge by default on every graded run (skillbench and generic alike). When the
    #: task defines no llm_rubric of its own, a default one (built-in static rubric, weight 0.3) is
    #: added — IF a judge API key is available. ``--llm-rubric no`` turns all of this off.
    run_llm_rubric: bool = True
    #: Run the ADAPTIVE rubric (step 8): four task-specific tests generated from the instruction +
    #: SKILL.md, judged blind, one call per test. Recorded and displayed but weight 0 in the reward
    #: until it proves itself against static. ``--adaptive-rubric no`` turns it off.
    run_adaptive_rubric: bool = True
    #: The FIXED-rubric judge — same rubric text for every task (rubrics/fixed.md, else the
    #: built-in). The baseline rung of the ladder: fixed -> generated static -> adaptive.
    #: Weight 0 in the reward. ``--fixed-rubric no`` / grading.fixed_rubric turn it off.
    run_fixed_rubric: bool = True
    fixed_rubric_text: str | None = None  # from a grading.fixed_rubric PATH, read at load time
    adaptive_provider: str | None = None  # --adaptive-provider (generator + judge)
    adaptive_model: str | None = None  # --adaptive-model
    #: From the yaml's `grading:` block when a PATH was given instead of yes/no — the rubric text
    #: (static) / criteria JSON (adaptive) read from that file at load time. None = no path given;
    #: the runner then falls back to the rubrics/<task>/ cache or generates.
    static_rubric_text: str | None = None
    adaptive_criteria_json: str | None = None

    # Defaults a config file may carry (CLI flags override these; built-ins fill what's left).
    default_harness: str | None = None  # defaults.agent / defaults.harness in the yaml
    default_trials: int | None = None  # defaults.trials

    # Run settings
    attempts: int = 1
    timeout_sec: int = 300
    cpus: int = 2
    memory_mb: int = 4096
