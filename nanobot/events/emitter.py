"""In-process async pub-sub for nanobot events."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Callable, Coroutine

from loguru import logger

from .models import Event, EventType

# Type alias for subscriber callbacks
Listener = Callable[[Event], Coroutine[Any, Any, None]]


class EventEmitter:
    """
    In-process async pub-sub for nanobot events.

    Usage:
        emitter = EventEmitter()

        # Subscribe
        emitter.on(EventType.AGENT_STARTED, my_handler)
        emitter.on("*", catch_all_handler)        # wildcard
        emitter.on("agent.*", agent_handler)       # category wildcard

        # Emit
        await emitter.emit(Event(
            event_type=EventType.AGENT_STARTED,
            agent_id="coder",
            payload={"model": "claude-sonnet-4-20250514"}
        ))
    """

    def __init__(self) -> None:
        # event_type key -> list of async callbacks
        self._listeners: dict[str, list[Listener]] = defaultdict(list)

    def on(self, event_type: EventType | str, callback: Listener) -> None:
        """Subscribe to an event type. Use ``"*"`` for all events."""
        key = event_type.value if isinstance(event_type, EventType) else event_type
        self._listeners[key].append(callback)

    def off(self, event_type: EventType | str, callback: Listener) -> None:
        """Unsubscribe a specific callback."""
        key = event_type.value if isinstance(event_type, EventType) else event_type
        self._listeners[key] = [cb for cb in self._listeners[key] if cb is not callback]

    async def emit(self, event: Event) -> None:
        """
        Dispatch event to all matching listeners.

        Listeners run concurrently via ``asyncio.gather``.
        Errors in listeners are logged but never propagated — a broken
        listener must not crash the agent loop.
        """
        targets: list[Listener] = []

        # Exact match listeners
        targets.extend(self._listeners.get(event.event_type.value, []))

        # Wildcard listeners
        targets.extend(self._listeners.get("*", []))

        # Category wildcard: "agent.*" matches "agent.started"
        category = event.event_type.value.split(".")[0] + ".*"
        targets.extend(self._listeners.get(category, []))

        if not targets:
            return

        results = await asyncio.gather(
            *(cb(event) for cb in targets),
            return_exceptions=True,
        )

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    "Event listener error for {}: {}",
                    event.event_type.value,
                    result,
                )
