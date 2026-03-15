"""Tests for the memory watcher and scheduler (Phase 5).

Covers:
- Watcher: file classification, diff computation, no-op without watchdog
- Scheduler: time helpers, job_hourly, job_weekly, MemoryScheduler lifecycle
- Event emission from scheduler jobs
"""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from nanobot.events.emitter import EventEmitter
from nanobot.events.models import Event, EventType
from nanobot.memory.db.connection import init_db
from nanobot.memory.db.schema import MemoryEventType


# ─── Helpers ───────────────────────────────────────────────


def _collect_events(emitter: EventEmitter, event_type: EventType | str = "*") -> list[Event]:
    collected: list[Event] = []

    async def _listener(event: Event) -> None:
        collected.append(event)

    emitter.on(event_type, _listener)
    return collected


# ─── Watcher: file classification ──────────────────────────


class TestWatcherClassification:
    def test_classify_daily_note(self):
        from nanobot.memory.watcher import _classify_file

        ws = Path("/workspace")
        assert _classify_file(ws / "memory" / "2026-03-14.md", ws) == MemoryEventType.daily_note

    def test_classify_memory_md(self):
        from nanobot.memory.watcher import _classify_file

        ws = Path("/workspace")
        assert _classify_file(ws / "MEMORY.md", ws) == MemoryEventType.memory_write

    def test_classify_unrelated_file(self):
        from nanobot.memory.watcher import _classify_file

        ws = Path("/workspace")
        assert _classify_file(ws / "src" / "main.py", ws) is None

    def test_classify_deep_memory_file(self):
        from nanobot.memory.watcher import _classify_file

        ws = Path("/workspace")
        # memory/people/someone.md is not a daily note pattern
        assert _classify_file(ws / "memory" / "people" / "someone.md", ws) is None

    def test_classify_outside_workspace(self):
        from nanobot.memory.watcher import _classify_file

        ws = Path("/workspace")
        assert _classify_file(Path("/other/memory/2026-03-14.md"), ws) is None

    def test_classify_bad_date_pattern(self):
        from nanobot.memory.watcher import _classify_file

        ws = Path("/workspace")
        assert _classify_file(ws / "memory" / "notes.md", ws) is None
        assert _classify_file(ws / "memory" / "20260314.md", ws) is None


# ─── Watcher: diff computation ─────────────────────────────


class TestWatcherDiff:
    def test_first_observation_returns_full_content(self):
        from nanobot.memory.watcher import _compute_new_content

        result = _compute_new_content(None, "hello\nworld\n")
        assert result == "hello\nworld\n"

    def test_no_change_returns_original(self):
        from nanobot.memory.watcher import _compute_new_content

        result = _compute_new_content("hello\n", "hello\n")
        assert result == "hello\n"

    def test_added_lines_extracted(self):
        from nanobot.memory.watcher import _compute_new_content

        old = "line1\nline2\n"
        new = "line1\nline2\nline3\n"
        result = _compute_new_content(old, new)
        assert "line3" in result

    def test_empty_diff_returns_new_content(self):
        from nanobot.memory.watcher import _compute_new_content

        # If only deletions, returns full new content
        result = _compute_new_content("a\nb\nc\n", "a\nc\n")
        assert result  # Should not be empty


# ─── Watcher: no-op without watchdog ──────────────────────


class TestWatcherNoOp:
    def test_watcher_without_watchdog_is_noop(self, tmp_path):
        """WorkspaceWatcher gracefully degrades when watchdog is not installed."""
        from nanobot.memory.watcher import WorkspaceWatcher

        with patch("nanobot.memory.watcher._check_watchdog", return_value=False):
            watcher = WorkspaceWatcher(
                workspaces=[(tmp_path, "test-agent")],
                db_path=tmp_path / "memory.db",
            )
            assert watcher._available is False
            watcher.start()  # Should not raise
            assert not watcher.is_alive
            watcher.stop()  # Should not raise


# ─── Scheduler: time helpers ──────────────────────────────


class TestSchedulerTimeHelpers:
    def test_parse_time_standard(self):
        from nanobot.memory.scheduler import _parse_time

        assert _parse_time("02:00") == (2, 0)
        assert _parse_time("14:30") == (14, 30)
        assert _parse_time("00:00") == (0, 0)

    def test_seconds_until_future_time(self):
        from nanobot.memory.scheduler import _seconds_until

        # A time 1 hour from now should give roughly 3600 seconds
        future = datetime.now() + timedelta(hours=1)
        secs = _seconds_until(future.hour, future.minute)
        assert 3500 < secs < 90000  # Within reasonable bounds

    def test_seconds_until_past_time_wraps_to_tomorrow(self):
        from nanobot.memory.scheduler import _seconds_until

        # A time 1 hour ago should wrap to tomorrow
        past = datetime.now() - timedelta(hours=1)
        secs = _seconds_until(past.hour, past.minute)
        assert secs > 80000  # Should be ~23 hours

    def test_seconds_until_weekday(self):
        from nanobot.memory.scheduler import _seconds_until_weekday

        # Should always return a positive value
        secs = _seconds_until_weekday(6, 3, 0)  # Sunday 03:00
        assert secs > 0
        assert secs <= 7 * 86400  # At most 7 days

    def test_day_map_coverage(self):
        from nanobot.memory.scheduler import _DAY_MAP

        assert len(_DAY_MAP) == 7
        assert _DAY_MAP["monday"] == 0
        assert _DAY_MAP["sunday"] == 6


# ─── Scheduler: job_hourly ─────────────────────────────────


