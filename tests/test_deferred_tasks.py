"""Tests for deferred tasks: task_board, cost calculation, memory CLI."""

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nanobot.events.emitter import EventEmitter
from nanobot.events.models import Event, EventType, UsagePayload
from nanobot.events.store import AgentState, ChainState, EventStore, TaskState
from nanobot.providers.pricing import ModelPricing, compute_cost, _lookup_pricing


# ═══════════════════════════════════════════════════════════
# Pricing module tests
# ═══════════════════════════════════════════════════════════


class TestPricing:
    """Tests for nanobot.providers.pricing."""

    def test_exact_match(self):
        """Exact model name matches pricing table."""
        cost = compute_cost("anthropic/claude-sonnet-4", input_tokens=1000, output_tokens=500)
        assert cost is not None
        # 1000/1M * 3.0 + 500/1M * 15.0 = 0.003 + 0.0075 = 0.0105
        assert abs(cost - 0.0105) < 1e-6

    def test_prefix_match(self):
        """Versioned model names match via longest prefix."""
        cost = compute_cost(
            "anthropic/claude-sonnet-4-20250514",
            input_tokens=1_000_000,
            output_tokens=0,
        )
        assert cost is not None
        assert abs(cost - 3.0) < 1e-6

    def test_cache_read_tokens(self):
        """Cache read tokens use the cache_read_per_m rate."""
        cost = compute_cost(
            "anthropic/claude-opus-4",
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=1_000_000,
        )
        assert cost is not None
        assert abs(cost - 1.5) < 1e-6

    def test_unknown_model_returns_none(self):
        """Unknown model names return None."""
        cost = compute_cost("totally-unknown/fake-model-9000", input_tokens=100)
        assert cost is None

    def test_zero_tokens(self):
        """Zero tokens should return 0.0 cost."""
        cost = compute_cost("gpt-4o", input_tokens=0, output_tokens=0)
        assert cost == 0.0

    def test_openai_gpt4o_pricing(self):
        """OpenAI gpt-4o pricing is correct."""
        cost = compute_cost("gpt-4o", input_tokens=1_000_000, output_tokens=1_000_000)
        assert cost is not None
        # 2.5 + 10.0 = 12.5
        assert abs(cost - 12.5) < 1e-6

    def test_deepseek_pricing(self):
        """DeepSeek chat pricing is correct."""
        cost = compute_cost("deepseek/deepseek-chat", input_tokens=1_000_000, output_tokens=1_000_000)
        assert cost is not None
        # 0.14 + 0.28 = 0.42
        assert abs(cost - 0.42) < 1e-6

    def test_case_insensitive_lookup(self):
        """Lookup should be case-insensitive."""
        p1 = _lookup_pricing("Anthropic/Claude-Sonnet-4")
        p2 = _lookup_pricing("anthropic/claude-sonnet-4")
        assert p1 is not None
        assert p2 is not None
        assert p1.input_per_m == p2.input_per_m

    def test_openrouter_prefix(self):
        """OpenRouter prefixed models match."""
        cost = compute_cost("openrouter/anthropic/claude-sonnet-4-20250514", input_tokens=1_000_000)
        assert cost is not None
        assert abs(cost - 3.0) < 1e-6

    def test_groq_model(self):
        """Groq models have pricing."""
        cost = compute_cost("groq/llama-3.3-70b-versatile", input_tokens=1_000_000, output_tokens=1_000_000)
        assert cost is not None
        # 0.59 + 0.79 = 1.38
        assert abs(cost - 1.38) < 1e-6


# ═══════════════════════════════════════════════════════════
# TaskState in EventStore tests
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def tmp_store(tmp_path):
    """Create an EventStore with a temporary database."""
    store = EventStore(tmp_path / "test_events.db")
    yield store
    store.close()


