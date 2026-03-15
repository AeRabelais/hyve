"""Tests for the nanobot event system (Phase 1)."""

import asyncio
import json
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from nanobot.events.emitter import EventEmitter
from nanobot.events.models import Event, EventType, ToolCallPayload, UsagePayload
from nanobot.events.store import AgentState, ChainState, EventStore


# ─── EventEmitter Tests ───────────────────────────────────


@pytest.mark.asyncio
async def test_emitter_exact_match():
    """Exact event type subscriptions receive the event."""
    emitter = EventEmitter()
    received = []

    async def handler(event: Event):
        received.append(event)

    emitter.on(EventType.AGENT_STARTED, handler)

    event = Event(event_type=EventType.AGENT_STARTED, agent_id="test")
    await emitter.emit(event)

    assert len(received) == 1
    assert received[0].agent_id == "test"


@pytest.mark.asyncio
async def test_emitter_wildcard():
    """Wildcard '*' listener receives all events."""
    emitter = EventEmitter()
    received = []

    async def handler(event: Event):
        received.append(event)

    emitter.on("*", handler)

    await emitter.emit(Event(event_type=EventType.AGENT_STARTED))
    await emitter.emit(Event(event_type=EventType.TOOL_CALLED))

    assert len(received) == 2


@pytest.mark.asyncio
async def test_emitter_category_wildcard():
    """Category wildcard 'agent.*' matches agent.started but not tool.called."""
    emitter = EventEmitter()
    received = []

    async def handler(event: Event):
        received.append(event)

    emitter.on("agent.*", handler)

    await emitter.emit(Event(event_type=EventType.AGENT_STARTED))
    await emitter.emit(Event(event_type=EventType.AGENT_COMPLETED))
    await emitter.emit(Event(event_type=EventType.TOOL_CALLED))  # should NOT match

    assert len(received) == 2
    assert all(e.event_type.value.startswith("agent.") for e in received)


@pytest.mark.asyncio
async def test_emitter_off():
    """Unsubscribing via off() stops delivery."""
    emitter = EventEmitter()
    received = []

    async def handler(event: Event):
        received.append(event)

    emitter.on(EventType.AGENT_STARTED, handler)
    await emitter.emit(Event(event_type=EventType.AGENT_STARTED))
    assert len(received) == 1

    emitter.off(EventType.AGENT_STARTED, handler)
    await emitter.emit(Event(event_type=EventType.AGENT_STARTED))
    assert len(received) == 1  # No new events


@pytest.mark.asyncio
async def test_emitter_error_isolation():
    """A broken listener does not crash the emitter or block others."""
    emitter = EventEmitter()
    received = []

    async def broken_handler(event: Event):
        raise RuntimeError("I'm broken!")

    async def good_handler(event: Event):
        received.append(event)

    emitter.on(EventType.AGENT_STARTED, broken_handler)
    emitter.on(EventType.AGENT_STARTED, good_handler)

    # Should not raise
    await emitter.emit(Event(event_type=EventType.AGENT_STARTED))

    # Good handler still received the event
    assert len(received) == 1


@pytest.mark.asyncio
async def test_emitter_no_listeners():
    """Emitting with no listeners is a no-op (no error)."""
    emitter = EventEmitter()
    await emitter.emit(Event(event_type=EventType.AGENT_STARTED))


@pytest.mark.asyncio
async def test_emitter_multiple_match_types():
    """An event can match exact, wildcard, and category simultaneously."""
    emitter = EventEmitter()
    calls = {"exact": 0, "wildcard": 0, "category": 0}

    async def exact(event: Event):
        calls["exact"] += 1

    async def wildcard(event: Event):
        calls["wildcard"] += 1

    async def category(event: Event):
        calls["category"] += 1

    emitter.on(EventType.AGENT_STARTED, exact)
    emitter.on("*", wildcard)
    emitter.on("agent.*", category)

    await emitter.emit(Event(event_type=EventType.AGENT_STARTED))

    assert calls == {"exact": 1, "wildcard": 1, "category": 1}


