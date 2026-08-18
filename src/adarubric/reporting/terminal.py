"""TerminalReporter — a minimal thread-safe live progress printer for the CLI.

Prints stage transitions as they happen. A richer `rich`-based live table can replace this later
without touching the runner (both consume the same ProgressEvent stream).
"""

from __future__ import annotations

import threading

import typer

from adarubric.core.contracts import ProgressReporter
from adarubric.core.models import ProgressEvent


class TerminalReporter(ProgressReporter):
    def __init__(self) -> None:
        self._lock = threading.Lock()

    def note(self, msg: str) -> None:
        """One live activity/log line (--verbose), indented under the current stage dot.

        Thread-safe: docker build output, the agent's stdout and stderr, and grading notes all
        arrive from different threads and must not interleave mid-line.
        """
        with self._lock:
            for line in (str(msg).splitlines() or [""]):
                typer.secho(f"      | {line}", dim=True)

    def emit(self, event: ProgressEvent) -> None:
        with self._lock:
            label = f"{event.harness}/{event.task} a{event.attempt}"
            if event.trial is not None:
                label += f"/t{event.trial}"
            if event.type == "attempt_started":
                typer.echo(f"> {event.harness}/{event.task} attempt {event.attempt}")
            elif event.type == "trial_started":
                typer.echo(f"  > trial {event.trial}")
            elif event.type == "stage_changed" and event.stage is not None:
                typer.echo(f"      . {event.stage.value}")
            elif event.type == "trial_finished":
                stage = event.stage.value if event.stage else "?"
                reward = f"  reward={event.reward:.2f}" if event.reward is not None else ""
                typer.echo(f"  = trial {event.trial}: {stage}{reward}")
            elif event.type == "attempt_finished":
                typer.echo(f"= {event.harness}/{event.task} attempt {event.attempt} done")
