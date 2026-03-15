"""TTL-based fact pruning for the memory system.

Removes expired facts based on each fact's decay_tier and ttl_seconds.
Permanent facts are never pruned. Designed to be run hourly.

Pruning rule::

    DELETE FROM facts
    WHERE decay_tier != 'permanent'
      AND ttl_seconds IS NOT NULL
      AND datetime(accessed_at, '+' || ttl_seconds || ' seconds') < datetime('now')
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from nanobot.memory.db.connection import get_engine


@dataclass(frozen=True, slots=True)
class PrunedFact:
    """Information about a single pruned fact."""
    id: str
    category: str
    entity: str | None
    key: str
    decay_tier: str
    ttl_seconds: int
    accessed_at: str
    age_seconds: float


_SELECT_EXPIRED = """
    SELECT
        id, category, entity, key, decay_tier, ttl_seconds, accessed_at,
        (julianday('now') - julianday(accessed_at)) * 86400.0 AS age_seconds
    FROM facts
    WHERE decay_tier != 'permanent'
      AND ttl_seconds IS NOT NULL
      AND datetime(accessed_at, '+' || ttl_seconds || ' seconds') < datetime('now')
    ORDER BY accessed_at ASC
"""

_DELETE_EXPIRED = """
    DELETE FROM facts
    WHERE decay_tier != 'permanent'
      AND ttl_seconds IS NOT NULL
      AND datetime(accessed_at, '+' || ttl_seconds || ' seconds') < datetime('now')
"""


def prune_expired_facts(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
) -> list[PrunedFact]:
    """Delete expired facts based on their TTL and last access time.

    Parameters
    ----------
    conn
        SQLite connection.
    dry_run
        If True, identify expired facts but don't delete them.

    Returns
    -------
    list[PrunedFact]
        Facts that were (or would be) pruned.
    """
    rows = conn.execute(_SELECT_EXPIRED).fetchall()

    pruned: list[PrunedFact] = []
    for row in rows:
        pruned.append(PrunedFact(
            id=row[0], category=row[1], entity=row[2], key=row[3],
            decay_tier=row[4], ttl_seconds=row[5], accessed_at=row[6],
            age_seconds=row[7],
        ))

    if not pruned:
        logger.debug("Prune cycle: nothing to prune")
        return pruned

    mode = "DRY RUN" if dry_run else "PRUNING"
    logger.info("{}: {} expired fact(s) identified", mode, len(pruned))
    for pf in pruned:
        age_hours = pf.age_seconds / 3600
        logger.debug(
            "  {} [{}/{}] tier={} ttl={}s ({:.1f}h ago)",
            mode, pf.entity or "(none)", pf.key, pf.decay_tier, pf.ttl_seconds, age_hours,
        )

    if not dry_run:
        conn.execute(_DELETE_EXPIRED)
        conn.commit()
        logger.info("Pruned {} expired fact(s)", len(pruned))

    return pruned


def run_prune_cycle(
    db_path: Path | None = None,
    *,
    dry_run: bool = False,
) -> list[PrunedFact]:
    """Run a single prune cycle. Convenience wrapper."""
    conn = get_engine(db_path)
    return prune_expired_facts(conn, dry_run=dry_run)
