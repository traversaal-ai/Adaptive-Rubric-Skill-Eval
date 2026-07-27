"""Sandbox registry — maps a sandbox name to its implementation.

The plugin seam for execution environments.
"""

from __future__ import annotations

from typing import Callable

from adarubric.core.contracts import Sandbox
from adarubric.sandboxes.docker import DockerSandbox
from adarubric.sandboxes.local import LocalSandbox

_REGISTRY: dict[str, Callable[[], Sandbox]] = {
    "local": LocalSandbox,
    "docker": DockerSandbox,
}


def sandbox_names() -> list[str]:
    """Names of all registered sandboxes."""
    return list(_REGISTRY)


def create_sandbox(name: str) -> Sandbox:
    """Instantiate a sandbox by name. Raises ``ValueError`` if unknown."""
    factory = _REGISTRY.get(name)
    if factory is None:
        available = ", ".join(sandbox_names())
        raise ValueError(f'Unknown sandbox "{name}". Available sandboxes: {available}')
    return factory()
