"""SQLite-backed event persistence and in-memory derived state."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from .models import Event, EventType

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp  TEXT    NOT NULL,
    event_type TEXT    NOT NULL,
    agent_id   TEXT,
    chain_id   TEXT,
    payload    TEXT    NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_events_time   ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_type   ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_agent  ON events(agent_id);
CREATE INDEX IF NOT EXISTS idx_events_chain  ON events(chain_id);
"""


class EventStore:
    """
    SQLite-backed event persistence + in-memory derived state.

    The store is both a listener (receives events from EventEmitter)
    and a query interface (serves dashboard, memory system, etc).
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.executescript(SCHEMA)

        # ── Derived state (rebuilt from events on startup) ──
        self.active_agents: dict[str, AgentState] = {}
        self.active_chains: dict[str, ChainState] = {}
        self.last_heartbeat: HeartbeatState | None = None
        self.cron_jobs: dict[str, CronJobState] = {}
        self.task_board: dict[str, TaskState] = {}

        self._rebuild_state()

    # ── EventEmitter listener ──────────────────────────────

    async def handle_event(self, event: Event) -> None:
        """Primary listener — register with ``emitter.on("*", store.handle_event)``."""
        self._persist(event)
        self._update_derived_state(event)

    def _persist(self, event: Event) -> None:
        """Write a single event row to SQLite."""
        self._conn.execute(
            "INSERT INTO events (timestamp, event_type, agent_id, chain_id, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                event.timestamp.isoformat(),
                event.event_type.value,
                event.agent_id,
                event.chain_id,
                json.dumps(event.payload),
            ),
        )
        self._conn.commit()

    # ── Derived state management ───────────────────────────

    def _update_derived_state(self, event: Event) -> None:
        """Update in-memory state from a single new event."""
        et = event.event_type

        if et == EventType.AGENT_STARTED:
            self.active_agents[event.agent_id] = AgentState(
                agent_id=event.agent_id,
                status="running",
                started_at=event.timestamp,
                chain_id=event.chain_id,
                iteration=0,
            )
            # Task board: agent becomes an active task
            if event.agent_id:
                title = event.payload.get("preview", f"Agent {event.agent_id} processing")
                self.task_board[f"agent:{event.agent_id}"] = TaskState(
                    task_id=f"agent:{event.agent_id}",
                    title=title[:120] if title else f"Agent {event.agent_id} processing",
                    agent_id=event.agent_id,
                    chain_id=event.chain_id,
                    status="active",
                    started_at=event.timestamp,
                )

        elif et == EventType.AGENT_ITERATION:
            if state := self.active_agents.get(event.agent_id):
                state.iteration += 1

        elif et == EventType.AGENT_COMPLETED:
            if state := self.active_agents.get(event.agent_id):
                state.status = "idle"
                state.completed_at = event.timestamp
            # Task board: mark agent task done
            task_key = f"agent:{event.agent_id}"
            if task := self.task_board.get(task_key):
                task.status = "done"
                task.completed_at = event.timestamp

        # Chain derived state — updated by ChainManager events
        elif et == EventType.CHAIN_DELEGATED:
            chain_id = event.chain_id
            if chain_id and chain_id not in self.active_chains:
                self.active_chains[chain_id] = ChainState(
                    chain_id=chain_id,
                    status="active",
                    started_at=event.timestamp,
                )
            # Task board: chain becomes an active task
            if chain_id:
                from_agent = event.payload.get("from_agent", "")
                title = f"Chain #{chain_id}"
                if from_agent:
                    title += f" (from {from_agent})"
                self.task_board[f"chain:{chain_id}"] = TaskState(
                    task_id=f"chain:{chain_id}",
                    title=title,
                    agent_id=event.agent_id,
                    chain_id=chain_id,
                    status="active",
                    started_at=event.timestamp,
                )

        elif et == EventType.CHAIN_AWAITING_APPROVAL:
            if state := self.active_chains.get(event.chain_id):
                state.status = "awaiting_approval"
            # Task board: chain task becomes pending (waiting for approval)
            if event.chain_id:
                task_key = f"chain:{event.chain_id}"
                if task := self.task_board.get(task_key):
                    task.status = "pending"
                    task.title = f"Awaiting approval: #{event.chain_id}"

        elif et == EventType.CHAIN_COMPLETED:
            if state := self.active_chains.get(event.chain_id):
                state.status = "completed"
                state.completed_at = event.timestamp
            # Task board: mark chain task done
            if event.chain_id:
                task_key = f"chain:{event.chain_id}"
                if task := self.task_board.get(task_key):
                    task.status = "done"
                    task.completed_at = event.timestamp

        # Usage events update agent cumulative stats
        elif et == EventType.USAGE_TRACKED:
            if state := self.active_agents.get(event.agent_id):
                state.total_tokens += event.payload.get("input_tokens", 0)
                state.total_tokens += event.payload.get("output_tokens", 0)
                cost = event.payload.get("cost_usd")
                if cost is not None:
                    state.total_cost_usd += cost

        # Heartbeat tracking
        elif et == EventType.HEARTBEAT_CHECKED:
            self.last_heartbeat = HeartbeatState(
                action=event.payload.get("action", "skip"),
                checked_at=event.timestamp,
                had_content=event.payload.get("had_content", True),
                error=event.payload.get("error"),
            )

        # Cron tracking — last execution per job
        elif et == EventType.CRON_TRIGGERED:
            job_id = event.payload.get("job_id", "")
            if job_id:
                self.cron_jobs[job_id] = CronJobState(
                    job_id=job_id,
                    job_name=event.payload.get("job_name", ""),
                    last_triggered_at=event.timestamp,
                    last_status=event.payload.get("status", "ok"),
                    last_error=event.payload.get("error"),
                )

    def _rebuild_state(self) -> None:
        """Replay all stored events to rebuild derived state on startup."""
        cursor = self._conn.execute(
            "SELECT timestamp, event_type, agent_id, chain_id, payload "
            "FROM events ORDER BY id ASC"
        )
        count = 0
        for row in cursor:
            try:
                event = Event(
                    timestamp=datetime.fromisoformat(row[0]),
                    event_type=EventType(row[1]),
                    agent_id=row[2],
                    chain_id=row[3],
                    payload=json.loads(row[4]),
                )
                self._update_derived_state(event)
                count += 1
            except (ValueError, KeyError) as e:
                logger.warning("Skipping malformed event row: {}", e)

        logger.info(
            "EventStore rebuilt: {} events replayed, {} agents, {} chains",
            count,
            len(self.active_agents),
            len(self.active_chains),
        )

    # ── Query interface ────────────────────────────────────

    def query(
        self,
        event_type: Optional[EventType] = None,
        agent_id: Optional[str] = None,
        chain_id: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[Event]:
        """Flexible query for dashboard, memory system, debugging."""
        clauses: list[str] = []
        params: list[Any] = []

        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type.value)
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if chain_id:
            clauses.append("chain_id = ?")
            params.append(chain_id)
        if since:
            clauses.append("timestamp >= ?")
            params.append(since.isoformat())

        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        sql = (
            f"SELECT id, timestamp, event_type, agent_id, chain_id, payload "
            f"FROM events {where} ORDER BY id DESC LIMIT ?"
        )
        params.append(limit)

        rows = self._conn.execute(sql, params).fetchall()
        return [
            Event(
                id=r[0],
                timestamp=datetime.fromisoformat(r[1]),
                event_type=EventType(r[2]),
                agent_id=r[3],
                chain_id=r[4],
                payload=json.loads(r[5]),
            )
            for r in rows
        ]

    def close(self) -> None:
        """Close the SQLite connection."""
        self._conn.close()


# ── Derived state models ───────────────────────────────────


class AgentState:
    """Mutable in-memory state for an active agent."""

    def __init__(
        self,
        agent_id: str,
        status: str,
        started_at: datetime,
        chain_id: Optional[str] = None,
        iteration: int = 0,
    ) -> None:
        self.agent_id = agent_id
        self.status = status
        self.started_at = started_at
        self.completed_at: Optional[datetime] = None
        self.chain_id = chain_id
        self.iteration = iteration
        self.total_tokens: int = 0
        self.total_cost_usd: float = 0.0


class ChainState:
    """Mutable in-memory state for an active chain."""

    def __init__(
        self,
        chain_id: str,
        status: str,
        started_at: datetime,
    ) -> None:
        self.chain_id = chain_id
        self.status = status
        self.started_at = started_at
        self.completed_at: Optional[datetime] = None


class HeartbeatState:
    """Last-known heartbeat state (overwritten each tick)."""

    def __init__(
        self,
        action: str,
        checked_at: datetime,
        had_content: bool = True,
        error: Optional[str] = None,
    ) -> None:
        self.action = action
        self.checked_at = checked_at
        self.had_content = had_content
        self.error = error


class CronJobState:
    """Last-known execution state per cron job ID."""

    def __init__(
        self,
        job_id: str,
        job_name: str,
        last_triggered_at: datetime,
        last_status: str = "ok",
        last_error: Optional[str] = None,
    ) -> None:
        self.job_id = job_id
        self.job_name = job_name
        self.last_triggered_at = last_triggered_at
        self.last_status = last_status
        self.last_error = last_error


class TaskState:
    """Mutable in-memory state for a task on the Kanban board.

    Tasks are derived from agent and chain lifecycle events:
      * ``agent.started``  → active task  (key: ``agent:{agent_id}``)
      * ``agent.completed`` → done task
      * ``chain.delegated`` → active task  (key: ``chain:{chain_id}``)
      * ``chain.awaiting_approval`` → pending task
      * ``chain.completed`` → done task
    """

    def __init__(
        self,
        task_id: str,
        title: str,
        agent_id: Optional[str],
        chain_id: Optional[str],
        status: str,
        started_at: datetime,
    ) -> None:
        self.task_id = task_id
        self.title = title
        self.agent_id = agent_id
        self.chain_id = chain_id
        self.status = status  # "pending" | "active" | "done"
        self.started_at = started_at
        self.completed_at: Optional[datetime] = None
