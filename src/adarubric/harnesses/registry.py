"""Harness registry — maps a harness name to its implementation.

The plugin seam for coding agents: a new harness is a new module here + one line in ``_REGISTRY``.
Ported from skillgrade `src/agents/registry.ts`.
"""

from __future__ import annotations

from typing import Callable

from adarubric.core.contracts import Harness
from adarubric.harnesses.claude import ClaudeHarness
from adarubric.harnesses.codex import CodexHarness
from adarubric.harnesses.gemini import GeminiHarness

_REGISTRY: dict[str, Callable[[], Harness]] = {
    "claude-code": ClaudeHarness,
    "gemini-cli": GeminiHarness,
    "codex": CodexHarness,
    # "acp" and "opencode" are registered in a later piece.
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
