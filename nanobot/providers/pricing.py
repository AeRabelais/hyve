"""Model pricing tables for cost calculation.

Provides per-token pricing for common LLM models. Used by the agent loop
to populate ``cost_usd`` in ``usage.tracked`` events.

Pricing is **per million tokens** (USD). Sourced from provider websites.

Lookup strategy: exact match first, then longest-prefix match, which
allows ``anthropic/claude-sonnet-4-20250514`` to match ``anthropic/claude-sonnet-4``.

Updated: 2025-03.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """Per-million-token pricing for a model."""

    input_per_m: float  # USD per 1 M input tokens
    output_per_m: float  # USD per 1 M output tokens
    cache_read_per_m: float = 0.0  # USD per 1 M cached input tokens


# ── Pricing table ──────────────────────────────────────────
# Longest prefix wins during lookup.

_PRICING_TABLE: dict[str, ModelPricing] = {
    # ── Anthropic (direct + via OpenRouter prefix) ──
    "anthropic/claude-opus-4": ModelPricing(15.0, 75.0, 1.5),
    "anthropic/claude-sonnet-4": ModelPricing(3.0, 15.0, 0.3),
    "anthropic/claude-3.5-sonnet": ModelPricing(3.0, 15.0, 0.3),
    "anthropic/claude-3.5-haiku": ModelPricing(0.8, 4.0, 0.08),
    "anthropic/claude-3-opus": ModelPricing(15.0, 75.0, 1.5),
    "anthropic/claude-3-sonnet": ModelPricing(3.0, 15.0, 0.3),
    "anthropic/claude-3-haiku": ModelPricing(0.25, 1.25, 0.03),
    # Bare model names (custom / OpenRouter / litellm shorthand)
    "claude-opus-4": ModelPricing(15.0, 75.0, 1.5),
    "claude-sonnet-4": ModelPricing(3.0, 15.0, 0.3),
    "claude-3.5-sonnet": ModelPricing(3.0, 15.0, 0.3),
    "claude-3.5-haiku": ModelPricing(0.8, 4.0, 0.08),
    "claude-3-opus": ModelPricing(15.0, 75.0, 1.5),
    "claude-3-sonnet": ModelPricing(3.0, 15.0, 0.3),
    "claude-3-haiku": ModelPricing(0.25, 1.25, 0.03),
    # ── OpenAI ──
    "openai/gpt-4o": ModelPricing(2.5, 10.0, 1.25),
    "openai/gpt-4o-mini": ModelPricing(0.15, 0.6, 0.075),
    "openai/gpt-4-turbo": ModelPricing(10.0, 30.0),
    "openai/gpt-4": ModelPricing(30.0, 60.0),
    "openai/o1": ModelPricing(15.0, 60.0),
    "openai/o1-mini": ModelPricing(3.0, 12.0),
    "openai/o1-pro": ModelPricing(150.0, 600.0),
    "openai/o3": ModelPricing(10.0, 40.0),
    "openai/o3-mini": ModelPricing(1.1, 4.4),
    "openai/o4-mini": ModelPricing(1.1, 4.4),
    "gpt-4o": ModelPricing(2.5, 10.0, 1.25),
    "gpt-4o-mini": ModelPricing(0.15, 0.6, 0.075),
    "gpt-4-turbo": ModelPricing(10.0, 30.0),
    "gpt-4": ModelPricing(30.0, 60.0),
    "o1": ModelPricing(15.0, 60.0),
    "o1-mini": ModelPricing(3.0, 12.0),
    "o1-pro": ModelPricing(150.0, 600.0),
    "o3": ModelPricing(10.0, 40.0),
    "o3-mini": ModelPricing(1.1, 4.4),
    "o4-mini": ModelPricing(1.1, 4.4),
    # ── DeepSeek ──
    "deepseek/deepseek-chat": ModelPricing(0.14, 0.28, 0.014),
    "deepseek/deepseek-reasoner": ModelPricing(0.55, 2.19),
    "deepseek-chat": ModelPricing(0.14, 0.28, 0.014),
    "deepseek-reasoner": ModelPricing(0.55, 2.19),
    # ── Google Gemini ──
    "gemini/gemini-2.5-pro": ModelPricing(1.25, 10.0),
    "gemini/gemini-2.5-flash": ModelPricing(0.15, 0.6),
    "gemini/gemini-2.0-flash": ModelPricing(0.1, 0.4),
    "gemini/gemini-1.5-pro": ModelPricing(1.25, 5.0),
    "gemini/gemini-1.5-flash": ModelPricing(0.075, 0.3),
    # ── Groq ──
    "groq/llama-3.3-70b": ModelPricing(0.59, 0.79),
    "groq/llama-3.1-70b": ModelPricing(0.59, 0.79),
    "groq/llama-3.1-8b": ModelPricing(0.05, 0.08),
    "groq/mixtral-8x7b": ModelPricing(0.24, 0.24),
    # ── GitHub Copilot ──
    "github_copilot/gpt-4o": ModelPricing(2.5, 10.0, 1.25),
    "github-copilot/gpt-4o": ModelPricing(2.5, 10.0, 1.25),
    # ── OpenAI Codex ──
    "openai-codex/codex-mini": ModelPricing(1.5, 6.0),
    # ── Moonshot ──
    "moonshot/moonshot-v1-8k": ModelPricing(0.85, 0.85),
    "moonshot/moonshot-v1-32k": ModelPricing(1.7, 1.7),
    "moonshot/moonshot-v1-128k": ModelPricing(4.25, 4.25),
    # ── Zhipu GLM ──
    "zhipu/glm-4-plus": ModelPricing(0.7, 0.7),
    "zhipu/glm-4-flash": ModelPricing(0.007, 0.007),
    # ── MiniMax ──
    "minimax/abab7-chat": ModelPricing(1.4, 1.4),
    # ── SiliconFlow ──
    "siliconflow/deepseek-v3": ModelPricing(0.14, 0.28),
    # ── OpenRouter passthrough — fallback prefixes ──
    "openrouter/anthropic/claude-opus-4": ModelPricing(15.0, 75.0, 1.5),
    "openrouter/anthropic/claude-sonnet-4": ModelPricing(3.0, 15.0, 0.3),
    "openrouter/anthropic/claude-3.5-sonnet": ModelPricing(3.0, 15.0, 0.3),
    "openrouter/openai/gpt-4o": ModelPricing(2.5, 10.0, 1.25),
    "openrouter/openai/o3-mini": ModelPricing(1.1, 4.4),
    "openrouter/google/gemini-2.5-pro": ModelPricing(1.25, 10.0),
    "openrouter/deepseek/deepseek-chat": ModelPricing(0.14, 0.28),
}


def _lookup_pricing(model: str) -> ModelPricing | None:
    """Find pricing by exact match, then longest-prefix match."""
    key = model.lower()

    # Exact match
    if key in _PRICING_TABLE:
        return _PRICING_TABLE[key]

    # Longest prefix match
    best: ModelPricing | None = None
    best_len = 0
    for prefix, pricing in _PRICING_TABLE.items():
        if key.startswith(prefix.lower()) and len(prefix) > best_len:
            best = pricing
            best_len = len(prefix)

    return best


def compute_cost(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float | None:
    """Calculate cost in USD for a single LLM call.

    Uses longest-prefix matching against the pricing table.
    Returns ``None`` if the model has no known pricing.
    """
    pricing = _lookup_pricing(model)
    if pricing is None:
        return None

    cost = (
        (input_tokens / 1_000_000) * pricing.input_per_m
        + (output_tokens / 1_000_000) * pricing.output_per_m
        + (cache_read_tokens / 1_000_000) * pricing.cache_read_per_m
    )
    return round(cost, 8)
