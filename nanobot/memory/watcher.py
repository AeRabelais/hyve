"""Filesystem watcher — ingests workspace file changes into the memory event store.

Monitors each configured workspace for:
  * ``memory/YYYY-MM-DD.md``  — daily note changes  → ``daily_note`` events
  * ``MEMORY.md``             — agent memory writes → ``memory_write`` events

File changes are debounced (default 2 s) so rapid saves from
an editor don't produce duplicate events.  On each debounced change the
watcher diffs against cached content and inserts only the *new* material
as a raw event.

Requires the ``watchdog`` package (optional dependency).  If not installed,
:class:`WorkspaceWatcher` logs a warning and operates as a no-op.
"""

from __future__ import annotations

import difflib
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from nanobot.memory.db.connection import get_engine
from nanobot.memory.db.queries import insert_memory_event
from nanobot.memory.db.schema import MemoryEventType

if TYPE_CHECKING:
    from nanobot.events.emitter import EventEmitter

# Matches daily note filenames like 2026-03-08.md
_DAILY_NOTE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")

# Sentinel: True once we confirm watchdog is available
_HAS_WATCHDOG: bool | None = None


def _check_watchdog() -> bool:
    """Lazy check for watchdog availability."""
    global _HAS_WATCHDOG
    if _HAS_WATCHDOG is None:
        try:
            import watchdog  # noqa: F401
            _HAS_WATCHDOG = True
        except ImportError:
            _HAS_WATCHDOG = False
    return _HAS_WATCHDOG


# ── Helpers ────────────────────────────────────────────────


def _classify_file(file_path: Path, workspace_path: Path) -> MemoryEventType | None:
    """Return the event type for a watched file, or ``None`` if not watched.

    Watched patterns (relative to a workspace root):
      * ``memory/YYYY-MM-DD.md`` → daily_note
      * ``MEMORY.md``            → memory_write
    """
    try:
        rel = file_path.relative_to(workspace_path)
    except ValueError:
        return None

    parts = rel.parts

    # memory/YYYY-MM-DD.md (exactly two levels deep)
    if (
        len(parts) == 2
        and parts[0] == "memory"
        and _DAILY_NOTE_RE.match(parts[1])
    ):
        return MemoryEventType.daily_note

    # MEMORY.md at workspace root
    if len(parts) == 1 and parts[0] == "MEMORY.md":
        return MemoryEventType.memory_write

    return None


def _compute_new_content(old: str | None, new: str) -> str:
    """Return only the *added* lines between old and new content.

    If there is no prior version (first observation), returns the full
    new content.  Uses :mod:`difflib` unified diff under the hood but
    extracts only ``+`` lines (additions).
    """
    if old is None:
        return new

    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)

    added: list[str] = []
    for line in difflib.unified_diff(old_lines, new_lines, n=0):
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            added.append(line[1:])

    return "".join(added) if added else new


# ── Watchdog handler (created only if watchdog is available) ─


