"""Database engine, session management, and initialisation for nanobot memory.

The SQLite memory database lives at ``~/.nanobot/memory.db`` by default.
On first run, :func:`init_db` creates tables, FTS5 virtual table, and
synchronisation triggers.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from loguru import logger

from nanobot.memory.db.schema import MEMORY_SCHEMA
from nanobot.memory.db.triggers import FTS5_SCHEMA

DEFAULT_DB_PATH = Path.home() / ".nanobot" / "memory.db"

_connections: dict[str, sqlite3.Connection] = {}


def get_engine(db_path: Path | None = None) -> sqlite3.Connection:
    """Return a SQLite connection for the given database path.

    SQLite pragmas applied on every new connection:
      * ``journal_mode = WAL``  — concurrent readers + single writer
      * ``foreign_keys = ON``   — enforce FK constraints
      * ``busy_timeout = 5000`` — wait up to 5 s on lock contention

    Connections are cached by path to avoid re-opening.
    """
    path = db_path or DEFAULT_DB_PATH
    key = str(path)

    if key in _connections:
        try:
            _connections[key].execute("SELECT 1")
            return _connections[key]
        except sqlite3.ProgrammingError:
            del _connections[key]

    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 5000;")

    _connections[key] = conn
    return conn


def get_session(db_path: Path | None = None) -> sqlite3.Connection:
    """Alias for get_engine — returns a SQLite connection."""
    return get_engine(db_path)


def init_db(db_path: Path | None = None) -> sqlite3.Connection:
    """Create the database directory, tables, FTS5 virtual table, and triggers.

    Safe to call repeatedly — all operations are idempotent.

    Returns the connection for convenience.
    """
    conn = get_engine(db_path)
    conn.executescript(MEMORY_SCHEMA)
    conn.executescript(FTS5_SCHEMA)
    logger.debug("Memory database initialised at {}", db_path or DEFAULT_DB_PATH)
    return conn


def close_all() -> None:
    """Close all cached connections."""
    for conn in _connections.values():
        try:
            conn.close()
        except Exception:
            pass
    _connections.clear()
