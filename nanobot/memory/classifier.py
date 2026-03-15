"""Decay tier classifier for distilled facts.

Classifies each fact into one of five decay tiers that control TTL-based
lifecycle management. Uses nanobot's LLM provider for classification.
Falls back to ACTIVE on any failure for graceful degradation.

Tiers:
    permanent   — never expires  (identities, decisions, conventions)
    stable      — 90 day TTL     (project descriptions, team relationships)
    active      — 14 day TTL     (current tasks, sprint context)
    session     — 24 hour TTL    (debugging context, temp workarounds)
    checkpoint  — 4 hour TTL     (pre-flight state saves)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from nanobot.memory.db.schema import DecayTier

if TYPE_CHECKING:
    from nanobot.config.schema import DecayTTLConfig
    from nanobot.providers.base import LLMProvider

# ── Constants ──────────────────────────────────────────────

_VALID_TIERS = frozenset(t.value for t in DecayTier)

_SYSTEM_PROMPT = """\
You are a memory decay classifier for a personal knowledge management system.

Given a structured fact (category, entity, key, value, and optional rationale), \
classify it into exactly ONE of the following decay tiers:

**permanent** — Facts that should never expire:
  - Names, birthdays, identities, personal details
  - Architectural decisions, API endpoints, system design choices
  - Conventions ("always do X", "never do Y")

**stable** — Facts that remain relevant for ~90 days:
  - Project descriptions, tech stack mentions
  - Team relationships, role descriptions

**active** — Facts relevant for ~14 days:
  - Current tasks, sprint work, deadlines

**session** — Facts relevant for ~24 hours:
  - Debugging context, error messages, stack traces
  - Temporary workarounds

**checkpoint** — Facts relevant for ~4 hours:
  - Pre-operation state captures before risky changes

Respond with ONLY the tier name as a single lowercase word."""

_USER_TEMPLATE = """\
Category: {category}
Entity: {entity}
Key: {key}
Value: {value}
Rationale: {rationale}"""

_BATCH_USER_TEMPLATE = """\
Classify each fact into a decay tier. \
Respond with a JSON array of tier names in the same order. \
Example: ["permanent", "active", "session"]

Facts:
{facts_block}"""


# ── Result type ────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    decay_tier: DecayTier
    ttl_seconds: int | None


# ── TTL computation ────────────────────────────────────────


def compute_ttl_seconds(
    tier: DecayTier,
    decay_config: DecayTTLConfig | None = None,
) -> int | None:
    """Map a DecayTier to its TTL in seconds. Returns None for permanent."""
    from nanobot.config.schema import DecayTTLConfig
    cfg = decay_config or DecayTTLConfig()

    match tier:
        case DecayTier.permanent:
            return None
        case DecayTier.stable:
            return cfg.stable_ttl_days * 86_400
        case DecayTier.active:
            return cfg.active_ttl_days * 86_400
        case DecayTier.session:
            return cfg.session_ttl_hours * 3_600
        case DecayTier.checkpoint:
            return cfg.checkpoint_ttl_hours * 3_600


def _parse_tier(raw: str) -> DecayTier | None:
    """Parse a raw LLM response string into a DecayTier."""
    cleaned = raw.strip().lower().strip('"').strip("'").strip(".")
    if cleaned in _VALID_TIERS:
        return DecayTier(cleaned)
    for tier_val in _VALID_TIERS:
        if tier_val in cleaned:
            return DecayTier(tier_val)
    return None


# ── Single-fact classification ─────────────────────────────


async def classify_decay_tier(
    category: str,
    entity: str | None,
    key: str,
    value: str,
    rationale: str | None,
    provider: LLMProvider,
    model: str,
    decay_config: DecayTTLConfig | None = None,
) -> ClassificationResult:
    """Classify a single fact using the LLM provider."""
    user_msg = _USER_TEMPLATE.format(
        category=category,
        entity=entity or "(none)",
        key=key,
        value=value,
        rationale=rationale or "(none)",
    )

    try:
        response = await provider.chat(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            model=model,
            max_tokens=64,
            temperature=0.0,
        )
        tier = _parse_tier(response.content or "")
        if tier is None:
            logger.warning("Could not parse tier from LLM response: {} — defaulting to ACTIVE", response.content)
            tier = DecayTier.active
    except Exception as exc:
        logger.error("Classification failed for [{}/{}/{}]: {} — defaulting to ACTIVE", category, entity, key, exc)
        tier = DecayTier.active

    ttl = compute_ttl_seconds(tier, decay_config)
    return ClassificationResult(decay_tier=tier, ttl_seconds=ttl)


# ── Batch classification ──────────────────────────────────


@dataclass(frozen=True, slots=True)
class FactInput:
    """Minimal fact representation for batch classification."""
    category: str
    entity: str | None
    key: str
    value: str
    rationale: str | None


async def classify_decay_tier_batch(
    facts: list[FactInput],
    provider: LLMProvider,
    model: str,
    decay_config: DecayTTLConfig | None = None,
) -> list[ClassificationResult]:
    """Classify multiple facts in a single LLM call."""
    if not facts:
        return []

    if len(facts) == 1:
        f = facts[0]
        return [await classify_decay_tier(
            f.category, f.entity, f.key, f.value, f.rationale,
            provider, model, decay_config,
        )]

    lines = []
    for i, f in enumerate(facts, 1):
        lines.append(
            f"{i}. Category: {f.category} | Entity: {f.entity or '(none)'} | "
            f"Key: {f.key} | Value: {f.value} | Rationale: {f.rationale or '(none)'}"
        )

    user_msg = _BATCH_USER_TEMPLATE.format(facts_block="\n".join(lines))
    default_tier = DecayTier.active
    default_ttl = compute_ttl_seconds(default_tier, decay_config)
    fallback = ClassificationResult(decay_tier=default_tier, ttl_seconds=default_ttl)

    try:
        response = await provider.chat(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            model=model,
            max_tokens=max(128, len(facts) * 20),
            temperature=0.0,
        )

        tiers_raw = json.loads(response.content or "[]")
        if not isinstance(tiers_raw, list):
            logger.warning("Batch response is not a list — defaulting all to ACTIVE")
            return [fallback] * len(facts)

        results: list[ClassificationResult] = []
        for i, f in enumerate(facts):
            if i < len(tiers_raw):
                tier = _parse_tier(str(tiers_raw[i]))
                if tier is None:
                    tier = default_tier
            else:
                tier = default_tier
            ttl = compute_ttl_seconds(tier, decay_config)
            results.append(ClassificationResult(decay_tier=tier, ttl_seconds=ttl))

        return results

    except Exception as exc:
        logger.error("Batch classification failed for {} facts: {} — defaulting all to ACTIVE", len(facts), exc)
        return [fallback] * len(facts)
