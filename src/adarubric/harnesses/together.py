"""Together AI variants of the claude-code and codex harnesses, via TogetherLink.

TogetherLink (https://togetherlink.vercel.app — Together's own open-source tool) installs thin
wrapper commands that run the REAL CLIs through a local translation proxy pointed at Together's
API: ``tclaude`` wraps Claude Code (Anthropic protocol → Together) and ``tcodex`` wraps Codex
(Responses API → Together's chat completions). Same binaries, same output formats — so these
harnesses are subclasses that swap only the command name, the API key, and the installer.

Why this exists: with one TOGETHER_API_KEY the SAME open model (Kimi, GLM, Qwen, DeepSeek …) can
be run through BOTH the claude-code and codex harnesses — isolating the harness/skill-discovery
variable, which cross-vendor runs (each CLI on its own vendor's model) never can.

⚠️ STATUS: UNVERIFIED. TogetherLink marks both integrations Beta, and no live run has gone
through these harnesses yet (needs a TOGETHER_API_KEY). Known caveats before trusting results:
- the wrappers prompt for the key on FIRST launch — the installer here only verifies the command
  exists (running it during a docker build could hang the build);
- reported cost fields will be wrong or empty (pricing tables don't know Together models) —
  report tokens, not dollars;
- pin ``--model`` to a Together model id; the CLIs' vendor defaults don't exist on Together.
"""

from __future__ import annotations

from adarubric.harnesses.claude import ClaudeHarness
from adarubric.harnesses.codex import CodexHarness

#: Together's one-line installer (installs Bun if needed; binaries land in ~/.togetherlink/bin).
#: Deliberately verified with `command -v`, never by RUNNING a wrapper — first launch is
#: interactive (asks for the key) and would hang a docker build.
_TOGETHERLINK_INSTALL = (
    "curl -fsSL https://togetherlink.vercel.app/install.sh | sh "
    "&& ln -sf /root/.togetherlink/bin/* /usr/local/bin/ "
)


class ClaudeTogetherHarness(ClaudeHarness):
    """Claude Code on Together models: identical harness, `tclaude` instead of `claude`."""

    name = "claude-code-together"
    cli = "tclaude"
    env_keys = ("TOGETHER_API_KEY",)
    # Claude Code itself must be installed too — tclaude wraps the real binary.
    docker_install = (
        "curl -fsSL https://claude.ai/install.sh | bash "
        "&& ln -sf /root/.local/bin/claude /usr/local/bin/claude "
        "&& " + _TOGETHERLINK_INSTALL + "&& command -v tclaude"
    )


class CodexTogetherHarness(CodexHarness):
    """Codex on Together models: identical harness, `tcodex` instead of `codex`.

    The parent's best-effort `login --with-api-key` step is guarded on $OPENAI_API_KEY, which this
    harness never injects — so it is a clean no-op here, exactly as intended.
    """

    name = "codex-together"
    cli = "tcodex"
    env_keys = ("TOGETHER_API_KEY",)
    docker_install = (
        CodexHarness.docker_install.rsplit("&& codex --version", 1)[0]
        + "&& " + _TOGETHERLINK_INSTALL + "&& command -v tcodex"
    )
