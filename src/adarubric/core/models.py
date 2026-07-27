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
    num_turns: int | None = None
    duration_api_ms: float | None = None
    cost_usd: float | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
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
    num_turns: int | None = None
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
    model: str | None = None
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
    graded: bool = False  # a grader ran (Step 2+)
    reward: float = 0.0  # weighted grader score 0..1 (0 when not graded)
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
    rubric: str | None = None  # llm_rubric: rubric text or file path
    model: str | None = None  # llm_rubric: model override
    provider: str | None = None  # llm_rubric: "gemini" | "anthropic" | "openai"
    weight: float = 1.0


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

    # Run settings
    attempts: int = 1
    timeout_sec: int = 300
    cpus: int = 2
    memory_mb: int = 4096
