"""Tests for heartbeat and cron event emission (Phase 4).

Covers:
- HeartbeatPayload / CronTriggeredPayload model construction
- HeartbeatService emits heartbeat.checked on tick (skip/run/error/no-content)
- HeartbeatService emits heartbeat.checked on trigger_now
- CronService emits cron.triggered on _execute_job (ok/error)
- EventStore derived state updates for heartbeat/cron events
- Emitter-less services still work (no emitter = no crash)
- End-to-end: emit → store → derived state round-trip
"""

import asyncio

import pytest

from nanobot.events.emitter import EventEmitter
from nanobot.events.models import (
    CronTriggeredPayload,
    Event,
    EventType,
    HeartbeatPayload,
)
from nanobot.events.store import EventStore
from nanobot.providers.base import LLMResponse, ToolCallRequest


# ─── Helpers ───────────────────────────────────────────────


class DummyProvider:
    """LLM provider that returns pre-configured responses."""

    def __init__(self, responses: list[LLMResponse]):
        self._responses = list(responses)

    async def chat(self, *args, **kwargs) -> LLMResponse:
        if self._responses:
            return self._responses.pop(0)
        return LLMResponse(content="", tool_calls=[])


def _make_skip_response() -> LLMResponse:
    return LLMResponse(
        content="",
        tool_calls=[
            ToolCallRequest(
                id="hb_1",
                name="heartbeat",
                arguments={"action": "skip"},
            )
        ],
    )


def _make_run_response(tasks: str = "check open tasks") -> LLMResponse:
    return LLMResponse(
        content="",
        tool_calls=[
            ToolCallRequest(
                id="hb_1",
                name="heartbeat",
                arguments={"action": "run", "tasks": tasks},
            )
        ],
    )


def _collect_events(emitter: EventEmitter, event_type: EventType | str = "*") -> list[Event]:
    """Register a listener that collects events into a list."""
    collected: list[Event] = []

    async def _listener(event: Event) -> None:
        collected.append(event)

    emitter.on(event_type, _listener)
    return collected


# ─── Payload model tests ──────────────────────────────────


class TestPayloadModels:
    def test_heartbeat_payload_defaults(self):
        p = HeartbeatPayload(action="skip")
        assert p.action == "skip"
        assert p.tasks == ""
        assert p.had_content is True
        assert p.duration_ms is None
        assert p.error is None

    def test_heartbeat_payload_full(self):
        p = HeartbeatPayload(
            action="run",
            tasks="deploy app",
            had_content=True,
            duration_ms=42.5,
            error=None,
        )
        d = p.model_dump()
        assert d["action"] == "run"
        assert d["tasks"] == "deploy app"
        assert d["duration_ms"] == 42.5

    def test_heartbeat_payload_error(self):
        p = HeartbeatPayload(action="run", error="timeout")
        assert p.error == "timeout"

    def test_cron_payload_defaults(self):
        p = CronTriggeredPayload(
            job_id="abc",
            job_name="test",
            schedule_kind="every",
        )
        assert p.status == "ok"
        assert p.error is None
        assert p.deliver is False
        assert p.channel is None

    def test_cron_payload_full(self):
        p = CronTriggeredPayload(
            job_id="abc",
            job_name="daily-report",
            schedule_kind="cron",
            message="generate report",
            status="ok",
            duration_ms=1234.5,
            deliver=True,
            channel="telegram",
        )
        d = p.model_dump()
        assert d["job_id"] == "abc"
        assert d["schedule_kind"] == "cron"
        assert d["deliver"] is True
        assert d["channel"] == "telegram"

    def test_cron_payload_error(self):
        p = CronTriggeredPayload(
            job_id="x",
            job_name="fail",
            schedule_kind="at",
            status="error",
            error="connection lost",
        )
        assert p.status == "error"
        assert p.error == "connection lost"


# ─── HeartbeatService event emission ──────────────────────


