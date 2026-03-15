"""Event types and Pydantic models for the nanobot event system."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """All event types emitted by nanobot components."""

    # Agent lifecycle
    AGENT_STARTED = "agent.started"
    AGENT_ITERATION = "agent.iteration"
    AGENT_COMPLETED = "agent.completed"

    # Tool execution
    TOOL_CALLED = "tool.called"
    TOOL_RESULT = "tool.result"

    # Routing (Phase 2)
    MESSAGE_ROUTED = "message.routed"

    # Chain coordination (Phase 2)
    CHAIN_DELEGATED = "chain.delegated"
    CHAIN_AWAITING_APPROVAL = "chain.awaiting_approval"
    CHAIN_APPROVED = "chain.approved"
    CHAIN_COMPLETED = "chain.completed"
    CHAIN_CHECKPOINT = "chain.checkpoint"

    # Memory (Phase 3)
    MEMORY_WRITTEN = "memory.written"

    # Scheduling
    HEARTBEAT_CHECKED = "heartbeat.checked"
    CRON_TRIGGERED = "cron.triggered"

    # Usage / cost tracking
    USAGE_TRACKED = "usage.tracked"


class Event(BaseModel):
    """Immutable event record. Created once, never modified."""

    id: Optional[int] = None  # Set by EventStore on persist
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event_type: EventType
    agent_id: Optional[str] = None  # Which agent emitted this
    chain_id: Optional[str] = None  # Which chain this belongs to
    payload: dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": True}


# ─── Typed payload helpers ─────────────────────────────────
# These aren't stored separately — they're convenience builders
# that produce structured payloads via .model_dump().


class ToolCallPayload(BaseModel):
    """Payload for tool.called / tool.result events."""

    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    duration_ms: Optional[float] = None
    success: Optional[bool] = None
    error: Optional[str] = None


class UsagePayload(BaseModel):
    """Payload for usage.tracked events."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: Optional[float] = None


class ChainCheckpointPayload(BaseModel):
    """Payload for chain.checkpoint events."""

    intent: str  # What the chain is about to do
    state: dict[str, Any]  # Current state snapshot
    expected_outcome: Optional[str] = None
    files_modified: list[str] = Field(default_factory=list)