def _make_handler_class():
    """Dynamically create the watchdog handler to avoid import errors."""
    from watchdog.events import FileSystemEvent, FileSystemEventHandler

    class _MemoryFileHandler(FileSystemEventHandler):
        """Watchdog handler that debounces file changes and inserts events."""

        def __init__(
            self,
            workspace_path: Path,
            agent_id: str,
            db_path: Path | None = None,
            debounce_seconds: float = 2.0,
            emitter: EventEmitter | None = None,
        ) -> None:
            super().__init__()
            self._workspace_path = workspace_path.resolve()
            self._agent_id = agent_id
            self._db_path = db_path
            self._debounce_seconds = debounce_seconds
            self._emitter = emitter

            self._timers: dict[str, threading.Timer] = {}
            self._cache: dict[str, str] = {}
            self._lock = threading.Lock()

        def on_modified(self, event: FileSystemEvent) -> None:
            self._handle(event)

        def on_created(self, event: FileSystemEvent) -> None:
            self._handle(event)

        def _handle(self, event: FileSystemEvent) -> None:
            if event.is_directory:
                return

            file_path = Path(event.src_path).resolve()
            event_type = _classify_file(file_path, self._workspace_path)
            if event_type is None:
                return

            path_key = str(file_path)
            with self._lock:
                existing = self._timers.pop(path_key, None)
                if existing is not None:
                    existing.cancel()

                timer = threading.Timer(
                    self._debounce_seconds,
                    self._process_change,
                    args=(file_path, event_type),
                )
                self._timers[path_key] = timer
                timer.start()
                logger.debug("Watcher: debounce started for {} ({} s)", path_key, self._debounce_seconds)

        def _process_change(self, file_path: Path, event_type: MemoryEventType) -> None:
            """Read file, diff against cache, and insert a raw event."""
            path_key = str(file_path)

            try:
                new_content = file_path.read_text(encoding="utf-8")
            except FileNotFoundError:
                logger.warning("Watcher: file disappeared before processing: {}", path_key)
                return
            except OSError as exc:
                logger.error("Watcher: could not read {}: {}", path_key, exc)
                return

            with self._lock:
                old_content = self._cache.get(path_key)
                self._cache[path_key] = new_content
                self._timers.pop(path_key, None)

            diff_content = _compute_new_content(old_content, new_content)

            if not diff_content.strip():
                logger.debug("Watcher: no new content in {} — skipping", path_key)
                return

            conn = get_engine(self._db_path)
            event_id = insert_memory_event(
                conn,
                agent_id=self._agent_id,
                event_type=event_type,
                source=path_key,
                content=diff_content,
            )
            logger.info(
                "Watcher: event ingested agent={} type={} source={} id={}",
                self._agent_id, event_type.value, path_key, event_id,
            )

            # Emit memory.written event for observability
            if self._emitter:
                import asyncio
                from nanobot.events.models import Event, EventType

                event = Event(
                    event_type=EventType.MEMORY_WRITTEN,
                    agent_id=self._agent_id,
                    payload={
                        "source": "watcher",
                        "file": path_key,
                        "memory_event_type": event_type.value,
                        "event_id": event_id,
                    },
                )
                try:
                    loop = asyncio.get_running_loop()
                    loop.call_soon_threadsafe(
                        asyncio.ensure_future, self._emitter.emit(event)
                    )
                except RuntimeError:
                    pass  # No running loop — skip event emission

    return _MemoryFileHandler


# ── Public API ─────────────────────────────────────────────


class WorkspaceWatcher:
    """Watches workspace directories for memory-relevant file changes.

    If ``watchdog`` is not installed, the watcher logs a warning and
    operates as a no-op.

    Usage::

        watcher = WorkspaceWatcher(
            workspaces=[("/path/to/workspace", "agent-1")],
            db_path=memory_db_path,
            emitter=emitter,
        )
        watcher.start()   # non-blocking, spawns background threads
        ...
        watcher.stop()     # clean shutdown
    """

    def __init__(
        self,
        workspaces: list[tuple[Path, str]],
        db_path: Path | None = None,
        debounce_seconds: float = 2.0,
        emitter: EventEmitter | None = None,
    ) -> None:
        self._workspaces = workspaces
        self._db_path = db_path
        self._debounce_seconds = debounce_seconds
        self._emitter = emitter
        self._observer = None
        self._available = _check_watchdog()

        if not self._available:
            logger.warning(
                "Watcher: watchdog package not installed — filesystem watching disabled. "
                "Install with: pip install watchdog"
            )

    def start(self) -> None:
        """Start watching in background threads (non-blocking)."""
        if not self._available:
            return

        from watchdog.observers import Observer

        HandlerClass = _make_handler_class()
        self._observer = Observer()

        for ws_path, agent_id in self._workspaces:
            ws_path = Path(ws_path)
            if not ws_path.exists():
                logger.warning("Watcher: workspace path does not exist, skipping: {} (agent={})", ws_path, agent_id)
                continue

            handler = HandlerClass(
                workspace_path=ws_path,
                agent_id=agent_id,
                db_path=self._db_path,
                debounce_seconds=self._debounce_seconds,
                emitter=self._emitter,
            )
            self._observer.schedule(handler, str(ws_path), recursive=True)
            logger.info("Watcher: watching workspace {} (agent={})", ws_path, agent_id)

        self._observer.start()
        logger.info("WorkspaceWatcher started")

    def stop(self) -> None:
        """Signal the observer to stop and wait for threads to finish."""
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None
            logger.info("WorkspaceWatcher stopped")

    @property
    def is_alive(self) -> bool:
        """Whether the observer thread is still running."""
        if self._observer is None:
            return False
        return self._observer.is_alive()
