"""Async memory scheduler — orchestrates periodic memory maintenance jobs.

Runs as a background asyncio task alongside the gateway, executing:

  * **Hourly** — TTL prune + event summary
  * **Daily** (configurable, default 02:00) — distillation + generation
  * **Weekly** (configurable, default Sunday 03:00) — deep cleanup
    (archive stale facts, compact events table)

No external dependencies — uses only asyncio timers.

Usage::

    scheduler = MemoryScheduler(
        workspace=workspace_path,
        provider=provider,
        model="gpt-4o-mini",
        config=config.memory,
        emitter=emitter,
    )
    await scheduler.start()
    ...
    scheduler.stop()
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from nanobot.memory.db.connection import get_engine, init_db
from nanobot.memory.db.queries import (
    archive_stale_facts,
    compact_events_table,
    get_db_stats,
    get_hourly_event_summary,
)

if TYPE_CHECKING:
    from nanobot.config.schema import MemoryConfig
    from nanobot.events.emitter import EventEmitter
    from nanobot.providers.base import LLMProvider


# ── Helpers ────────────────────────────────────────────────


def _parse_time(time_str: str) -> tuple[int, int]:
    """Parse 'HH:MM' to (hour, minute)."""
    parts = time_str.split(":")
    return int(parts[0]), int(parts[1])


def _seconds_until(hour: int, minute: int) -> float:
    """Seconds from now until the next occurrence of HH:MM local time."""
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _seconds_until_weekday(weekday: int, hour: int, minute: int) -> float:
    """Seconds from now until the next occurrence of a specific weekday + time.

    weekday: 0=Monday, 6=Sunday.
    """
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    days_ahead = weekday - now.weekday()
    if days_ahead < 0:
        days_ahead += 7
    elif days_ahead == 0 and target <= now:
        days_ahead = 7

    target += timedelta(days=days_ahead)
    return (target - now).total_seconds()


_DAY_MAP = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


# ── Job implementations ───────────────────────────────────


async def job_hourly(db_path: Path | None = None) -> dict:
    """Hourly: prune expired facts + log event summary.

    Returns a summary dict for observability.
    """
    logger.info("Memory scheduler: hourly cycle starting")
    result = {"pruned": 0, "event_summary": {}}

    # 1. TTL prune
    try:
        from nanobot.memory.pruner import run_prune_cycle

        pruned = run_prune_cycle(db_path)
        result["pruned"] = len(pruned)
        logger.info("Memory scheduler: hourly prune — {} fact(s) removed", len(pruned))
    except Exception as exc:
        logger.error("Memory scheduler: hourly prune failed — {}", exc)
        result["prune_error"] = str(exc)

    # 2. Event summary
    try:
        conn = get_engine(db_path)
        summary = get_hourly_event_summary(conn)
        result["event_summary"] = summary
        if summary:
            total = sum(c for by_agent in summary.values() for c in by_agent.values())
            logger.info("Memory scheduler: {} event(s) in last hour", total)
        else:
            logger.info("Memory scheduler: no events in last hour")
    except Exception as exc:
        logger.error("Memory scheduler: hourly event summary failed — {}", exc)

    logger.info("Memory scheduler: hourly cycle complete")
    return result


async def job_daily(
    workspace: Path,
    provider: LLMProvider,
    model: str,
    db_path: Path | None = None,
    config: MemoryConfig | None = None,
    agent_id: str | None = None,
) -> dict:
    """Daily: distillation + generation.

    Returns a summary dict for observability.
    """
    logger.info("Memory scheduler: daily cycle starting")
    result: dict = {}

    # 1. Distillation
    try:
        from nanobot.memory.distiller import run_distillation

        decay_config = config.decay if config else None
        dist_result = await run_distillation(
            provider=provider,
            model=model,
            db_path=db_path,
            decay_config=decay_config,
        )
        result["distillation"] = {
            "events_processed": dist_result.events_processed,
            "facts_extracted": dist_result.facts_extracted,
            "facts_inserted": dist_result.facts_inserted,
            "facts_updated": dist_result.facts_updated,
            "errors": dist_result.errors,
        }
        logger.info(
            "Memory scheduler: daily distillation — {} events → {} facts ({} new, {} updated)",
            dist_result.events_processed, dist_result.facts_extracted,
            dist_result.facts_inserted, dist_result.facts_updated,
        )
    except Exception as exc:
        logger.error("Memory scheduler: daily distillation failed — {}", exc)
        result["distillation_error"] = str(exc)

    # 2. Generation
    try:
        from nanobot.memory.generator import run_generation

        max_tokens = config.index.max_tokens if config else 3000
        active_slots = config.index.active_context_slots if config else 3

        gen_result = run_generation(
            workspace=workspace,
            db_path=db_path,
            max_tokens=max_tokens,
            active_context_slots=active_slots,
            agent_id=agent_id,
        )
        result["generation"] = {
            "workspaces_written": gen_result.workspaces_written,
            "detail_files_written": gen_result.detail_files_written,
            "detail_files_cleaned": gen_result.detail_files_cleaned,
            "facts_rendered": gen_result.facts_rendered,
            "errors": gen_result.errors,
        }
        logger.info(
            "Memory scheduler: daily generation — {} detail files, {} cleaned",
            gen_result.detail_files_written, gen_result.detail_files_cleaned,
        )
    except Exception as exc:
        logger.error("Memory scheduler: daily generation failed — {}", exc)
        result["generation_error"] = str(exc)

    logger.info("Memory scheduler: daily cycle complete")
    return result


async def job_weekly(db_path: Path | None = None) -> dict:
    """Weekly: deep cleanup (archive stale, compact events).

    Returns a summary dict for observability.
    """
    logger.info("Memory scheduler: weekly cleanup starting")
    result: dict = {}
    conn = get_engine(db_path)

    # 1. Full prune (catches anything hourly missed)
    try:
        from nanobot.memory.pruner import run_prune_cycle

        pruned = run_prune_cycle(db_path)
        result["pruned"] = len(pruned)
        logger.info("Memory scheduler: weekly prune — {} fact(s)", len(pruned))
    except Exception as exc:
        logger.error("Memory scheduler: weekly prune failed — {}", exc)

    # 2. Archive stale facts (90+ days untouched)
    try:
        archived = archive_stale_facts(conn, stale_days=90)
        result["archived"] = archived
        logger.info("Memory scheduler: weekly archive — {} stale fact(s)", archived)
    except Exception as exc:
        logger.error("Memory scheduler: weekly archive failed — {}", exc)

    # 3. Compact events table (events older than 30 days)
    try:
        compacted = compact_events_table(conn, older_than_days=30)
        result["compacted"] = compacted
        logger.info("Memory scheduler: weekly compact — {} old event(s)", compacted)
    except Exception as exc:
        logger.error("Memory scheduler: weekly compact failed — {}", exc)

    # 4. DB stats for logging
    try:
        stats = get_db_stats(conn)
        result["stats"] = stats
        logger.info("Memory scheduler: DB stats — {} facts, {} events",
                     stats["total_facts"], stats["total_events"])
    except Exception as exc:
        logger.error("Memory scheduler: stats failed — {}", exc)

    logger.info("Memory scheduler: weekly cleanup complete")
    return result


# ── Scheduler ──────────────────────────────────────────────


class MemoryScheduler:
    """Async memory maintenance scheduler.

    Runs hourly/daily/weekly memory jobs as background asyncio tasks.
    Integrates with the nanobot event system for observability.
    """

    def __init__(
        self,
        workspace: Path,
        provider: LLMProvider,
        model: str,
        config: MemoryConfig | None = None,
        db_path: Path | None = None,
        emitter: EventEmitter | None = None,
        agent_id: str | None = None,
    ) -> None:
        self._workspace = workspace
        self._provider = provider
        self._model = model
        self._config = config
        self._db_path = db_path
        self._emitter = emitter
        self._agent_id = agent_id
        self._running = False
        self._tasks: list[asyncio.Task] = []

    async def _emit(self, source: str, result: dict) -> None:
        """Emit a memory.written event with scheduler job results."""
        if not self._emitter:
            return
        from nanobot.events.models import Event, EventType

        await self._emitter.emit(Event(
            event_type=EventType.MEMORY_WRITTEN,
            agent_id=self._agent_id,
            payload={
                "source": f"scheduler.{source}",
                "result": result,
            },
        ))

    async def _hourly_loop(self) -> None:
        """Run the hourly job on the hour, then every 60 minutes."""
        # Wait for the next full hour
        now = datetime.now()
        next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        initial_delay = (next_hour - now).total_seconds()
        await asyncio.sleep(initial_delay)

        while self._running:
            try:
                schedule_cfg = self._config.schedule if self._config else None
                do_prune = not schedule_cfg or schedule_cfg.hourly_prune

                if do_prune:
                    result = await job_hourly(self._db_path)
                    await self._emit("hourly", result)
                else:
                    logger.debug("Memory scheduler: hourly prune disabled by config")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Memory scheduler: hourly loop error — {}", exc)

            await asyncio.sleep(3600)

    async def _daily_loop(self) -> None:
        """Run the daily job at the configured time, then every 24 hours."""
        schedule_cfg = self._config.schedule if self._config else None
        time_str = schedule_cfg.daily_distill_time if schedule_cfg else "02:00"
        hour, minute = _parse_time(time_str)

        initial_delay = _seconds_until(hour, minute)
        logger.info("Memory scheduler: daily job scheduled in {:.0f}s (at {:02d}:{:02d})",
                     initial_delay, hour, minute)
        await asyncio.sleep(initial_delay)

        while self._running:
            try:
                result = await job_daily(
                    workspace=self._workspace,
                    provider=self._provider,
                    model=self._model,
                    db_path=self._db_path,
                    config=self._config,
                    agent_id=self._agent_id,
                )
                await self._emit("daily", result)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Memory scheduler: daily loop error — {}", exc)

            await asyncio.sleep(86400)

    async def _weekly_loop(self) -> None:
        """Run the weekly job on the configured day/time, then every 7 days."""
        schedule_cfg = self._config.schedule if self._config else None
        day_name = (schedule_cfg.weekly_cleanup_day if schedule_cfg else "sunday").lower()
        time_str = schedule_cfg.weekly_cleanup_time if schedule_cfg else "03:00"
        hour, minute = _parse_time(time_str)
        weekday = _DAY_MAP.get(day_name, 6)

        initial_delay = _seconds_until_weekday(weekday, hour, minute)
        logger.info("Memory scheduler: weekly job scheduled in {:.0f}s ({} {:02d}:{:02d})",
                     initial_delay, day_name, hour, minute)
        await asyncio.sleep(initial_delay)

        while self._running:
            try:
                result = await job_weekly(self._db_path)
                await self._emit("weekly", result)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Memory scheduler: weekly loop error — {}", exc)

            await asyncio.sleep(604800)  # 7 days

    async def start(self) -> None:
        """Start all scheduled loops as background tasks."""
        if self._running:
            logger.warning("Memory scheduler already running")
            return

        # Ensure memory DB is initialised
        init_db(self._db_path)

        self._running = True
        self._tasks = [
            asyncio.create_task(self._hourly_loop()),
            asyncio.create_task(self._daily_loop()),
            asyncio.create_task(self._weekly_loop()),
        ]
        logger.info("Memory scheduler started (3 loops)")

    def stop(self) -> None:
        """Cancel all running loops."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        self._tasks = []
        logger.info("Memory scheduler stopped")

    async def run_once(self, job_name: str) -> dict:
        """Run a single named job immediately (for CLI subcommands).

        Parameters
        ----------
        job_name
            One of ``"hourly"``, ``"daily"``, ``"weekly"``.
        """
        init_db(self._db_path)

        if job_name == "hourly":
            return await job_hourly(self._db_path)
        elif job_name == "daily":
            return await job_daily(
                workspace=self._workspace,
                provider=self._provider,
                model=self._model,
                db_path=self._db_path,
                config=self._config,
                agent_id=self._agent_id,
            )
        elif job_name == "weekly":
            return await job_weekly(self._db_path)
        else:
            raise ValueError(f"Unknown job: {job_name}")

    def status(self) -> dict:
        """Return scheduler status for diagnostics."""
        return {
            "running": self._running,
            "tasks": len(self._tasks),
            "active_tasks": sum(1 for t in self._tasks if not t.done()),
        }