class TestHeartbeatEvents:
    @pytest.mark.asyncio
    async def test_tick_no_content_emits_skip(self, tmp_path):
        """When HEARTBEAT.md doesn't exist, emit skip with had_content=False."""
        from nanobot.heartbeat.service import HeartbeatService

        emitter = EventEmitter()
        collected = _collect_events(emitter, EventType.HEARTBEAT_CHECKED)

        provider = DummyProvider([])
        service = HeartbeatService(
            workspace=tmp_path,
            provider=provider,
            model="test-model",
            emitter=emitter,
        )

        await service._tick()

        assert len(collected) == 1
        assert collected[0].event_type == EventType.HEARTBEAT_CHECKED
        assert collected[0].payload["action"] == "skip"
        assert collected[0].payload["had_content"] is False

    @pytest.mark.asyncio
    async def test_tick_skip_decision_emits_skip(self, tmp_path):
        """When LLM decides skip, emit heartbeat.checked with action=skip."""
        from nanobot.heartbeat.service import HeartbeatService

        (tmp_path / "HEARTBEAT.md").write_text("- [ ] nothing", encoding="utf-8")

        emitter = EventEmitter()
        collected = _collect_events(emitter, EventType.HEARTBEAT_CHECKED)

        provider = DummyProvider([_make_skip_response()])
        service = HeartbeatService(
            workspace=tmp_path,
            provider=provider,
            model="test-model",
            emitter=emitter,
        )

        await service._tick()

        assert len(collected) == 1
        assert collected[0].payload["action"] == "skip"
        assert collected[0].payload["had_content"] is True
        assert collected[0].payload["duration_ms"] is not None
        assert collected[0].payload["duration_ms"] >= 0

    @pytest.mark.asyncio
    async def test_tick_run_decision_emits_run(self, tmp_path):
        """When LLM decides run and execute completes, emit heartbeat.checked with action=run."""
        from nanobot.heartbeat.service import HeartbeatService

        (tmp_path / "HEARTBEAT.md").write_text("- [ ] deploy", encoding="utf-8")

        emitter = EventEmitter()
        collected = _collect_events(emitter, EventType.HEARTBEAT_CHECKED)

        async def _execute(tasks: str) -> str:
            return "done"

        provider = DummyProvider([_make_run_response("deploy app")])
        service = HeartbeatService(
            workspace=tmp_path,
            provider=provider,
            model="test-model",
            on_execute=_execute,
            emitter=emitter,
        )

        await service._tick()

        assert len(collected) == 1
        assert collected[0].payload["action"] == "run"
        assert collected[0].payload["tasks"] == "deploy app"
        assert collected[0].payload["duration_ms"] >= 0
        assert collected[0].payload["error"] is None

    @pytest.mark.asyncio
    async def test_tick_execution_error_emits_error(self, tmp_path):
        """When on_execute raises, emit heartbeat.checked with error field."""
        from nanobot.heartbeat.service import HeartbeatService

        (tmp_path / "HEARTBEAT.md").write_text("- [ ] fail", encoding="utf-8")

        emitter = EventEmitter()
        collected = _collect_events(emitter, EventType.HEARTBEAT_CHECKED)

        async def _execute(tasks: str) -> str:
            raise RuntimeError("boom")

        provider = DummyProvider([_make_run_response("fail task")])
        service = HeartbeatService(
            workspace=tmp_path,
            provider=provider,
            model="test-model",
            on_execute=_execute,
            emitter=emitter,
        )

        # _tick catches the exception internally
        await service._tick()

        assert len(collected) == 1
        assert collected[0].payload["error"] == "boom"

    @pytest.mark.asyncio
    async def test_trigger_now_emits_skip(self, tmp_path):
        """trigger_now() emits heartbeat.checked even when no content."""
        from nanobot.heartbeat.service import HeartbeatService

        emitter = EventEmitter()
        collected = _collect_events(emitter, EventType.HEARTBEAT_CHECKED)

        provider = DummyProvider([])
        service = HeartbeatService(
            workspace=tmp_path,
            provider=provider,
            model="test-model",
            emitter=emitter,
        )

        result = await service.trigger_now()
        assert result is None
        assert len(collected) == 1
        assert collected[0].payload["had_content"] is False

    @pytest.mark.asyncio
    async def test_trigger_now_emits_run(self, tmp_path):
        """trigger_now() with run decision emits heartbeat.checked with tasks."""
        from nanobot.heartbeat.service import HeartbeatService

        (tmp_path / "HEARTBEAT.md").write_text("- [ ] do stuff", encoding="utf-8")

        emitter = EventEmitter()
        collected = _collect_events(emitter, EventType.HEARTBEAT_CHECKED)

        async def _execute(tasks: str) -> str:
            return "executed"

        provider = DummyProvider([_make_run_response("do stuff")])
        service = HeartbeatService(
            workspace=tmp_path,
            provider=provider,
            model="test-model",
            on_execute=_execute,
            emitter=emitter,
        )

        result = await service.trigger_now()
        assert result == "executed"
        assert len(collected) == 1
        assert collected[0].payload["action"] == "run"
        assert collected[0].payload["tasks"] == "do stuff"

    @pytest.mark.asyncio
    async def test_no_emitter_does_not_crash(self, tmp_path):
        """HeartbeatService works normally without an emitter (backward compat)."""
        from nanobot.heartbeat.service import HeartbeatService

        (tmp_path / "HEARTBEAT.md").write_text("- [ ] task", encoding="utf-8")

        provider = DummyProvider([_make_skip_response()])
        service = HeartbeatService(
            workspace=tmp_path,
            provider=provider,
            model="test-model",
            # No emitter
        )

        await service._tick()  # Should not raise