class TestTaskBoard:
    """Tests for task_board derived state in EventStore."""

    @pytest.mark.asyncio
    async def test_agent_started_creates_task(self, tmp_store):
        """agent.started creates an active task on the board."""
        await tmp_store.handle_event(Event(
            event_type=EventType.AGENT_STARTED,
            agent_id="coder",
            payload={"preview": "Write unit tests"},
        ))
        assert "agent:coder" in tmp_store.task_board
        task = tmp_store.task_board["agent:coder"]
        assert task.status == "active"
        assert task.title == "Write unit tests"
        assert task.agent_id == "coder"

    @pytest.mark.asyncio
    async def test_agent_completed_marks_task_done(self, tmp_store):
        """agent.completed transitions task to done."""
        await tmp_store.handle_event(Event(
            event_type=EventType.AGENT_STARTED,
            agent_id="coder",
            payload={"preview": "Build feature"},
        ))
        await tmp_store.handle_event(Event(
            event_type=EventType.AGENT_COMPLETED,
            agent_id="coder",
        ))
        task = tmp_store.task_board["agent:coder"]
        assert task.status == "done"
        assert task.completed_at is not None

    @pytest.mark.asyncio
    async def test_chain_delegated_creates_task(self, tmp_store):
        """chain.delegated creates an active chain task."""
        await tmp_store.handle_event(Event(
            event_type=EventType.CHAIN_DELEGATED,
            agent_id="leader",
            chain_id="api-docs",
            payload={"from_agent": "leader"},
        ))
        assert "chain:api-docs" in tmp_store.task_board
        task = tmp_store.task_board["chain:api-docs"]
        assert task.status == "active"
        assert "api-docs" in task.title
        assert "leader" in task.title

    @pytest.mark.asyncio
    async def test_chain_awaiting_approval_makes_pending(self, tmp_store):
        """chain.awaiting_approval transitions chain task to pending."""
        await tmp_store.handle_event(Event(
            event_type=EventType.CHAIN_DELEGATED,
            chain_id="review-pr",
        ))
        await tmp_store.handle_event(Event(
            event_type=EventType.CHAIN_AWAITING_APPROVAL,
            chain_id="review-pr",
        ))
        task = tmp_store.task_board["chain:review-pr"]
        assert task.status == "pending"
        assert "approval" in task.title.lower()

    @pytest.mark.asyncio
    async def test_chain_completed_marks_task_done(self, tmp_store):
        """chain.completed transitions chain task to done."""
        await tmp_store.handle_event(Event(
            event_type=EventType.CHAIN_DELEGATED,
            chain_id="deploy",
        ))
        await tmp_store.handle_event(Event(
            event_type=EventType.CHAIN_COMPLETED,
            chain_id="deploy",
        ))
        task = tmp_store.task_board["chain:deploy"]
        assert task.status == "done"
        assert task.completed_at is not None

    @pytest.mark.asyncio
    async def test_multiple_agents_tracked(self, tmp_store):
        """Multiple agents create separate task entries."""
        await tmp_store.handle_event(Event(
            event_type=EventType.AGENT_STARTED,
            agent_id="coder",
            payload={"preview": "Coding"},
        ))
        await tmp_store.handle_event(Event(
            event_type=EventType.AGENT_STARTED,
            agent_id="reviewer",
            payload={"preview": "Reviewing"},
        ))
        assert len(tmp_store.task_board) == 2
        assert tmp_store.task_board["agent:coder"].title == "Coding"
        assert tmp_store.task_board["agent:reviewer"].title == "Reviewing"

    @pytest.mark.asyncio
    async def test_task_board_survives_rebuild(self, tmp_path):
        """Task board state rebuilds correctly from persisted events."""
        db_path = tmp_path / "rebuild_tasks.db"

        store1 = EventStore(db_path)
        await store1.handle_event(Event(
            event_type=EventType.AGENT_STARTED,
            agent_id="coder",
            payload={"preview": "Building"},
        ))
        await store1.handle_event(Event(
            event_type=EventType.CHAIN_DELEGATED,
            chain_id="deploy",
            payload={"from_agent": "leader"},
        ))
        store1.close()

        store2 = EventStore(db_path)
        assert "agent:coder" in store2.task_board
        assert "chain:deploy" in store2.task_board
        assert store2.task_board["agent:coder"].status == "active"
        store2.close()


# ═══════════════════════════════════════════════════════════
# Cost tracking tests
# ═══════════════════════════════════════════════════════════