# ─── EventStore Tests ─────────────────────────────────────


@pytest.fixture
def tmp_store(tmp_path):
    """Create an EventStore with a temporary database."""
    store = EventStore(tmp_path / "test_events.db")
    yield store
    store.close()


@pytest.mark.asyncio
async def test_store_persist_and_query(tmp_store):
    """Events are persisted and queryable."""
    event = Event(
        event_type=EventType.AGENT_STARTED,
        agent_id="default",
        payload={"model": "test-model"},
    )
    await tmp_store.handle_event(event)

    results = tmp_store.query(agent_id="default")
    assert len(results) == 1
    assert results[0].event_type == EventType.AGENT_STARTED
    assert results[0].payload["model"] == "test-model"


@pytest.mark.asyncio
async def test_store_query_by_type(tmp_store):
    """Query filters by event_type."""
    await tmp_store.handle_event(Event(event_type=EventType.AGENT_STARTED, agent_id="a"))
    await tmp_store.handle_event(Event(event_type=EventType.TOOL_CALLED, agent_id="a"))
    await tmp_store.handle_event(Event(event_type=EventType.AGENT_COMPLETED, agent_id="a"))

    results = tmp_store.query(event_type=EventType.TOOL_CALLED)
    assert len(results) == 1
    assert results[0].event_type == EventType.TOOL_CALLED


@pytest.mark.asyncio
async def test_store_query_limit(tmp_store):
    """Query respects limit."""
    for i in range(20):
        await tmp_store.handle_event(Event(
            event_type=EventType.AGENT_ITERATION,
            agent_id="default",
            payload={"iteration": i},
        ))

    results = tmp_store.query(limit=5)
    assert len(results) == 5


@pytest.mark.asyncio
async def test_store_derived_agent_state(tmp_store):
    """Derived state tracks agent lifecycle."""
    await tmp_store.handle_event(Event(
        event_type=EventType.AGENT_STARTED,
        agent_id="coder",
        payload={"model": "test"},
    ))

    assert "coder" in tmp_store.active_agents
    state = tmp_store.active_agents["coder"]
    assert state.status == "running"
    assert state.iteration == 0

    await tmp_store.handle_event(Event(
        event_type=EventType.AGENT_ITERATION,
        agent_id="coder",
    ))
    assert state.iteration == 1

    await tmp_store.handle_event(Event(
        event_type=EventType.AGENT_COMPLETED,
        agent_id="coder",
    ))
    assert state.status == "idle"
    assert state.completed_at is not None


@pytest.mark.asyncio
async def test_store_derived_usage_tracking(tmp_store):
    """Usage events accumulate tokens in agent state."""
    await tmp_store.handle_event(Event(
        event_type=EventType.AGENT_STARTED,
        agent_id="default",
    ))

    await tmp_store.handle_event(Event(
        event_type=EventType.USAGE_TRACKED,
        agent_id="default",
        payload={"input_tokens": 100, "output_tokens": 50},
    ))
    await tmp_store.handle_event(Event(
        event_type=EventType.USAGE_TRACKED,
        agent_id="default",
        payload={"input_tokens": 200, "output_tokens": 80},
    ))

    state = tmp_store.active_agents["default"]
    assert state.total_tokens == 430  # 100+50+200+80


@pytest.mark.asyncio
async def test_store_derived_chain_state(tmp_store):
    """Derived state tracks chain lifecycle."""
    await tmp_store.handle_event(Event(
        event_type=EventType.CHAIN_DELEGATED,
        chain_id="paper-review",
    ))
    assert "paper-review" in tmp_store.active_chains
    assert tmp_store.active_chains["paper-review"].status == "active"

    await tmp_store.handle_event(Event(
        event_type=EventType.CHAIN_AWAITING_APPROVAL,
        chain_id="paper-review",
    ))
    assert tmp_store.active_chains["paper-review"].status == "awaiting_approval"

    await tmp_store.handle_event(Event(
        event_type=EventType.CHAIN_COMPLETED,
        chain_id="paper-review",
    ))
    assert tmp_store.active_chains["paper-review"].status == "completed"