# ─── CronService event emission ───────────────────────────


class TestCronEvents:
    @pytest.mark.asyncio
    async def test_execute_job_emits_ok(self, tmp_path):
        """Successful job execution emits cron.triggered with status=ok."""
        from nanobot.cron.service import CronService
        from nanobot.cron.types import CronSchedule

        emitter = EventEmitter()
        collected = _collect_events(emitter, EventType.CRON_TRIGGERED)

        called: list[str] = []

        async def on_job(job):
            called.append(job.id)
            return "done"

        service = CronService(
            tmp_path / "cron" / "jobs.json",
            on_job=on_job,
            emitter=emitter,
        )

        job = service.add_job(
            name="test-job",
            schedule=CronSchedule(kind="every", every_ms=60000),
            message="hello world",
        )

        await service.run_job(job.id)

        assert len(called) == 1
        assert len(collected) == 1

        ev = collected[0]
        assert ev.event_type == EventType.CRON_TRIGGERED
        assert ev.payload["job_id"] == job.id
        assert ev.payload["job_name"] == "test-job"
        assert ev.payload["schedule_kind"] == "every"
        assert ev.payload["message"] == "hello world"
        assert ev.payload["status"] == "ok"
        assert ev.payload["error"] is None
        assert ev.payload["duration_ms"] >= 0

    @pytest.mark.asyncio
    async def test_execute_job_emits_error(self, tmp_path):
        """Failed job execution emits cron.triggered with status=error."""
        from nanobot.cron.service import CronService
        from nanobot.cron.types import CronSchedule

        emitter = EventEmitter()
        collected = _collect_events(emitter, EventType.CRON_TRIGGERED)

        async def on_job(job):
            raise ValueError("job failed")

        service = CronService(
            tmp_path / "cron" / "jobs.json",
            on_job=on_job,
            emitter=emitter,
        )

        job = service.add_job(
            name="bad-job",
            schedule=CronSchedule(kind="every", every_ms=60000),
            message="will fail",
        )

        await service.run_job(job.id)

        assert len(collected) == 1
        ev = collected[0]
        assert ev.payload["status"] == "error"
        assert ev.payload["error"] == "job failed"
        assert ev.payload["job_name"] == "bad-job"

    @pytest.mark.asyncio
    async def test_execute_job_captures_deliver_channel(self, tmp_path):
        """Cron event payload includes deliver and channel from job payload."""
        from nanobot.cron.service import CronService
        from nanobot.cron.types import CronSchedule

        emitter = EventEmitter()
        collected = _collect_events(emitter, EventType.CRON_TRIGGERED)

        async def on_job(job):
            return "sent"

        service = CronService(
            tmp_path / "cron" / "jobs.json",
            on_job=on_job,
            emitter=emitter,
        )

        job = service.add_job(
            name="notify-job",
            schedule=CronSchedule(kind="every", every_ms=60000),
            message="send report",
            deliver=True,
            channel="telegram",
        )

        await service.run_job(job.id)

        assert len(collected) == 1
        ev = collected[0]
        assert ev.payload["deliver"] is True
        assert ev.payload["channel"] == "telegram"

    @pytest.mark.asyncio
    async def test_no_emitter_does_not_crash(self, tmp_path):
        """CronService works normally without an emitter (backward compat)."""
        from nanobot.cron.service import CronService
        from nanobot.cron.types import CronSchedule

        called: list[str] = []

        async def on_job(job):
            called.append(job.id)

        service = CronService(
            tmp_path / "cron" / "jobs.json",
            on_job=on_job,
            # No emitter
        )

        job = service.add_job(
            name="no-emitter",
            schedule=CronSchedule(kind="every", every_ms=60000),
            message="test",
        )

        await service.run_job(job.id)
        assert len(called) == 1

    @pytest.mark.asyncio
    async def test_no_on_job_callback_still_emits(self, tmp_path):
        """Even without on_job callback, cron.triggered is emitted."""
        from nanobot.cron.service import CronService
        from nanobot.cron.types import CronSchedule

        emitter = EventEmitter()
        collected = _collect_events(emitter, EventType.CRON_TRIGGERED)

        service = CronService(
            tmp_path / "cron" / "jobs.json",
            on_job=None,
            emitter=emitter,
        )

        job = service.add_job(
            name="noop-job",
            schedule=CronSchedule(kind="every", every_ms=60000),
            message="noop",
        )

        await service.run_job(job.id)

        assert len(collected) == 1
        assert collected[0].payload["status"] == "ok"


