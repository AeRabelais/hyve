"""
Event system for nanobot — EventEmitter + EventStore.

Usage:
    from nanobot.events import setup
    emitter, store = setup(data_dir)
"""

from pathlib import Path

from .emitter import EventEmitter
from .models import Event, EventType
from .store import EventStore

__all__ = ["Event", "EventEmitter", "EventStore", "EventType", "emitter", "setup", "store"]

# Singleton instances — initialized in setup()
emitter: EventEmitter | None = None
store: EventStore | None = None


def setup(data_dir: Path) -> tuple[EventEmitter, EventStore]:
    """
    Initialize the event system. Call once during nanobot startup.

    Args:
        data_dir: nanobot data directory (e.g. ``~/.nanobot``).

    Returns:
        Tuple of (emitter, store) ready for use.
    """
    global emitter, store

    emitter = EventEmitter()
    store = EventStore(data_dir / "events.db")

    # Wire store as wildcard listener — it persists every event
    emitter.on("*", store.handle_event)

    return emitter, store
