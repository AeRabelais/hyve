"""SQLite table definitions for the nanobot memory system.

Tables:
    memory_events — Layer 1 raw event store (daily notes, memory writes, conversations)
    facts         — Layer 3 distilled fact store with decay tiers and TTL support
    facts_fts     — FTS5 virtual table for fast structured lookups (no embedding costs)

Uses raw SQL + dataclasses (consistent with Phase 1 EventStore pattern).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Optional


# ── Enums ──────────────────────────────────────────────────


class MemoryEventType(str, Enum):
    """Valid event types ingested by the memory system."""
    daily_note = "daily_note"
    memory_write = "memory_write"
    conversation = "conversation"
    distillation = "distillation"


class FactCategory(str, Enum):
    """Semantic categories for distilled facts."""
    person = "person"
    project = "project"
    decision = "decision"
    convention = "convention"
    preference = "preference"
    task = "task"


class DecayTier(str, Enum):
    """Memory decay tiers controlling fact TTL behaviour.

    Tiers (longest → shortest lived):
        permanent   — never expires (identities, decisions, conventions)
        stable      — 90-day TTL (project descriptions, team relationships)
        active      — 14-day TTL (current tasks, sprint context)
        session     — 24-hour TTL (debugging context, temp workarounds)
        checkpoint  — 4-hour TTL (pre-flight state saves)
    """
    permanent = "permanent"
    stable = "stable"
    active = "active"
    session = "session"
    checkpoint = "checkpoint"


# ── Data models ────────────────────────────────────────────


def _gen_id() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class MemoryEvent:
    """Layer 1 — Raw event record from workspace watcher or conversation."""
    id: str = field(default_factory=_gen_id)
    timestamp: datetime = field(default_factory=_utcnow)
    agent_id: Optional[str] = None
    event_type: MemoryEventType = MemoryEventType.conversation
    source: Optional[str] = None
    content: str = ""
    event_metadata: str = "{}"


@dataclass
class Fact:
    """Layer 3 — Distilled fact with decay tier and TTL support."""
    id: str = field(default_factory=_gen_id)
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    accessed_at: datetime = field(default_factory=_utcnow)
    agent_id: Optional[str] = None
    category: FactCategory = FactCategory.task
    entity: Optional[str] = None
    key: str = ""
    value: str = ""
    rationale: Optional[str] = None
    decay_tier: DecayTier = DecayTier.active
    ttl_seconds: Optional[int] = None
    source_event_id: Optional[str] = None
    tags: str = "[]"


# ── SQL Schema ─────────────────────────────────────────────

MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_events (
    id              TEXT PRIMARY KEY,
    timestamp       TEXT NOT NULL,
    agent_id        TEXT,
    event_type      TEXT NOT NULL,
    source          TEXT,
    content         TEXT NOT NULL DEFAULT '',
    event_metadata  TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_mevents_time   ON memory_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_mevents_agent  ON memory_events(agent_id);
CREATE INDEX IF NOT EXISTS idx_mevents_type   ON memory_events(event_type);

CREATE TABLE IF NOT EXISTS facts (
    id               TEXT PRIMARY KEY,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    accessed_at      TEXT NOT NULL,
    agent_id         TEXT,
    category         TEXT NOT NULL,
    entity           TEXT,
    key              TEXT NOT NULL,
    value            TEXT NOT NULL,
    rationale        TEXT,
    decay_tier       TEXT NOT NULL,
    ttl_seconds      INTEGER,
    source_event_id  TEXT REFERENCES memory_events(id),
    tags             TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_facts_category_entity ON facts(category, entity);
CREATE INDEX IF NOT EXISTS idx_facts_accessed        ON facts(accessed_at);
CREATE INDEX IF NOT EXISTS idx_facts_decay_tier      ON facts(decay_tier);
CREATE INDEX IF NOT EXISTS idx_facts_agent           ON facts(agent_id);
"""
