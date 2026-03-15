"""
Layered memory system for nanobot agents.

Architecture:
  Layer 1: Raw event log — append-only SQLite EventStore (Phase 1)
  Layer 2: Working memory — per-agent, rebuilt from recent events + summaries
  Layer 3: Distilled memory — LLM-extracted structured facts with FTS5 search
  Layer 4: Core knowledge — hierarchical index + detail files (Obsidian-compatible)

Usage:
    from nanobot.memory import init_db, get_session
    init_db(db_path)
"""

from nanobot.memory.db.connection import get_engine, get_session, init_db
from nanobot.memory.db.schema import DecayTier, Fact, FactCategory, MemoryEvent, MemoryEventType

__all__ = [
    "DecayTier",
    "Fact",
    "FactCategory",
    "MemoryEvent",
    "MemoryEventType",
    "get_engine",
    "get_session",
    "init_db",
]
