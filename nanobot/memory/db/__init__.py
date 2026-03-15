"""nanobot.memory.db — Database layer for the memory system.

Re-exports the public API for convenient imports::

    from nanobot.memory.db import MemoryEvent, Fact, init_db
"""

from nanobot.memory.db.connection import get_engine, get_session, init_db
from nanobot.memory.db.schema import (
    DecayTier,
    Fact,
    FactCategory,
    MemoryEvent,
    MemoryEventType,
)

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