class TestCostTracking:
    """Tests for cost_usd tracking in EventStore."""

    @pytest.mark.asyncio
    async def test_cost_accumulates_in_agent_state(self, tmp_store):
        """cost_usd from usage events accumulates in agent total_cost_usd."""
        await tmp_store.handle_event(Event(
            event_type=EventType.AGENT_STARTED,
            agent_id="default",
        ))
        await tmp_store.handle_event(Event(
            event_type=EventType.USAGE_TRACKED,
            agent_id="default",
            payload={"input_tokens": 100, "output_tokens": 50, "cost_usd": 0.005},
        ))
        await tmp_store.handle_event(Event(
            event_type=EventType.USAGE_TRACKED,
            agent_id="default",
            payload={"input_tokens": 200, "output_tokens": 80, "cost_usd": 0.01},
        ))
        state = tmp_store.active_agents["default"]
        assert state.total_tokens == 430
        assert abs(state.total_cost_usd - 0.015) < 1e-8

    @pytest.mark.asyncio
    async def test_null_cost_does_not_accumulate(self, tmp_store):
        """None cost_usd doesn't affect total_cost_usd."""
        await tmp_store.handle_event(Event(
            event_type=EventType.AGENT_STARTED,
            agent_id="default",
        ))
        await tmp_store.handle_event(Event(
            event_type=EventType.USAGE_TRACKED,
            agent_id="default",
            payload={"input_tokens": 100, "output_tokens": 50, "cost_usd": None},
        ))
        state = tmp_store.active_agents["default"]
        assert state.total_cost_usd == 0.0

    @pytest.mark.asyncio
    async def test_cost_rebuilds_on_restart(self, tmp_path):
        """Cost state survives EventStore restart."""
        db_path = tmp_path / "cost_rebuild.db"

        store1 = EventStore(db_path)
        await store1.handle_event(Event(
            event_type=EventType.AGENT_STARTED,
            agent_id="coder",
        ))
        await store1.handle_event(Event(
            event_type=EventType.USAGE_TRACKED,
            agent_id="coder",
            payload={"input_tokens": 500, "output_tokens": 200, "cost_usd": 0.025},
        ))
        store1.close()

        store2 = EventStore(db_path)
        state = store2.active_agents["coder"]
        assert abs(state.total_cost_usd - 0.025) < 1e-8
        store2.close()


# ═══════════════════════════════════════════════════════════
# UsagePayload model tests
# ═══════════════════════════════════════════════════════════


class TestUsagePayload:
    """Tests for the UsagePayload model."""

    def test_cost_usd_field(self):
        """UsagePayload includes cost_usd field."""
        payload = UsagePayload(
            model="gpt-4o",
            input_tokens=1000,
            output_tokens=500,
            cost_usd=0.0125,
        )
        dumped = payload.model_dump()
        assert dumped["cost_usd"] == 0.0125

    def test_cost_usd_optional(self):
        """cost_usd defaults to None."""
        payload = UsagePayload(model="gpt-4o", input_tokens=100)
        assert payload.cost_usd is None


# ═══════════════════════════════════════════════════════════
# TaskState model tests
# ═══════════════════════════════════════════════════════════


class TestTaskStateModel:
    """Tests for the TaskState class."""

    def test_task_state_init(self):
        """TaskState initializes with correct defaults."""
        now = datetime.now(UTC)
        task = TaskState(
            task_id="agent:coder",
            title="Building feature",
            agent_id="coder",
            chain_id=None,
            status="active",
            started_at=now,
        )
        assert task.task_id == "agent:coder"
        assert task.status == "active"
        assert task.completed_at is None
        assert task.agent_id == "coder"

    def test_task_state_mutable(self):
        """TaskState fields can be updated."""
        now = datetime.now(UTC)
        task = TaskState(
            task_id="chain:deploy",
            title="Deploy chain",
            agent_id="leader",
            chain_id="deploy",
            status="active",
            started_at=now,
        )
        task.status = "done"
        task.completed_at = datetime.now(UTC)
        assert task.status == "done"
        assert task.completed_at is not None