# ─── EventStore derived state ─────────────────────────────


class TestEventStoreDerivedState:
    def test_heartbeat_state_updated(self, tmp_path):
        """EventStore updates last_heartbeat from heartbeat.checked events."""
        store = EventStore(tmp_path / "events.db")

        assert store.last_heartbeat is None

        event = Event(
            event_type=EventType.HEARTBEAT_CHECKED,
            payload={"action": "skip", "had_content": True},
        )
        store._update_derived_state(event)

        assert store.last_heartbeat is not None
        assert store.last_heartbeat.action == "skip"
        assert store.last_heartbeat.had_content is True

    def test_heartbeat_state_overwritten(self, tmp_path):
        """Each heartbeat event replaces the previous state."""
        store = EventStore(tmp_path / "events.db")

        event1 = Event(
            event_type=EventType.HEARTBEAT_CHECKED,
            payload={"action": "skip", "had_content": False},
        )
        store._update_derived_state(event1)

        event2 = Event(
            event_type=EventType.HEARTBEAT_CHECKED,
            payload={"action": "run", "had_content": True},
        )
        store._update_derived_state(event2)

        assert store.last_heartbeat.action == "run"
        assert store.last_heartbeat.had_content is True

    def test_heartbeat_error_state(self, tmp_path):
        """Heartbeat error is captured in derived state."""
        store = EventStore(tmp_path / "events.db")

        event = Event(
            event_type=EventType.HEARTBEAT_CHECKED,
            payload={"action": "run", "error": "timeout"},
        )
        store._update_derived_state(event)

        assert store.last_heartbeat.error == "timeout"

    def test_cron_state_updated(self, tmp_path):
        """EventStore updates cron_jobs from cron.triggered events."""
        store = EventStore(tmp_path / "events.db")

        assert len(store.cron_jobs) == 0

        event = Event(
            event_type=EventType.CRON_TRIGGERED,
            payload={
                "job_id": "abc",
                "job_name": "daily-backup",
                "status": "ok",
            },
        )
        store._update_derived_state(event)

        assert "abc" in store.cron_jobs
        state = store.cron_jobs["abc"]
        assert state.job_name == "daily-backup"
        assert state.last_status == "ok"
        assert state.last_error is None

    def test_cron_state_tracks_multiple_jobs(self, tmp_path):
        """Multiple cron jobs are tracked independently."""
        store = EventStore(tmp_path / "events.db")

        for job_id, name in [("j1", "backup"), ("j2", "report")]:
            event = Event(
                event_type=EventType.CRON_TRIGGERED,
                payload={"job_id": job_id, "job_name": name, "status": "ok"},
            )
            store._update_derived_state(event)

        assert len(store.cron_jobs) == 2
        assert store.cron_jobs["j1"].job_name == "backup"
        assert store.cron_jobs["j2"].job_name == "report"

    def test_cron_error_state(self, tmp_path):
        """Cron error is captured in derived state."""
        store = EventStore(tmp_path / "events.db")

        event = Event(
            event_type=EventType.CRON_TRIGGERED,
            payload={
                "job_id": "err",
                "job_name": "failing",
                "status": "error",
                "error": "disk full",
            },
        )
        store._update_derived_state(event)

        state = store.cron_jobs["err"]
        assert state.last_status == "error"
        assert state.last_error == "disk full"

    def test_cron_state_overwritten_on_rerun(self, tmp_path):
        """Re-running a job overwrites its cron state."""
        store = EventStore(tmp_path / "events.db")

        event1 = Event(
            event_type=EventType.CRON_TRIGGERED,
            payload={"job_id": "j1", "job_name": "backup", "status": "error", "error": "fail"},
        )
        store._update_derived_state(event1)
        assert store.cron_jobs["j1"].last_status == "error"

        event2 = Event(
            event_type=EventType.CRON_TRIGGERED,
            payload={"job_id": "j1", "job_name": "backup", "status": "ok"},
        )
        store._update_derived_state(event2)
        assert store.cron_jobs["j1"].last_status == "ok"
        assert store.cron_jobs["j1"].last_error is None

    def test_empty_job_id_ignored(self, tmp_path):
        """Cron events with empty job_id are ignored in derived state."""
        store = EventStore(tmp_path / "events.db")

        event = Event(
            event_type=EventType.CRON_TRIGGERED,
            payload={"job_id": "", "job_name": "ghost", "status": "ok"},
        )
        store._update_derived_state(event)

        assert len(store.cron_jobs) == 0


