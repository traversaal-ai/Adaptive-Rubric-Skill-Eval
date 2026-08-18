"""The contracts (ports) that adapters implement.

Four abstract bases, in the vocabulary of the tool:

- ``Harness`` — a coding-agent CLI (Claude Code, Gemini CLI, Codex, …).  Adapters: harnesses/
- ``Sandbox`` — where a run executes (local host or a Docker container).  Adapters: sandboxes/
- ``Grader``  — scores a completed run.  (Step 2+)                         Adapters: grading/
- ``LLM``     — a text-in/text-out model backend.  (Step 3+)

Imports only the data models — never an adapter.
"""

from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from typing import Callable, Protocol

from adarubric.core.models import (
    EvalSpec,
    GraderResult,
    GraderSpec,
    ProgressEvent,
    RunOutput,
    ShellResult,
    TranscriptEntry,
)


#: Canonical location (relative to the workspace/workdir) where the sandbox writes the instruction.
#: Harnesses feed it to their CLI via stdin redirection — no shell escaping, cross-platform,
#: container-safe (replaces the old python/base64 shim).
PROMPT_RELPATH = ".adarubric/prompt.md"

#: AdaRubric control files/dirs that define the task or its grading. When a skill folder is injected
#: into an agent's discovery dir, these are STRIPPED — they must never be visible to the agent, or
#: the eval (instruction source, grader definition) would leak into the workspace. This is the
#: isolation guarantee for the "drop SKILL.md + grader.yaml in one folder" convention.
SKILL_INJECT_IGNORE = (
    "grader.yaml", "grader.yml",
    "adarubric.yaml", "adarubric.yml",
    "eval.yaml", "eval.yml",
    "TASK.md",
    ".adarubric",
)


class RunCommand(Protocol):
    """Callback that runs a shell command in the attempt's workspace and returns its result.

    The active :class:`Sandbox` supplies this, so a :class:`Harness` never needs to know whether it
    is running locally or inside Docker.
    """

    def __call__(
        self, command: str, env: dict[str, str] | None = None
    ) -> ShellResult: ...


class Harness(ABC):
    """One coding-agent CLI under test.

    The harness is chosen *explicitly* by name (no key auto-detection). Each subclass declares which
    environment variable(s) it needs; the runner resolves only those from the environment and injects
    only those into the sandbox, failing fast with a clear message if any are missing.
    """

    #: Registry id, e.g. "claude-code". Set on each concrete subclass.
    name: str = ""
    #: The executable invoked inside the sandbox, e.g. "claude". Used for pre-flight checks.
    cli: str = ""
    #: The model this harness is pinned to for a run (e.g. "claude-opus-4-8"). Set via ``--model``.
    #: ``None`` → let the CLI use its own default. Passed to the CLI as ``--model``/``-m`` by ``run``.
    model: str | None = None
    #: Environment variable(s) this harness requires (e.g. ["ANTHROPIC_API_KEY"]).
    env_keys: tuple[str, ...] = ()
    #: The skill-discovery dir(s) THIS harness actually searches, relative to its base (the workspace
    #: for local; the container home for docker). Each injected skill is copied into each of these so
    #: the harness can genuinely discover it — required for a FAIR skill_opened metric across harnesses.
    #: The default is a broad superset; concrete harnesses narrow it to their real path(s).
    skill_dirs: tuple[str, ...] = (".claude/skills", ".agents/skills")
    #: Shell snippet that installs this harness's CLI inside a (debian-based) docker image.
    #: Used by the DockerSandbox overlay build. Empty → nothing to install (e.g. test doubles).
    docker_install: str = ""
    #: Optional live-narration sink, set by the CLI in ``--verbose`` mode. Harnesses that watch the
    #: agent act in real time (ACP) report tool calls / messages here as they happen; subprocess
    #: harnesses don't need it — their CLI's output is streamed live by the sandbox instead.
    echo: "Callable[[str], None] | None" = None

    def __init__(self, model: str | None = None) -> None:
        #: Instance-level model pin (overrides the class default) — ``None`` keeps the CLI default.
        self.model = model

    @abstractmethod
    def run(
        self, instruction: str, workspace: str, run_command: RunCommand
    ) -> RunOutput:
        """Run the agent on ``instruction`` inside ``workspace``; capture and return the output."""
        raise NotImplementedError


