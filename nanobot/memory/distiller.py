"""Distillation pipeline — extracts structured facts from raw events.

The distillation job:
  1. Reads raw memory events since the last distillation run
  2. Groups them by agent_id
  3. Sends each group to the LLM for fact extraction
  4. Classifies decay tiers (decisions/conventions → permanent, others → classifier)
  5. Deduplicates against existing facts
  6. Inserts/updates facts in the database
  7. Records a distillation marker event
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from nanobot.memory.classifier import (
    ClassificationResult,
    FactInput,
    classify_decay_tier_batch,
    compute_ttl_seconds,
)
from nanobot.memory.db.connection import get_engine
from nanobot.memory.db.queries import (
    get_events_since,
    get_last_distillation_time,
    insert_memory_event,
    upsert_fact,
)
from nanobot.memory.db.schema import DecayTier, FactCategory, MemoryEventType

if TYPE_CHECKING:
    from nanobot.config.schema import DecayTTLConfig, MemoryConfig
    from nanobot.providers.base import LLMProvider


# ── Extraction prompt ──────────────────────────────────────

_EXTRACTION_SYSTEM_PROMPT = """\
You are a memory extraction system for a personal knowledge management platform.

Given conversation logs and daily notes from an AI agent, extract structured facts.

For each fact, return a JSON object with these fields:
- "category": one of "person", "project", "decision", "convention", "preference", "task"
- "entity": the subject (person name, project name, etc.) — use null if no specific entity
- "key": what about the entity (e.g. "birthday", "tech_stack", "status")
- "value": the fact itself (concise but complete)
- "rationale": if this is a decision, explain WHY it was made (null otherwise)
- "tags": array of relevant topic tags

Pay special attention to:
- **Decisions with rationale** ("we chose X because Y") → category: "decision"
- **Conventions** ("always do X", "never do Y") → category: "convention"
- **People facts** (roles, preferences, birthdays, relationships)
- **Project facts** (tech stack, architecture, status, goals)

Return a JSON array of fact objects. If no facts can be extracted, return [].
Do NOT wrap the JSON in markdown code fences. Return ONLY the JSON array."""

_EXTRACTION_USER_TEMPLATE = """\
Agent: {agent_id}
Time range: {time_range}

