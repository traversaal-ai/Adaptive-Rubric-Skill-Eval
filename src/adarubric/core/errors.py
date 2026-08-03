"""Typed errors that distinguish *our* infrastructure failing from *the agent* failing.

The distinction matters for scoring. A trial recorded with ``success: false`` means the agent was
given a fair shot and did not complete the task — that number feeds the pass rate. If the Docker
daemon is down, or the harness CLI is missing, the agent never ran at all; recording that as a
failed trial silently poisons the metric and makes the dashboard blame the model for a laptop
problem. Those conditions raise :class:`SandboxUnavailable` instead, which aborts the run *before*
any artifact is written.
"""

from __future__ import annotations


class AdaRubricError(RuntimeError):
    """Base class for AdaRubric's own errors."""


class SandboxUnavailable(AdaRubricError):
    """The execution environment isn't usable — the run cannot start (or cannot continue).

    Raised by :meth:`Sandbox.preflight` (before anything is created) and by sandbox operations that
    discover the environment died mid-run. Never recorded as a trial result: the CLI prints
    ``message`` and exits non-zero, leaving no output behind.

    ``message`` should be a short, actionable sentence — what's wrong and what to do about it.
    """