class Sandbox(ABC):
    """An isolated place to execute attempts (local host or a container)."""

    #: Registry name, e.g. "local" / "docker".
    name: str = ""
    #: Optional live-activity sink, set by the CLI (to the StatusReporter's ``note``). Lets a sandbox
    #: report what it's doing — building the image, copying files, running container commands — to a
    #: live dashboard. ``None`` → a no-op; the sandbox never depends on it being set.
    activity: "Callable[[str], None] | None" = None

    #: Optional RAW-output sink (``--verbose``): full docker build / agent CLI output, streamed live
    #: line by line. Separate from ``activity`` (milestones) because it is high-volume — it goes to
    #: the terminal only, never into status.json (which is rewritten on every activity line).
    log: "Callable[[str], None] | None" = None

    def _note(self, msg: str) -> None:
        """Emit a live activity line if a sink is attached (safe no-op otherwise)."""
        cb = self.activity
        if cb is not None:
            try:
                cb(msg)
            except Exception:  # noqa: BLE001 - activity reporting must never break a run
                pass

    def _log(self, line: str) -> None:
        """Emit one raw output line to the verbose sink (safe no-op when none is attached)."""
        cb = self.log
        if cb is not None and line:
            try:
                cb(line)
            except Exception:  # noqa: BLE001 - the live view must never break a run
                pass

    def preflight(self) -> None:
        """Verify this sandbox's infrastructure is usable, BEFORE any output is created.

        Called once by the CLI up front. Raises
        :class:`~adarubric.core.errors.SandboxUnavailable` with a short, actionable message when the
        environment isn't ready (Docker daemon down, CLI missing). That aborts the run cleanly
        instead of recording a bogus failed trial — an environment problem on *our* side is not an
        agent failure and must never land in the results. Default: nothing to check.
        """
        return None

    def prepare(
        self, spec: EvalSpec, harness: "Harness", env: dict[str, str] | None = None
    ) -> str | None:
        """One-time setup shared across attempts (e.g. build the Docker image, including the
        harness CLI overlay). Idempotent — safe to call before every attempt. Optional."""
        return None

    @abstractmethod
    def setup(
        self, spec: EvalSpec, harness: "Harness", env: dict[str, str] | None = None
    ) -> str:
        """Create an isolated workspace for one attempt.

        Copies ``spec.workspace_files``/``workspace_map``, writes the instruction to the canonical
        prompt file (``.adarubric/prompt.md``), and injects each skill into the *running harness's*
        ``skill_dirs`` — relative to the workspace root for local, the container HOME for docker
        (fair skill_opened). Returns a workspace handle (a path for local, a container id for docker).
        """
        raise NotImplementedError

    @abstractmethod
    def run_command(
        self, workspace: str, command: str, env: dict[str, str] | None = None
    ) -> ShellResult:
        """Run ``command`` inside ``workspace`` and capture stdout/stderr/exit code."""
        raise NotImplementedError

    def popen(
        self, workspace: str, command: str, env: dict[str, str] | None = None
    ) -> "subprocess.Popen[str]":
        """Start a LONG-LIVED process in ``workspace`` with pipes on stdin/stdout/stderr.

        ``run_command`` is fire-and-forget: send a command, wait, read the output. That cannot host a
        conversation. ACP agents need the opposite — a process that stays alive while both sides
        exchange messages — so this is the seam for them. Local runs it with ``cwd``; Docker wraps it
        in ``docker exec -i``, which is what lets an ACP agent run against a container.
        """
        raise NotImplementedError(f"{self.name} sandbox cannot host an interactive process")

    def stage(self, workspace: str, host_src: str, dest: str) -> None:
        """Copy a host path INTO the sandbox at ``dest`` — used to place the grader/verifier AFTER
        the agent has finished (so the agent never sees it). ``dest`` is relative to the workspace
        for local, and an absolute container path for docker (e.g. ``/verifier``)."""
        raise NotImplementedError

    @abstractmethod
    def export_workspace(self, workspace: str, dest_dir: str) -> None:
        """Copy the attempt's final workspace out to ``dest_dir`` (the output folder).

        Local copies the directory; Docker uses ``docker cp``. Called after ``run`` and before
        ``cleanup`` so the agent's produced/changed files are preserved.
        """
        raise NotImplementedError

    @abstractmethod
    def list_files(self, workspace: str) -> dict[str, str]:
        """Return ``{relative_path: content_hash}`` for every file in ``workspace``.

        The runner snapshots this right after ``setup`` and again after ``run`` to compute the
        created / modified / deleted change manifest.
        """
        raise NotImplementedError

    @abstractmethod
    def cleanup(self, workspace: str) -> None:
        """Tear down a single attempt's workspace."""
        raise NotImplementedError

    def teardown(self) -> None:
        """One-time teardown after all attempts (e.g. remove the image). Optional."""
        return None

    def diagnose(self, workspace: str) -> str:
        """Optional diagnostics captured when an attempt fails."""
        return ""


class Grader(ABC):
    """Scores a completed attempt's workspace. Implementations arrive in Step 2 / Step 3."""

    name: str = ""

    @abstractmethod
    def grade(
        self,
        workspace: str,
        sandbox: Sandbox,
        grader_spec: GraderSpec,
        spec: EvalSpec,
        transcript: list[TranscriptEntry],
        env: dict[str, str] | None = None,
    ) -> GraderResult:
        raise NotImplementedError


class LLM(ABC):
    """A text-in/text-out model backend (gemini | anthropic | openai). Used from Step 3 / Step 7."""

    name: str = ""

    @abstractmethod
    def complete(self, prompt: str, model: str | None = None) -> str:
        raise NotImplementedError


class ProgressReporter(ABC):
    """A sink for runner lifecycle events — the seam for live tracking (terminal / web dashboard).

    The runner emits :class:`ProgressEvent`s at each stage; concrete reporters render them (a live
    console table, a status.json on disk, an SSE web push). Adapters live in ``reporting/``.

    MUST be thread-safe: with parallel attempts, ``emit`` is called from multiple worker threads.
    """

    @abstractmethod
    def emit(self, event: ProgressEvent) -> None:
        raise NotImplementedError

    def close(self) -> None:
        """Flush / tear down (stop a live display, close a file/socket). Optional."""
        return None