Raw content:
{content}"""

_VALID_CATEGORIES = frozenset(c.value for c in FactCategory)


# ── Result type ────────────────────────────────────────────


@dataclass
class DistillationResult:
    events_processed: int = 0
    facts_extracted: int = 0
    facts_inserted: int = 0
    facts_updated: int = 0
    errors: list[str] = field(default_factory=list)


# ── LLM extraction ────────────────────────────────────────


async def _extract_facts_from_content(
    agent_id: str,
    content: str,
    time_range: str,
    provider: LLMProvider,
    model: str,
) -> list[dict]:
    """Call the LLM to extract structured facts from raw content."""
    user_msg = _EXTRACTION_USER_TEMPLATE.format(
        agent_id=agent_id,
        time_range=time_range,
        content=content,
    )

    response = await provider.chat(
        messages=[
            {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        model=model,
        temperature=0.0,
    )

    raw = (response.content or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()

    facts_raw = json.loads(raw)
    if not isinstance(facts_raw, list):
        raise ValueError(f"Expected JSON array, got {type(facts_raw).__name__}")
    return facts_raw


def _validate_extracted_fact(raw: dict) -> dict | None:
    """Validate and normalise a single extracted fact dict."""
    category = raw.get("category", "").lower().strip()
    if category not in _VALID_CATEGORIES:
        logger.warning("Invalid category {} in extracted fact, skipping", category)
        return None

    key = raw.get("key")
    value = raw.get("value")
    if not key or not value:
        return None

    return {
        "category": category,
        "entity": raw.get("entity") or None,
        "key": str(key).strip(),
        "value": str(value).strip(),
        "rationale": raw.get("rationale") or None,
        "tags": raw.get("tags", []),
    }


# ── Core distillation ─────────────────────────────────────


async def run_distillation(
    provider: LLMProvider,
    model: str,
    db_path: Path | None = None,
    decay_config: DecayTTLConfig | None = None,
) -> DistillationResult:
    """Run a full distillation cycle.

    1. Read unprocessed memory events since last distillation
    2. Group by agent_id
    3. Extract facts via LLM
    4. Classify decay tiers
    5. Deduplicate and insert/update
    6. Record distillation marker
    """
    result = DistillationResult()
    conn = get_engine(db_path)

    # 1. Find events since last distillation
    last_distill = get_last_distillation_time(conn)
    events = get_events_since(last_distill, conn)

    if not events:
        logger.info("Distillation: no new events to process")
        insert_memory_event(conn, event_type=MemoryEventType.distillation,
                           content=json.dumps({"events_processed": 0}))
        return result

    result.events_processed = len(events)
    logger.info("Distillation: processing {} event(s) since {}",
                len(events), last_distill or "beginning")

    # 2. Group by agent_id
    grouped: dict[str, list[str]] = defaultdict(list)
    event_ids: list[str] = []
    for eid, agent_id, etype, content in events:
        agent = agent_id or "unknown"
        grouped[agent].append(content)
        event_ids.append(eid)

    # 3. Extract facts per agent group
    all_extracted: list[tuple[dict, str, str]] = []

    for agent_id, contents in grouped.items():
        combined = "\n\n---\n\n".join(contents)
        if len(combined) > 30_000:
            combined = combined[:30_000] + "\n\n[... truncated]"

        time_range = f"since {last_distill.isoformat() if last_distill else 'start'}"

        try:
            raw_facts = await _extract_facts_from_content(
                agent_id, combined, time_range, provider, model,
            )
            logger.info("Extracted {} raw fact(s) for agent={}", len(raw_facts), agent_id)

            for raw in raw_facts:
                validated = _validate_extracted_fact(raw)
                if validated:
                    all_extracted.append((validated, agent_id, event_ids[0]))

        except Exception as exc:
            error_msg = f"Extraction failed for agent={agent_id}: {exc}"
            logger.error(error_msg)
            result.errors.append(error_msg)

    result.facts_extracted = len(all_extracted)

    if not all_extracted:
        logger.info("Distillation: no valid facts extracted")
        insert_memory_event(conn, event_type=MemoryEventType.distillation,
                           content=json.dumps({"events_processed": result.events_processed,
                                               "facts_extracted": 0}))
        return result

    # 4. Classify decay tiers
    facts_needing_classification: list[tuple[int, FactInput]] = []

    for i, (fact_dict, agent_id, _) in enumerate(all_extracted):
        category = fact_dict["category"]
        if category in ("decision", "convention"):
            # Auto-permanent for decisions and conventions
            fact_dict["_decay_tier"] = DecayTier.permanent
            fact_dict["_ttl_seconds"] = None
        else:
            facts_needing_classification.append((
                i,
                FactInput(
                    category=category,
                    entity=fact_dict["entity"],
                    key=fact_dict["key"],
                    value=fact_dict["value"],
                    rationale=fact_dict["rationale"],
                ),
            ))

    if facts_needing_classification:
        indices, fact_inputs = zip(*facts_needing_classification)
        classifications = await classify_decay_tier_batch(
            list(fact_inputs), provider, model, decay_config,
        )
        for idx, cls_result in zip(indices, classifications):
            all_extracted[idx][0]["_decay_tier"] = cls_result.decay_tier
            all_extracted[idx][0]["_ttl_seconds"] = cls_result.ttl_seconds

    # Default fallback
    for fact_dict, _, _ in all_extracted:
        if "_ttl_seconds" not in fact_dict:
            fact_dict["_decay_tier"] = DecayTier.active
            fact_dict["_ttl_seconds"] = compute_ttl_seconds(DecayTier.active, decay_config)

    # 5. Deduplicate and insert/update
    for fact_dict, agent_id, event_id in all_extracted:
        try:
            tags_json = json.dumps(fact_dict.get("tags", []))
            fact_id, is_new = upsert_fact(
                entity=fact_dict["entity"],
                key=fact_dict["key"],
                value=fact_dict["value"],
                conn=conn,
                category=FactCategory(fact_dict["category"]),
                decay_tier=fact_dict["_decay_tier"],
                ttl_seconds=fact_dict["_ttl_seconds"],
                rationale=fact_dict["rationale"],
                agent_id=agent_id,
                tags=tags_json,
                source_event_id=event_id,
            )
            if is_new:
                result.facts_inserted += 1
            else:
                result.facts_updated += 1
        except Exception as exc:
            error_msg = f"Upsert failed for [{fact_dict.get('entity')}/{fact_dict.get('key')}]: {exc}"
            logger.error(error_msg)
            result.errors.append(error_msg)

    conn.commit()

    # 6. Record distillation marker
    insert_memory_event(
        conn,
        event_type=MemoryEventType.distillation,
        content=json.dumps({
            "events_processed": result.events_processed,
            "facts_extracted": result.facts_extracted,
            "facts_inserted": result.facts_inserted,
            "facts_updated": result.facts_updated,
        }),
    )

    logger.info(
        "Distillation complete: {} events → {} extracted → {} inserted + {} updated ({} errors)",
        result.events_processed, result.facts_extracted,
        result.facts_inserted, result.facts_updated, len(result.errors),
    )
    return result