class TestJobHourly:
    @pytest.mark.asyncio
    async def test_hourly_prune_with_empty_db(self, tmp_path):
        """Hourly job runs cleanly on empty database."""
        from nanobot.memory.scheduler import job_hourly

        db_path = tmp_path / "memory.db"
        init_db(db_path)

        result = await job_hourly(db_path)
        assert result["pruned"] == 0
        assert isinstance(result["event_summary"], dict)

    @pytest.mark.asyncio
    async def test_hourly_returns_summary_dict(self, tmp_path):
        from nanobot.memory.scheduler import job_hourly

        db_path = tmp_path / "memory.db"
        init_db(db_path)

        result = await job_hourly(db_path)
        assert "pruned" in result
        assert "event_summary" in result


# ─── Scheduler: job_weekly ─────────────────────────────────


class TestJobWeekly:
    @pytest.mark.asyncio
    async def test_weekly_cleanup_empty_db(self, tmp_path):
        """Weekly cleanup runs cleanly on empty database."""
        from nanobot.memory.scheduler import job_weekly

        db_path = tmp_path / "memory.db"
        init_db(db_path)

        result = await job_weekly(db_path)
        assert result.get("pruned", 0) == 0
        assert result.get("archived", 0) == 0
        assert result.get("compacted", 0) == 0
        assert "stats" in result

    @pytest.mark.asyncio
    async def test_weekly_includes_db_stats(self, tmp_path):
        from nanobot.memory.scheduler import job_weekly

        db_path = tmp_path / "memory.db"
        init_db(db_path)

        result = await job_weekly(db_path)
        stats = result["stats"]
        assert stats["total_events"] == 0
        assert stats["total_facts"] == 0


# ─── Scheduler: MemoryScheduler lifecycle ──────────────────


class TestMemorySchedulerLifecycle:
    @pytest.mark.asyncio
    async def test_start_creates_tasks(self, tmp_path):
        from nanobot.memory.scheduler import MemoryScheduler

        db_path = tmp_path / "memory.db"
        provider = AsyncMock()

        scheduler = MemoryScheduler(
            workspace=tmp_path,
            provider=provider,
            model="test-model",
            db_path=db_path,
        )

        await scheduler.start()

        assert scheduler._running is True
        assert len(scheduler._tasks) == 3
        assert scheduler.status()["running"] is True
        assert scheduler.status()["tasks"] == 3

        scheduler.stop()
        assert scheduler._running is False
        assert len(scheduler._tasks) == 0

        # Allow cancelled tasks to clean up
        await asyncio.sleep(0.05)

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self, tmp_path):
        from nanobot.memory.scheduler import MemoryScheduler

        db_path = tmp_path / "memory.db"
        provider = AsyncMock()

        scheduler = MemoryScheduler(
            workspace=tmp_path,
            provider=provider,
            model="test-model",
            db_path=db_path,
        )

        await scheduler.start()
        first_tasks = list(scheduler._tasks)
        await scheduler.start()  # Should warn and return

        assert scheduler._tasks == first_tasks

        scheduler.stop()
        await asyncio.sleep(0.05)

    @pytest.mark.asyncio
    async def test_run_once_hourly(self, tmp_path):
        from nanobot.memory.scheduler import MemoryScheduler

        db_path = tmp_path / "memory.db"
        provider = AsyncMock()

        scheduler = MemoryScheduler(
            workspace=tmp_path,
            provider=provider,
            model="test-model",
            db_path=db_path,
        )

        result = await scheduler.run_once("hourly")
        assert "pruned" in result

    @pytest.mark.asyncio
    async def test_run_once_weekly(self, tmp_path):
        from nanobot.memory.scheduler import MemoryScheduler

        db_path = tmp_path / "memory.db"
        provider = AsyncMock()

        scheduler = MemoryScheduler(
            workspace=tmp_path,
            provider=provider,
            model="test-model",
            db_path=db_path,
        )

        result = await scheduler.run_once("weekly")
        assert "stats" in result

    @pytest.mark.asyncio
    async def test_run_once_unknown_raises(self, tmp_path):
        from nanobot.memory.scheduler import MemoryScheduler

        db_path = tmp_path / "memory.db"
        provider = AsyncMock()

        scheduler = MemoryScheduler(
            workspace=tmp_path,
            provider=provider,
            model="test-model",
            db_path=db_path,
        )

        with pytest.raises(ValueError, match="Unknown job"):
            await scheduler.run_once("bogus")


# ─── Scheduler: event emission ─────────────────────────────


class TestSchedulerEventEmission:
    @pytest.mark.asyncio
    async def test_scheduler_emits_on_hourly(self, tmp_path):
        """MemoryScheduler emits MEMORY_WRITTEN after hourly job."""
        from nanobot.memory.scheduler import MemoryScheduler

        db_path = tmp_path / "memory.db"
        provider = AsyncMock()
        emitter = EventEmitter()
        collected = _collect_events(emitter, EventType.MEMORY_WRITTEN)

        scheduler = MemoryScheduler(
            workspace=tmp_path,
            provider=provider,
            model="test-model",
            db_path=db_path,
            emitter=emitter,
            agent_id="test",
        )

        # Use _emit directly (run_once doesn't emit)
        result = await scheduler.run_once("hourly")
        await scheduler._emit("hourly", result)

        assert len(collected) == 1
        assert collected[0].payload["source"] == "scheduler.hourly"
        assert collected[0].agent_id == "test"

    @pytest.mark.asyncio
    async def test_scheduler_no_emitter_no_crash(self, tmp_path):
        """Scheduler works without emitter."""
        from nanobot.memory.scheduler import MemoryScheduler

        db_path = tmp_path / "memory.db"
        provider = AsyncMock()

        scheduler = MemoryScheduler(
            workspace=tmp_path,
            provider=provider,
            model="test-model",
            db_path=db_path,
            # No emitter
        )

        await scheduler._emit("hourly", {"pruned": 0})  # Should not raise