# ─── End-to-end: emit → persist → rebuild ─────────────────


class TestEndToEnd:
    @pytest.mark.asyncio
    async def test_heartbeat_round_trip(self, tmp_path):
        """Heartbeat event is emitted, persisted, and rebuilt on restart."""
        # Phase 1: emit and persist
        emitter = EventEmitter()
        store = EventStore(tmp_path / "events.db")
        emitter.on("*", store.handle_event)

        event = Event(
            event_type=EventType.HEARTBEAT_CHECKED,
            payload=HeartbeatPayload(
                action="run",
                tasks="deploy",
                duration_ms=100.0,
            ).model_dump(),
        )
        await emitter.emit(event)

        assert store.last_heartbeat is not None
        assert store.last_heartbeat.action == "run"

        # Phase 2: close and rebuild
        store.close()
        store2 = EventStore(tmp_path / "events.db")

        assert store2.last_heartbeat is not None
        assert store2.last_heartbeat.action == "run"
        store2.close()

    @pytest.mark.asyncio
    async def test_cron_round_trip(self, tmp_path):
        """Cron event is emitted, persisted, and rebuilt on restart."""
        emitter = EventEmitter()
        store = EventStore(tmp_path / "events.db")
        emitter.on("*", store.handle_event)

        event = Event(
            event_type=EventType.CRON_TRIGGERED,
            payload=CronTriggeredPayload(
                job_id="j42",
                job_name="nightly",
                schedule_kind="cron",
                status="ok",
                duration_ms=500.0,
            ).model_dump(),
        )
        await emitter.emit(event)

        assert "j42" in store.cron_jobs

        # Rebuild
        store.close()
        store2 = EventStore(tmp_path / "events.db")

        assert "j42" in store2.cron_jobs
        assert store2.cron_jobs["j42"].job_name == "nightly"
        assert store2.cron_jobs["j42"].last_status == "ok"
        store2.close()

    @pytest.mark.asyncio
    async def test_heartbeat_service_full_pipeline(self, tmp_path):
        """HeartbeatService → emitter → store → derived state, end-to-end."""
        from nanobot.heartbeat.service import HeartbeatService

        (tmp_path / "HEARTBEAT.md").write_text("- [ ] task", encoding="utf-8")

        emitter = EventEmitter()
        store = EventStore(tmp_path / "events.db")
        emitter.on("*", store.handle_event)

        executed: list[str] = []

        async def _execute(tasks: str) -> str:
            executed.append(tasks)
            return "done"

        provider = DummyProvider([_make_run_response("task from heartbeat")])
        service = HeartbeatService(
            workspace=tmp_path,
            provider=provider,
            model="test-model",
            on_execute=_execute,
            emitter=emitter,
        )

        await service._tick()

        # Verify execution happened
        assert executed == ["task from heartbeat"]

        # Verify event was persisted
        events = store.query(event_type=EventType.HEARTBEAT_CHECKED)
        assert len(events) == 1
        assert events[0].payload["action"] == "run"
        assert events[0].payload["tasks"] == "task from heartbeat"

        # Verify derived state
        assert store.last_heartbeat is not None
        assert store.last_heartbeat.action == "run"

        store.close()

    @pytest.mark.asyncio
    async def test_cron_service_full_pipeline(self, tmp_path):
        """CronService → emitter → store → derived state, end-to-end."""
        from nanobot.cron.service import CronService
        from nanobot.cron.types import CronSchedule

        emitter = EventEmitter()
        store = EventStore(tmp_path / "events.db")
        emitter.on("*", store.handle_event)

        async def on_job(job):
            return "result"

        service = CronService(
            tmp_path / "cron" / "jobs.json",
            on_job=on_job,
            emitter=emitter,
        )

        job = service.add_job(
            name="pipeline-job",
            schedule=CronSchedule(kind="every", every_ms=60000),
            message="pipeline test",
        )

        await service.run_job(job.id)

        # Verify event was persisted
        events = store.query(event_type=EventType.CRON_TRIGGERED)
        assert len(events) == 1
        assert events[0].payload["job_name"] == "pipeline-job"
        assert events[0].payload["status"] == "ok"

        # Verify derived state
        assert job.id in store.cron_jobs
        assert store.cron_jobs[job.id].job_name == "pipeline-job"

        store.close()

    @pytest.mark.asyncio
    async def test_wildcard_listener_receives_both_types(self, tmp_path):
        """A wildcard listener gets both heartbeat and cron events."""
        emitter = EventEmitter()
        all_events = _collect_events(emitter, "*")

        hb_event = Event(
            event_type=EventType.HEARTBEAT_CHECKED,
            payload={"action": "skip"},
        )
        cron_event = Event(
            event_type=EventType.CRON_TRIGGERED,
            payload={"job_id": "x", "job_name": "y", "status": "ok"},
        )

        await emitter.emit(hb_event)
        await emitter.emit(cron_event)

        assert len(all_events) == 2
        assert all_events[0].event_type == EventType.HEARTBEAT_CHECKED
        assert all_events[1].event_type == EventType.CRON_TRIGGERED
