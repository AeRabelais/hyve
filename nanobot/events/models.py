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

    # Routing — emitted by Router.parse() on every inbound message dispatch
    MESSAGE_ROUTED = "message.routed"

    # Chain coordination — emitted by ChainManager during multi-agent chains
    CHAIN_DELEGATED = "chain.delegated"
    CHAIN_AWAITING_APPROVAL = "chain.awaiting_approval"
    CHAIN_APPROVED = "chain.approved"
    CHAIN_COMPLETED = "chain.completed"
    CHAIN_CHECKPOINT = "chain.checkpoint"

    # Memory — emitted by the layered memory system (distiller, generator)
    MEMORY_WRITTEN = "memory.written"

    # Scheduling — emitted by HeartbeatService and CronService
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


class HeartbeatPayload(BaseModel):
    """Payload for heartbeat.checked events."""

    action: str  # "skip" or "run"
    tasks: str = ""  # Summary of tasks (when action=run)
    had_content: bool = True  # Whether HEARTBEAT.md existed and had content
    duration_ms: Optional[float] = None
    error: Optional[str] = None


class CronTriggeredPayload(BaseModel):
    """Payload for cron.triggered events."""

    job_id: str
    job_name: str
    schedule_kind: str  # "at", "every", or "cron"
    message: str = ""
    status: str = "ok"  # "ok" or "error"
    error: Optional[str] = None
    duration_ms: Optional[float] = None
    deliver: bool = False
    channel: Optional[str] = None
