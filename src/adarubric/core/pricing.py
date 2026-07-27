"""Cost estimation — turn token counts into a USD estimate when a harness doesn't report cost.

The reference prices below are a starting point, not a contract: LLM prices change and each harness
may run a different underlying model. Treat this table as editable config — override entries, or add
your own via ``register_price`` / a project pricing file. An unknown model yields ``None`` (no
estimate) rather than a fabricated number.

Anthropic prices: USD per 1M tokens, from the Claude API reference (cached 2026-06-24). Verify
against current pricing before relying on the numbers.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPrice:
    """USD per 1,000,000 tokens."""

    input_per_mtok: float
    output_per_mtok: float


# Keyed by a model-id prefix; lookup matches by longest prefix (handles date/variant suffixes and
# provider prefixes like "anthropic.claude-opus-4-8"). VERIFY before trusting — prices drift.
PRICES: dict[str, ModelPrice] = {
    # --- Anthropic (from the Claude API reference, cached 2026-06-24) ---
    "claude-opus-4-8": ModelPrice(5.0, 25.0),
    "claude-opus-4-7": ModelPrice(5.0, 25.0),
    "claude-opus-4-6": ModelPrice(5.0, 25.0),
    "claude-sonnet-5": ModelPrice(3.0, 15.0),  # intro 2.0/10.0 through 2026-08-31
    "claude-sonnet-4-6": ModelPrice(3.0, 15.0),
    "claude-haiku-4-5": ModelPrice(1.0, 5.0),
    "claude-fable-5": ModelPrice(10.0, 50.0),
    # --- OpenAI / Google: fill in the models your codex / gemini-cli harnesses actually use ---
    # "gpt-5": ModelPrice(?, ?),
    # "gemini-3-pro": ModelPrice(?, ?),
}


def register_price(model_prefix: str, input_per_mtok: float, output_per_mtok: float) -> None:
    """Add or override a price entry at runtime (e.g. from a project pricing file)."""
    PRICES[model_prefix] = ModelPrice(input_per_mtok, output_per_mtok)


def _lookup(model: str | None) -> ModelPrice | None:
    if not model:
        return None
    # Longest-prefix match so "anthropic.claude-opus-4-8-2026..." still resolves.
    best: tuple[int, ModelPrice] | None = None
    for prefix, price in PRICES.items():
        if prefix in model and (best is None or len(prefix) > best[0]):
            best = (len(prefix), price)
    return best[1] if best else None


def estimate_cost(
    model: str | None, input_tokens: int | None, output_tokens: int | None
) -> float | None:
    """Estimate USD cost from token counts. Returns ``None`` if the model or tokens are unknown."""
    price = _lookup(model)
    if price is None or input_tokens is None or output_tokens is None:
        return None
    return (
        input_tokens / 1_000_000 * price.input_per_mtok
        + output_tokens / 1_000_000 * price.output_per_mtok
    )
