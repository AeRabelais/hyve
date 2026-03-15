"""Demo data generator for the nanobot dashboard.

Injects realistic mock events into the EventEmitter so all panels
are populated with representative data when viewing the dashboard.

Usage::

    from nanobot.dashboard.demo import inject_demo_data
    await inject_demo_data(emitter)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from nanobot.events.emitter import EventEmitter
from nanobot.events.models import Event, EventType


async def inject_demo_data(emitter: EventEmitter) -> None:
    """Emit a sequence of realistic mock events to populate the dashboard."""
    now = datetime.now(UTC)

    events = _build_demo_events(now)
    for event in events:
        await emitter.emit(event)

    # Start a background task that emits periodic events
    asyncio.create_task(_live_demo_loop(emitter, now))


async def _live_demo_loop(emitter: EventEmitter, base_time: datetime) -> None:
    """Periodically emit events to simulate live activity."""
    iteration = 4
    while True:
        await asyncio.sleep(5)
        now = datetime.now(UTC)

        # Reviewer keeps iterating
        await emitter.emit(Event(
            timestamp=now,
            event_type=EventType.AGENT_ITERATION,
            agent_id="reviewer",
            chain_id="auth-fix",
            payload={"iteration": iteration},
        ))
        iteration += 1

        await asyncio.sleep(3)
        # Tool calls from reviewer
        await emitter.emit(Event(
            timestamp=datetime.now(UTC),
            event_type=EventType.TOOL_CALLED,
            agent_id="reviewer",
            chain_id="auth-fix",
            payload={"tool_name": "read_file", "args": {"path": "src/auth/validate.ts"}},
        ))

        await asyncio.sleep(1)
        await emitter.emit(Event(
            timestamp=datetime.now(UTC),
            event_type=EventType.TOOL_RESULT,
            agent_id="reviewer",
            chain_id="auth-fix",
            payload={"tool_name": "read_file", "success": True, "duration_ms": 12.5},
        ))

        await asyncio.sleep(4)
        # Usage tracking
        await emitter.emit(Event(
            timestamp=datetime.now(UTC),
            event_type=EventType.USAGE_TRACKED,
            agent_id="reviewer",
            payload={"model": "claude-sonnet-4", "input_tokens": 340, "output_tokens": 180},
        ))

        if iteration > 8:
            iteration = 4  # Reset for continuous demo


def _build_demo_events(now: datetime) -> list[Event]:
    """Build the initial batch of demo events."""
    events: list[Event] = []

    # ── Chain #api-docs (completed 12 min ago) ──
    t = now - timedelta(minutes=16)

    events.append(Event(
        timestamp=t,
        event_type=EventType.CHAIN_DELEGATED,
        agent_id="coder",
        chain_id="api-docs",
        payload={"from_agent": "user", "to_agent": "coder", "context": "Document the REST API endpoints"},
    ))
    events.append(Event(
        timestamp=t,
        event_type=EventType.AGENT_STARTED,
        agent_id="coder",
        chain_id="api-docs",
        payload={"model": "claude-sonnet-4"},
    ))
    t += timedelta(seconds=30)
    events.append(Event(
        timestamp=t,
        event_type=EventType.TOOL_CALLED,
        agent_id="coder",
        chain_id="api-docs",
        payload={"tool_name": "list_dir", "args": {"path": "src/routes/"}},
    ))
    events.append(Event(
        timestamp=t + timedelta(seconds=1),
        event_type=EventType.TOOL_RESULT,
        agent_id="coder",
        chain_id="api-docs",
        payload={"tool_name": "list_dir", "success": True, "duration_ms": 8.2},
    ))
    t += timedelta(minutes=2)
    events.append(Event(
        timestamp=t,
        event_type=EventType.AGENT_COMPLETED,
        agent_id="coder",
        chain_id="api-docs",
        payload={"tokens": 1840, "elapsed": 120000},
    ))
    events.append(Event(
        timestamp=t,
        event_type=EventType.USAGE_TRACKED,
        agent_id="coder",
        payload={"model": "claude-sonnet-4", "input_tokens": 1200, "output_tokens": 640},
    ))

    # Writer in api-docs
    t += timedelta(seconds=5)
    events.append(Event(
        timestamp=t,
        event_type=EventType.CHAIN_DELEGATED,
        agent_id="writer",
        chain_id="api-docs",
        payload={"from_agent": "coder", "to_agent": "writer"},
    ))
    events.append(Event(
        timestamp=t,
        event_type=EventType.AGENT_STARTED,
        agent_id="writer",
        chain_id="api-docs",
        payload={"model": "gpt-4o"},
    ))
    t += timedelta(minutes=2)
    events.append(Event(
        timestamp=t,
        event_type=EventType.TOOL_CALLED,
        agent_id="writer",
        chain_id="api-docs",
        payload={"tool_name": "write_file", "args": {"path": "docs/api.md"}},
    ))
    events.append(Event(
        timestamp=t + timedelta(seconds=1),
        event_type=EventType.TOOL_RESULT,
        agent_id="writer",
        chain_id="api-docs",
        payload={"tool_name": "write_file", "success": True, "duration_ms": 15.0},
    ))
    t += timedelta(seconds=30)
    events.append(Event(
        timestamp=t,
        event_type=EventType.AGENT_COMPLETED,
        agent_id="writer",
        chain_id="api-docs",
        payload={"tokens": 2200, "elapsed": 150000},
    ))
    events.append(Event(
        timestamp=t,
        event_type=EventType.USAGE_TRACKED,
        agent_id="writer",
        payload={"model": "gpt-4o", "input_tokens": 1400, "output_tokens": 800},
    ))
    events.append(Event(
        timestamp=t,
        event_type=EventType.CHAIN_COMPLETED,
        agent_id="writer",
        chain_id="api-docs",
        payload={"agents_involved": ["coder", "writer"], "total_tokens": 4040, "elapsed": 262000},
    ))

    # ── Heartbeat check ──
    t = now - timedelta(minutes=8)
    events.append(Event(
        timestamp=t,
        event_type=EventType.HEARTBEAT_CHECKED,
        payload={"action": "skip", "had_content": True, "duration_ms": 450.0},
    ))

    # ── Chain #paper-stats (awaiting approval) ──
    t = now - timedelta(minutes=5)
    events.append(Event(
        timestamp=t,
        event_type=EventType.CHAIN_DELEGATED,
        agent_id="validator",
        chain_id="paper-stats",
        payload={"from_agent": "user", "to_agent": "validator"},
    ))
    events.append(Event(
        timestamp=t,
        event_type=EventType.AGENT_STARTED,
        agent_id="validator",
        chain_id="paper-stats",
        payload={"model": "claude-sonnet-4"},
    ))
    t += timedelta(seconds=45)
    events.append(Event(
        timestamp=t,
        event_type=EventType.TOOL_CALLED,
        agent_id="validator",
        chain_id="paper-stats",
        payload={"tool_name": "read_file", "args": {"path": "paper/results.md"}},
    ))
    events.append(Event(
        timestamp=t + timedelta(seconds=1),
        event_type=EventType.TOOL_RESULT,
        agent_id="validator",
        chain_id="paper-stats",
        payload={"tool_name": "read_file", "success": True, "duration_ms": 9.1},
    ))
    t += timedelta(seconds=30)
    events.append(Event(
        timestamp=t,
        event_type=EventType.MEMORY_WRITTEN,
        agent_id="validator",
        chain_id="paper-stats",
        payload={"source": "agent", "content_preview": "Paper uses Bonferroni but has 47 comparisons"},
    ))
    t += timedelta(seconds=10)
    events.append(Event(
        timestamp=t,
        event_type=EventType.CHAIN_AWAITING_APPROVAL,
        agent_id="validator",
        chain_id="paper-stats",
        payload={"pending_agents": ["stats-checker", "methods-reviewer"]},
    ))
    events.append(Event(
        timestamp=t,
        event_type=EventType.USAGE_TRACKED,
        agent_id="validator",
        payload={"model": "claude-sonnet-4", "input_tokens": 890, "output_tokens": 320},
    ))

    # ── Chain #auth-fix (running — coder done, reviewer active) ──
    t = now - timedelta(minutes=2, seconds=14)
    events.append(Event(
        timestamp=t,
        event_type=EventType.CHAIN_DELEGATED,
        agent_id="coder",
        chain_id="auth-fix",
        payload={"from_agent": "user", "to_agent": "coder", "context": "Fix token validation bug in auth.ts"},
    ))
    events.append(Event(
        timestamp=t,
        event_type=EventType.AGENT_STARTED,
        agent_id="coder",
        chain_id="auth-fix",
        payload={"model": "claude-sonnet-4"},
    ))

    # Coder tool calls
    for i, (tool, args) in enumerate([
        ("read_file", {"path": "src/auth/auth.ts"}),
        ("read_file", {"path": "src/auth/auth.test.ts"}),
        ("shell", {"command": "npm test -- --filter auth"}),
        ("write_file", {"path": "src/auth/auth.ts"}),
        ("write_file", {"path": "src/auth/auth.test.ts"}),
    ]):
        tt = t + timedelta(seconds=15 + i * 12)
        events.append(Event(
            timestamp=tt,
            event_type=EventType.TOOL_CALLED,
            agent_id="coder",
            chain_id="auth-fix",
            payload={"tool_name": tool, "args": args},
        ))
        events.append(Event(
            timestamp=tt + timedelta(seconds=2),
            event_type=EventType.TOOL_RESULT,
            agent_id="coder",
            chain_id="auth-fix",
            payload={"tool_name": tool, "success": True, "duration_ms": 15.0 + i * 5},
        ))

    t += timedelta(minutes=1, seconds=42)
    events.append(Event(
        timestamp=t,
        event_type=EventType.AGENT_COMPLETED,
        agent_id="coder",
        chain_id="auth-fix",
        payload={"tokens": 2140, "elapsed": 102000},
    ))
    events.append(Event(
        timestamp=t,
        event_type=EventType.USAGE_TRACKED,
        agent_id="coder",
        payload={"model": "claude-sonnet-4", "input_tokens": 1400, "output_tokens": 740},
    ))

    # Delegation to reviewer
    t += timedelta(seconds=7)
    events.append(Event(
        timestamp=t,
        event_type=EventType.CHAIN_DELEGATED,
        agent_id="reviewer",
        chain_id="auth-fix",
        payload={"from_agent": "coder", "to_agent": "reviewer", "context": "Review auth.ts changes for edge cases"},
    ))
    events.append(Event(
        timestamp=t,
        event_type=EventType.AGENT_STARTED,
        agent_id="reviewer",
        chain_id="auth-fix",
        payload={"model": "claude-sonnet-4"},
    ))

    # Reviewer iterations
    for i in range(3):
        tt = t + timedelta(seconds=5 + i * 6)
        events.append(Event(
            timestamp=tt,
            event_type=EventType.AGENT_ITERATION,
            agent_id="reviewer",
            chain_id="auth-fix",
            payload={"iteration": i + 1},
        ))

    # Reviewer tool calls
    for tool, args in [
        ("list_dir", {"path": "src/auth/"}),
        ("read_file", {"path": "src/auth/auth.test.ts"}),
        ("read_file", {"path": "src/auth/auth.ts"}),
    ]:
        t += timedelta(seconds=6)
        events.append(Event(
            timestamp=t,
            event_type=EventType.TOOL_CALLED,
            agent_id="reviewer",
            chain_id="auth-fix",
            payload={"tool_name": tool, "args": args},
        ))
        events.append(Event(
            timestamp=t + timedelta(seconds=1),
            event_type=EventType.TOOL_RESULT,
            agent_id="reviewer",
            chain_id="auth-fix",
            payload={"tool_name": tool, "success": True, "duration_ms": 11.3},
        ))

    events.append(Event(
        timestamp=t + timedelta(seconds=2),
        event_type=EventType.USAGE_TRACKED,
        agent_id="reviewer",
        payload={"model": "claude-sonnet-4", "input_tokens": 560, "output_tokens": 280},
    ))

    # ── Cron job ──
    t = now - timedelta(minutes=10)
    events.append(Event(
        timestamp=t,
        event_type=EventType.CRON_TRIGGERED,
        payload={"job_id": "daily-brief", "job_name": "Daily Briefing", "schedule_kind": "cron", "status": "ok", "duration_ms": 3200.0},
    ))

    # Sort by timestamp
    events.sort(key=lambda e: e.timestamp)
    return events
