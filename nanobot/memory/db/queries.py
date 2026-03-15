"""Reusable database query helpers for the nanobot memory system.

Provides common operations used by the recall tool, distiller, generator,
and pruner without coupling them to raw SQL scattered across the codebase.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime, timezone
from typing import Any, Optional

from loguru import logger

from nanobot.memory.db.schema import (
    DecayTier,
    Fact,
    FactCategory,
    MemoryEvent,
    MemoryEventType,
)


# ── Fact helpers ───────────────────────────────────────────


def _row_to_fact(row: tuple) -> Fact:
    """Convert a SELECT * row to a Fact dataclass."""
    return Fact(
        id=row[0],
        created_at=datetime.fromisoformat(row[1]) if isinstance(row[1], str) else row[1],
        updated_at=datetime.fromisoformat(row[2]) if isinstance(row[2], str) else row[2],
        accessed_at=datetime.fromisoformat(row[3]) if isinstance(row[3], str) else row[3],
        agent_id=row[4],
        category=FactCategory(row[5]) if row[5] else FactCategory.task,
        entity=row[6],
        key=row[7],
        value=row[8],
        rationale=row[9],
        decay_tier=DecayTier(row[10]) if row[10] else DecayTier.active,
        ttl_seconds=row[11],
        source_event_id=row[12],
        tags=row[13] if row[13] else "[]",
    )


# ── TTL Refresh on Access ─────────────────────────────────


def refresh_accessed_at(
    fact_ids: list[str],
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
) -> int:
    """Batch-update ``accessed_at`` for the given fact IDs.

    Keeps actively-used facts alive by resetting their TTL clock.
    """
    if not fact_ids:
        return 0

    ts = (now or datetime.now(UTC)).isoformat()
    batch_size = 900

    total_updated = 0
    for i in range(0, len(fact_ids), batch_size):
        batch = fact_ids[i:i + batch_size]
        placeholders = ", ".join("?" for _ in batch)
        conn.execute(
            f"UPDATE facts SET accessed_at = ? WHERE id IN ({placeholders})",
            [ts, *batch],
        )
        total_updated += len(batch)

    conn.commit()
    logger.debug("Refreshed accessed_at for {} fact(s)", total_updated)
    return total_updated


# ── Deduplication ──────────────────────────────────────────


def find_duplicate_fact(
    entity: str | None,
    key: str,
    conn: sqlite3.Connection,
) -> tuple[str, str, str] | None:
    """Check for an existing fact with the same entity and key.

    Tries exact match first, then FTS5 fuzzy search.
    Returns (id, key, value) of the match, or None.
    """
    if entity is None:
        row = conn.execute(
            "SELECT id, key, value FROM facts WHERE entity IS NULL AND key = ? LIMIT 1",
            (key,),
        ).fetchone()
        return row if row else None

    # Exact match
    row = conn.execute(
        "SELECT id, key, value FROM facts WHERE entity = ? AND key = ? LIMIT 1",
        (entity, key),
    ).fetchone()
    if row:
        return row

    # FTS5 fuzzy match
    try:
        fts_row = conn.execute(
            'SELECT f.id, f.key, f.value FROM facts f '
            'WHERE f.rowid IN ('
            '    SELECT rowid FROM facts_fts '
            '    WHERE entity MATCH ? AND key MATCH ?'
            ') LIMIT 1',
            (f'"{entity}"', f'"{key}"'),
        ).fetchone()
        if fts_row:
            logger.debug("FTS5 fuzzy dedup match: entity={} key={}", entity, key)
            return fts_row
    except sqlite3.OperationalError as exc:
        logger.debug("FTS5 dedup search failed (non-fatal): {}", exc)

    return None


def upsert_fact(
    entity: str | None,
    key: str,
    value: str,
    conn: sqlite3.Connection,
    *,
    category: FactCategory = FactCategory.task,
    decay_tier: DecayTier = DecayTier.active,
    ttl_seconds: int | None = None,
    rationale: str | None = None,
    agent_id: str | None = None,
    tags: str = "[]",
    source_event_id: str | None = None,
) -> tuple[str, bool]:
    """Insert a new fact or update an existing duplicate.

    Returns (fact_id, is_new).
    """
    now = datetime.now(UTC).isoformat()

    existing = find_duplicate_fact(entity, key, conn)
    if existing:
        existing_id = existing[0]
        conn.execute(
            "UPDATE facts SET value = ?, updated_at = ?, accessed_at = ? WHERE id = ?",
            (value, now, now, existing_id),
        )
        logger.debug("Dedup: updated existing fact {} [{}/{}]", existing_id, entity, key)
        return existing_id, False

    import uuid
    fact_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO facts (id, created_at, updated_at, accessed_at, agent_id, "
        "category, entity, key, value, rationale, decay_tier, ttl_seconds, "
        "source_event_id, tags) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            fact_id, now, now, now, agent_id,
            category.value, entity, key, value, rationale,
            decay_tier.value, ttl_seconds, source_event_id, tags,
        ),
    )
    logger.debug("Dedup: inserted new fact {} [{}/{}]", fact_id, entity, key)
    return fact_id, True


# ── Insert event ───────────────────────────────────────────


def insert_memory_event(
    conn: sqlite3.Connection,
    *,
    agent_id: str | None = None,
    event_type: MemoryEventType = MemoryEventType.conversation,
    source: str | None = None,
    content: str = "",
    event_metadata: str = "{}",
) -> str:
    """Insert a raw memory event and return its ID."""
    import uuid
    event_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO memory_events (id, timestamp, agent_id, event_type, source, content, event_metadata) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (event_id, now, agent_id, event_type.value, source, content, event_metadata),
    )
    conn.commit()
    return event_id


# ── Generator query helpers ────────────────────────────────


def get_all_live_facts(
    conn: sqlite3.Connection,
    agent_id: str | None = None,
) -> list[Fact]:
    """Return all non-expired facts, optionally filtered by agent.

    "Non-expired" means:
      - decay_tier = 'permanent' (no TTL), OR
      - accessed_at + ttl_seconds > now (TTL not yet elapsed)

    Facts with agent_id IS NULL are considered shared and always included.
    """
    if agent_id:
        rows = conn.execute(
            "SELECT * FROM facts "
            "WHERE (agent_id = ? OR agent_id IS NULL) "
            "AND ("
            "  decay_tier = 'permanent' "
            "  OR ttl_seconds IS NULL "
            "  OR datetime(accessed_at, '+' || ttl_seconds || ' seconds') > datetime('now')"
            ") "
            "ORDER BY category, entity, key",
            (agent_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM facts "
            "WHERE ("
            "  decay_tier = 'permanent' "
            "  OR ttl_seconds IS NULL "
            "  OR datetime(accessed_at, '+' || ttl_seconds || ' seconds') > datetime('now')"
            ") "
            "ORDER BY category, entity, key"
        ).fetchall()

    return [_row_to_fact(row) for row in rows]


def group_facts_by_category(facts: list[Fact]) -> dict[str, list[Fact]]:
    """Group facts by their category string value."""
    grouped: dict[str, list[Fact]] = defaultdict(list)
    for f in facts:
        cat = f.category.value if isinstance(f.category, FactCategory) else str(f.category)
        grouped[cat].append(f)
    return grouped


def get_entity_fact_counts(facts: list[Fact]) -> dict[tuple[str, str], int]:
    """Count facts per (category, entity) pair."""
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for f in facts:
        cat = f.category.value if isinstance(f.category, FactCategory) else str(f.category)
        entity = f.entity or "__none__"
        counts[(cat, entity)] += 1
    return counts


def get_top_accessed_entities(
    facts: list[Fact],
    n: int = 3,
) -> list[tuple[str, str, datetime]]:
    """Return the n entities with the most recent average accessed_at."""
    detail_categories = {"person", "project"}
    entity_times: dict[tuple[str, str], list[datetime]] = defaultdict(list)

    for f in facts:
        cat = f.category.value if isinstance(f.category, FactCategory) else str(f.category)
        if cat not in detail_categories or not f.entity:
            continue
        accessed = f.accessed_at
        if isinstance(accessed, str):
            accessed = datetime.fromisoformat(accessed)
        entity_times[(cat, f.entity)].append(accessed)

    scored: list[tuple[str, str, datetime]] = []
    for (cat, entity), times in entity_times.items():
        avg_ts = datetime.fromtimestamp(
            sum(t.timestamp() for t in times) / len(times),
            tz=timezone.utc,
        )
        scored.append((cat, entity, avg_ts))

    scored.sort(key=lambda x: x[2], reverse=True)
    return scored[:n]


# ── Scheduler query helpers ────────────────────────────────


def get_hourly_event_summary(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """Count memory events by type and agent in the last hour."""
    rows = conn.execute(
        "SELECT event_type, COALESCE(agent_id, 'shared'), COUNT(*) "
        "FROM memory_events "
        "WHERE timestamp > datetime('now', '-1 hour') "
        "GROUP BY event_type, agent_id "
        "ORDER BY event_type, agent_id"
    ).fetchall()

    summary: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        summary[row[0]][row[1]] = row[2]
    return dict(summary)


def get_last_distillation_time(conn: sqlite3.Connection) -> datetime | None:
    """Find the timestamp of the most recent distillation event."""
    row = conn.execute(
        "SELECT timestamp FROM memory_events "
        "WHERE event_type = 'distillation' "
        "ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return datetime.fromisoformat(row[0]) if isinstance(row[0], str) else row[0]


def get_events_since(
    since: datetime | None,
    conn: sqlite3.Connection,
) -> list[tuple[str, str, str, str]]:
    """Fetch raw memory events since given timestamp.

    Returns list of (id, agent_id, event_type, content) tuples.
    Excludes distillation events themselves.
    """
    if since:
        rows = conn.execute(
            "SELECT id, agent_id, event_type, content FROM memory_events "
            "WHERE timestamp > ? AND event_type != 'distillation' "
            "ORDER BY timestamp ASC",
            (since.isoformat(),),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, agent_id, event_type, content FROM memory_events "
            "WHERE event_type != 'distillation' "
            "ORDER BY timestamp ASC"
        ).fetchall()
    return rows


def archive_stale_facts(
    conn: sqlite3.Connection,
    stale_days: int = 90,
) -> int:
    """Soft-delete facts not accessed in stale_days days.

    Moves them to decay_tier = 'archived'. Permanent facts are exempt.
    """
    cursor = conn.execute(
        "UPDATE facts "
        "SET decay_tier = 'archived' "
        "WHERE decay_tier NOT IN ('permanent', 'archived') "
        "AND datetime(accessed_at, '+' || ? || ' days') < datetime('now')",
        (stale_days,),
    )
    count = cursor.rowcount
    if count:
        conn.commit()
        logger.info("Archived {} stale fact(s) (not accessed in {}+ days)", count, stale_days)
    return count


def compact_events_table(
    conn: sqlite3.Connection,
    older_than_days: int = 30,
) -> int:
    """Delete old non-distillation memory events to keep the table lean."""
    cursor = conn.execute(
        "DELETE FROM memory_events "
        "WHERE event_type != 'distillation' "
        "AND timestamp < datetime('now', '-' || ? || ' days')",
        (older_than_days,),
    )
    count = cursor.rowcount
    if count:
        conn.commit()
        logger.info("Compacted memory_events table: deleted {} event(s) older than {} days", count, older_than_days)
    return count


def get_db_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return high-level database statistics."""
    total_events = conn.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0]
    total_facts = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]

    tier_rows = conn.execute(
        "SELECT decay_tier, COUNT(*) FROM facts GROUP BY decay_tier ORDER BY decay_tier"
    ).fetchall()
    facts_by_tier = {row[0]: row[1] for row in tier_rows}

    last_row = conn.execute(
        "SELECT timestamp FROM memory_events "
        "WHERE event_type = 'distillation' "
        "ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()
    last_distillation = last_row[0] if last_row else None

    return {
        "total_events": total_events,
        "total_facts": total_facts,
        "facts_by_tier": facts_by_tier,
        "last_distillation": last_distillation,
    }


# ── Search (for RecallTool) ───────────────────────────────


def search_fts5(
    conn: sqlite3.Connection,
    query: str,
    *,
    category: str | None = None,
    entity: str | None = None,
    limit: int = 10,
) -> list[Fact]:
    """FTS5 MATCH search against facts."""
    sql = (
        "SELECT f.* FROM facts f "
        "WHERE f.rowid IN ("
        "    SELECT rowid FROM facts_fts WHERE facts_fts MATCH ?"
        ")"
    )
    params: list[Any] = [query]

    if category:
        sql += " AND f.category = ?"
        params.append(category)
    if entity:
        sql += " AND f.entity = ?"
        params.append(entity)

    sql += " ORDER BY f.accessed_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    return [_row_to_fact(row) for row in rows]


def search_like(
    conn: sqlite3.Connection,
    query: str,
    *,
    category: str | None = None,
    entity: str | None = None,
    limit: int = 10,
) -> list[Fact]:
    """LIKE fallback search for broader matching."""
    pattern = f"%{query}%"
    sql = (
        "SELECT f.* FROM facts f "
        "WHERE ("
        "    f.entity LIKE ? OR f.key LIKE ? OR f.value LIKE ? "
        "    OR f.rationale LIKE ? OR f.category LIKE ?"
        ")"
    )
    params: list[Any] = [pattern, pattern, pattern, pattern, pattern]

    if category:
        sql += " AND f.category = ?"
        params.append(category)
    if entity:
        sql += " AND f.entity = ?"
        params.append(entity)

    sql += " ORDER BY f.accessed_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    return [_row_to_fact(row) for row in rows]


def search_facts(
    conn: sqlite3.Connection,
    query: str,
    *,
    category: str | None = None,
    entity: str | None = None,
    limit: int = 10,
) -> tuple[list[Fact], str]:
    """Multi-strategy search. Returns (results, strategy_used).

    Strategies tried in order:
      1. FTS5 MATCH
      2. LIKE fallback
      3. Category/entity filter only
    """
    # Strategy 1: FTS5
    if query:
        try:
            results = search_fts5(conn, query, category=category, entity=entity, limit=limit)
            if results:
                return results, "FTS5"
        except sqlite3.OperationalError:
            pass

    # Strategy 2: LIKE
    if query:
        results = search_like(conn, query, category=category, entity=entity, limit=limit)
        if results:
            return results, "LIKE"

    # Strategy 3: Filter only
    if category or entity:
        conditions: list[str] = []
        params: list[Any] = []
        if category:
            conditions.append("f.category = ?")
            params.append(category)
        if entity:
            conditions.append("f.entity = ?")
            params.append(entity)

        where = " AND ".join(conditions)
        sql = f"SELECT f.* FROM facts f WHERE {where} ORDER BY f.accessed_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        results = [_row_to_fact(row) for row in rows]
        if results:
            return results, "filter"

    return [], "none"