def test_store_rebuild_on_restart(tmp_path):
    """State rebuilds correctly from persisted events after restart."""
    db_path = tmp_path / "rebuild_test.db"

    # First instance — write events
    store1 = EventStore(db_path)
    asyncio.get_event_loop().run_until_complete(
        store1.handle_event(Event(
            event_type=EventType.AGENT_STARTED,
            agent_id="coder",
        ))
    )
    asyncio.get_event_loop().run_until_complete(
        store1.handle_event(Event(
            event_type=EventType.AGENT_ITERATION,
            agent_id="coder",
        ))
    )
    asyncio.get_event_loop().run_until_complete(
        store1.handle_event(Event(
            event_type=EventType.USAGE_TRACKED,
            agent_id="coder",
            payload={"input_tokens": 500, "output_tokens": 200},
        ))
    )
    store1.close()

    # Second instance — should rebuild state
    store2 = EventStore(db_path)
    assert "coder" in store2.active_agents
    state = store2.active_agents["coder"]
    assert state.status == "running"
    assert state.iteration == 1
    assert state.total_tokens == 700
    store2.close()


# ─── Integration: Emitter → Store ─────────────────────────


@pytest.mark.asyncio
async def test_emitter_store_integration(tmp_path):
    """Full round-trip: emit → store persists → queryable."""
    from nanobot.events import setup

    emitter, store = setup(tmp_path)

    await emitter.emit(Event(
        event_type=EventType.AGENT_STARTED,
        agent_id="default",
        payload={"model": "claude-test"},
    ))
    await emitter.emit(Event(
        event_type=EventType.TOOL_CALLED,
        agent_id="default",
        payload={"tool_name": "web_search", "args": {"query": "test"}},
    ))
    await emitter.emit(Event(
        event_type=EventType.TOOL_RESULT,
        agent_id="default",
        payload={"tool_name": "web_search", "duration_ms": 150.5, "success": True},
    ))
    await emitter.emit(Event(
        event_type=EventType.AGENT_COMPLETED,
        agent_id="default",
        payload={"status": "success"},
    ))

    # Query all events
    all_events = store.query(agent_id="default", limit=100)
    assert len(all_events) == 4

    # Check derived state
    assert "default" in store.active_agents
    assert store.active_agents["default"].status == "idle"

    # Query specific type
    tool_events = store.query(event_type=EventType.TOOL_CALLED)
    assert len(tool_events) == 1
    assert tool_events[0].payload["tool_name"] == "web_search"

    store.close()


# ─── Model Tests ──────────────────────────────────────────


def test_event_immutability():
    """Event model is frozen — attributes cannot be changed after creation."""
    event = Event(event_type=EventType.AGENT_STARTED, agent_id="test")

    with pytest.raises(Exception):  # ValidationError for frozen model
        event.agent_id = "changed"


def test_typed_payload_helpers():
    """Typed payload helpers produce valid dicts for Event payloads."""
    tool_payload = ToolCallPayload(
        tool_name="web_search",
        args={"query": "test"},
        duration_ms=150.5,
        success=True,
    )
    dumped = tool_payload.model_dump()
    assert dumped["tool_name"] == "web_search"
    assert dumped["duration_ms"] == 150.5

    usage_payload = UsagePayload(
        model="claude-sonnet",
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=25,
    )
    dumped = usage_payload.model_dump()
    assert dumped["model"] == "claude-sonnet"
    assert dumped["input_tokens"] == 100


def test_event_default_timestamp():
    """Events get auto-populated timestamps."""
    event = Event(event_type=EventType.AGENT_STARTED)
    assert event.timestamp is not None
    assert isinstance(event.timestamp, datetime)
