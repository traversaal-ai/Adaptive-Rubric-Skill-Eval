"""Live status — persist run progress to ``status.json`` as it happens, for a live dashboard.

The runner emits a ``ProgressEvent`` at each stage; :class:`StatusReporter` writes them to disk
immediately, plus a live **activity feed** of what the sandbox is doing (docker build, file staging,
container commands) via :meth:`note`. A watching dashboard (``dashboard/generate.py --watch``) renders
the run *as it happens* — current stage, and the activity feed — instead of a manual, after-the-fact
snapshot.

:class:`FanReporter` forwards each event to several reporters (e.g. the console + this file writer).
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from adarubric.core.contracts import ProgressReporter
from adarubric.core.models import ProgressEvent

_MAX_ACTIVITY = 400  # keep the feed bounded


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StatusReporter(ProgressReporter):
    """Writes a live ``status.json`` (current stage per trial + a shared activity feed)."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._current: str | None = None  # key of the trial currently running (for activity attribution)
        self._state: dict = {"updated_at": _now(), "running": True, "activity": [], "trials": {}}

    @staticmethod
    def _key(ev: ProgressEvent) -> str:
        return f"{ev.harness}/{ev.task}/attempt-{ev.attempt}/trial-{ev.trial}"

    def emit(self, ev: ProgressEvent) -> None:
        with self._lock:
            if ev.trial is not None:
                key = self._key(ev)
                self._current = key
                t = self._state["trials"].setdefault(
                    key,
                    {
                        "harness": ev.harness, "task": ev.task, "attempt": ev.attempt,
                        "trial": ev.trial, "stage": None, "started_at": ev.timestamp,
                        "updated_at": ev.timestamp, "reward": None, "done": False,
                        "success": None, "error": None,
                    },
                )
                if ev.stage is not None:
                    t["stage"] = ev.stage.value
                t["updated_at"] = ev.timestamp
                if ev.type == "trial_finished":
                    t["done"] = True
                    t["reward"] = ev.reward
                    if ev.meta is not None:
                        t["success"] = ev.meta.success
                        t["error"] = ev.meta.error
                    self._current = None
            if ev.type == "attempt_finished":
                self._current = None
            self._write()

    def note(self, msg: str) -> None:
        """Record a live activity line — called by the sandbox (docker build / copy / exec / staging)."""
        with self._lock:
            ts = _now()
            self._state["activity"].append({"ts": ts, "run": self._current, "msg": msg})
            self._state["activity"] = self._state["activity"][-_MAX_ACTIVITY:]
            # Also keep it on the trial itself, so a finished run's page can show what was built/copied.
            trial = self._state["trials"].get(self._current) if self._current else None
            if trial is not None:
                trial.setdefault("activity", []).append({"ts": ts, "msg": msg})
                trial["activity"] = trial["activity"][-200:]
            self._write()

    def close(self) -> None:
        with self._lock:
            self._state["running"] = False
            self._write()

    def _write(self) -> None:
        self._state["updated_at"] = _now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self._state, default=str), encoding="utf-8")
        tmp.replace(self.path)  # atomic swap so a watcher never reads a half-written file


class FanReporter(ProgressReporter):
    """Forwards every event/close to several reporters (e.g. terminal + status file)."""

    def __init__(self, *reporters: ProgressReporter | None) -> None:
        self._reporters = [r for r in reporters if r is not None]

    def emit(self, event: ProgressEvent) -> None:
        for r in self._reporters:
            r.emit(event)

    def close(self) -> None:
        for r in self._reporters:
            try:
                r.close()
            except Exception:  # noqa: BLE001 - one reporter's cleanup must not break others
                pass
