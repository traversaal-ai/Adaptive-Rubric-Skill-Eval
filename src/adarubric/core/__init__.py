"""Core layer — the stable center: pure data models and the abstract contracts.

Depends on nothing else in the package; everything else depends on it.
"""

from adarubric.core.contracts import (
    Grader,
    Harness,
    LLM,
    ProgressReporter,
    RunCommand,
    Sandbox,
)
from adarubric.core.models import (
    EvalReport,
    EvalSpec,
    GraderResult,
    GraderSpec,
    ProgressEvent,
    RunMeta,
    RunOutput,
    ShellResult,
    SkillTrigger,
    SkillUsage,
    Timing,
    TranscriptEntry,
    Trial,
    TrialStage,
    TriggerSource,
    Usage,
    WorkspaceChanges,
)

__all__ = [
    # contracts
    "Harness",
    "Sandbox",
    "Grader",
    "LLM",
    "ProgressReporter",
    "RunCommand",
    # data models
    "Trial",
    "TrialStage",
    "EvalReport",
    "EvalSpec",
    "GraderResult",
    "GraderSpec",
    "ProgressEvent",
    "RunMeta",
    "RunOutput",
    "ShellResult",
    "SkillTrigger",
    "SkillUsage",
    "Timing",
    "TranscriptEntry",
    "TriggerSource",
    "Usage",
    "WorkspaceChanges",
]
