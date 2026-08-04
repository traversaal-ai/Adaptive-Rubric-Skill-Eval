"""Cost estimation — turn token counts into a USD estimate when a harness doesn't report cost.

The reference prices below are a starting point, not a contract: LLM prices change and each harness
may run a different underlying model. Treat this table as editable config — override entries, or add
your own via ``register_price`` / a project pricing file. An unknown model yields ``None`` (no
estimate) rather than a fabricated number.

Anthropic prices: USD per 1M tokens, from the Claude API reference (cached 2026-06-24). Verify
against current pricing before relying on the numbers.
"""

from __future__ import annotations

import re
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
    # --- Google (list prices, cached 2026-08; VERIFY at ai.google.dev/pricing) ---
    # Gemini 2.5 Pro is tiered: these are the <=200k-prompt rates. Long prompts (>200k) bill higher
    # (~2.50/15.00), so a big-context run is UNDER-estimated here. Reported cost always wins when a
    # harness provides one; gemini-cli does not, so every gemini figure is an estimate.
    "gemini-2.5-pro": ModelPrice(1.25, 10.0),
    "gemini-2.5-flash": ModelPrice(0.30, 2.50),
    # --- OpenAI (list prices effective 2026-07-30; VERIFY at openai.com/api/pricing) ---
    # Cached input bills at 10% of these rates and the Batch API halves them — neither is modelled
    # here, so a cache-heavy run is OVER-estimated.
    "gpt-5.6-sol": ModelPrice(5.0, 30.0),
    "gpt-5.6-terra": ModelPrice(2.0, 12.0),
    "gpt-5.6-luna": ModelPrice(0.20, 1.20),
    # Legacy, retiring 2026-08-31. Kept so older runs still cost out; prices are the pre-5.6 rates.
    "gpt-5.4-mini": ModelPrice(0.25, 2.0),
    "gpt-5.4": ModelPrice(1.25, 10.0),
    # `gpt-5-codex` is retired — the API now answers 404 "Model not found" for it. Left in place only
    # so runs recorded before the retirement still resolve to a cost.
    "gpt-5-codex": ModelPrice(1.25, 10.0),
    "gpt-5": ModelPrice(1.25, 10.0),
}


def register_price(model_prefix: str, input_per_mtok: float, output_per_mtok: float) -> None:
    """Add or override a price entry at runtime (e.g. from a project pricing file)."""
    PRICES[model_prefix] = ModelPrice(input_per_mtok, output_per_mtok)


#: Names that are a *setting*, not a model. gemini reports "auto" (its recommended mode, which routes
#: each prompt to Flash or Pro), and claude-code-acp reports "Default (recommended)". Both are honest
#: — but there is no price for a routing mode, and matching one to a price table would be fiction.
#: Word-by-word, so "Default (recommended)" is caught as well as a bare "default".
_NOT_A_MODEL = ("auto", "default", "recommended", "unknown", "none", "unspecified")
#: Whole-string forms that survive word-splitting, e.g. "n/a" -> "na".
_NOT_A_MODEL_FLAT = ("na", "tbd", "")


def is_specific_model(model: str | None) -> bool:
    """False for routing labels like ``auto`` — truthful names that cannot be priced."""
    if not model or not model.strip():
        return False
    low = model.lower()
    if re.sub(r"[^a-z0-9]+", "", low) in _NOT_A_MODEL_FLAT:
        return False
    words = re.sub(r"[^a-z0-9]+", " ", low).split()
    return bool(words) and not all(word in _NOT_A_MODEL for word in words)


def _lookup(model: str | None) -> ModelPrice | None:
    if not is_specific_model(model):
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
