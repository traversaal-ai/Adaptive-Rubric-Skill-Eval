"""Harness registry — maps a harness name to its implementation.

The plugin seam for coding agents: a new harness is a new module here + one line in ``_REGISTRY``.
"""

from __future__ import annotations

from typing import Callable

from adarubric.core.contracts import Harness
from adarubric.harnesses.acp import AcpHarness
from adarubric.harnesses.claude import ClaudeHarness
from adarubric.harnesses.codex import CodexHarness
from adarubric.harnesses.gemini import GeminiHarness
from adarubric.harnesses.oracle import OracleHarness
from adarubric.harnesses.together import ClaudeTogetherHarness, CodexTogetherHarness

_REGISTRY: dict[str, Callable[[], Harness]] = {
    "claude-code": ClaudeHarness,
    "gemini-cli": GeminiHarness,
    "codex": CodexHarness,
    # Together AI variants (TogetherLink wrappers: one TOGETHER_API_KEY runs open models through
    # the same harnesses). Beta / unverified - see harnesses/together.py for the caveats.
    "claude-code-together": ClaudeTogetherHarness,
    "codex-together": CodexTogetherHarness,
    # Generic ACP wrapper — drives any Agent Client Protocol agent (configure with --acp-cmd).
    "acp": AcpHarness,
    # Not an agent: runs the task's own reference solution to prove the task can be passed at all.
    # Free (no model, no key) — use it before spending money on agents.
    "oracle": OracleHarness,
    # "opencode" is registered in a later piece.
}


def harness_names() -> list[str]:
    """Names of all registered harnesses."""
    return list(_REGISTRY)


def create_harness(name: str, model: str | None = None) -> Harness:
    """Instantiate a harness by name, optionally pinned to ``model``. Raises ``ValueError`` if unknown."""
    factory = _REGISTRY.get(name)
    if factory is None:
        available = ", ".join(harness_names())
        raise ValueError(f'Unknown harness "{name}". Available harnesses: {available}')
    return factory(model=model)
