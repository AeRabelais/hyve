"""Heartbeat service - periodic agent wake-up to check for tasks."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Coroutine

from loguru import logger

if TYPE_CHECKING:
    from nanobot.events.emitter import EventEmitter
    from nanobot.providers.base import LLMProvider

_HEARTBEAT_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "heartbeat",
            "description": "Report heartbeat decision after reviewing tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["skip", "run"],
                        "description": "skip = nothing to do, run = has active tasks",
                    },
                    "tasks": {
                        "type": "string",
                        "description": "Natural-language summary of active tasks (required for run)",
                    },
                },
                "required": ["action"],
            },
        },
    }
]


class HeartbeatService:
    """
    Periodic heartbeat service that wakes the agent to check for tasks.

    Phase 1 (decision): reads HEARTBEAT.md and asks the LLM — via a virtual
    tool call — whether there are active tasks.  This avoids free-text parsing
    and the unreliable HEARTBEAT_OK token.

    Phase 2 (execution): only triggered when Phase 1 returns ``run``.  The
    ``on_execute`` callback runs the task through the full agent loop and
    returns the result to deliver.
    """

    def __init__(
        self,
        workspace: Path,
        provider: LLMProvider,
        model: str,
        on_execute: Callable[[str], Coroutine[Any, Any, str]] | None = None,
        on_notify: Callable[[str], Coroutine[Any, Any, None]] | None = None,
        interval_s: int = 30 * 60,
        enabled: bool = True,
        emitter: EventEmitter | None = None,
    ):
        self.workspace = workspace
        self.provider = provider
        self.model = model
        self.on_execute = on_execute
        self.on_notify = on_notify
        self.interval_s = interval_s
        self.enabled = enabled
        self.emitter = emitter
        self._running = False
        self._task: asyncio.Task | None = None

    @property
    def heartbeat_file(self) -> Path:
        return self.workspace / "HEARTBEAT.md"

    def _read_heartbeat_file(self) -> str | None:
        if self.heartbeat_file.exists():
            try:
                return self.heartbeat_file.read_text(encoding="utf-8")
            except Exception:
                return None
        return None

    async def _decide(self, content: str) -> tuple[str, str]:
        """Phase 1: ask LLM to decide skip/run via virtual tool call.

        Returns (action, tasks) where action is 'skip' or 'run'.
        """
        response = await self.provider.chat(
            messages=[
                {"role": "system", "content": "You are a heartbeat agent. Call the heartbeat tool to report your decision."},
                {"role": "user", "content": (
                    "Review the following HEARTBEAT.md and decide whether there are active tasks.\n\n"
                    f"{content}"
                )},
            ],
            tools=_HEARTBEAT_TOOL,
            model=self.model,
        )

        if not response.has_tool_calls:
            return "skip", ""

        args = response.tool_calls[0].arguments
        return args.get("action", "skip"), args.get("tasks", "")

    async def start(self) -> None:
        """Start the heartbeat service."""
        if not self.enabled:
            logger.info("Heartbeat disabled")
            return
        if self._running:
            logger.warning("Heartbeat already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Heartbeat started (every {}s)", self.interval_s)

    def stop(self) -> None:
        """Stop the heartbeat service."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run_loop(self) -> None:
        """Main heartbeat loop."""
        while self._running:
            try:
                await asyncio.sleep(self.interval_s)
                if self._running:
                    await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Heartbeat error: {}", e)

    async def _emit_heartbeat(
        self,
        action: str,
        tasks: str = "",
        *,
        had_content: bool = True,
        duration_ms: float | None = None,
        error: str | None = None,
    ) -> None:
        """Emit a heartbeat.checked event if an emitter is configured."""
        if not self.emitter:
            return
        from nanobot.events.models import Event, EventType, HeartbeatPayload

        payload = HeartbeatPayload(
            action=action,
            tasks=tasks,
            had_content=had_content,
            duration_ms=duration_ms,
            error=error,
        )
        await self.emitter.emit(Event(
            event_type=EventType.HEARTBEAT_CHECKED,
            payload=payload.model_dump(),
        ))

    async def _tick(self) -> None:
        """Execute a single heartbeat tick."""
        content = self._read_heartbeat_file()
        if not content:
            logger.debug("Heartbeat: HEARTBEAT.md missing or empty")
            await self._emit_heartbeat("skip", had_content=False)
            return

        logger.info("Heartbeat: checking for tasks...")
        t0 = time.monotonic()

        try:
            action, tasks = await self._decide(content)
            duration_ms = (time.monotonic() - t0) * 1000

            if action != "run":
                logger.info("Heartbeat: OK (nothing to report)")
                await self._emit_heartbeat("skip", duration_ms=duration_ms)
                return

            logger.info("Heartbeat: tasks found, executing...")
            if self.on_execute:
                response = await self.on_execute(tasks)
                duration_ms = (time.monotonic() - t0) * 1000
                if response and self.on_notify:
                    logger.info("Heartbeat: completed, delivering response")
                    await self.on_notify(response)

            await self._emit_heartbeat("run", tasks, duration_ms=duration_ms)
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            logger.exception("Heartbeat execution failed")
            await self._emit_heartbeat("run", error=str(exc), duration_ms=duration_ms)

    async def trigger_now(self) -> str | None:
        """Manually trigger a heartbeat."""
        content = self._read_heartbeat_file()
        if not content:
            await self._emit_heartbeat("skip", had_content=False)
            return None

        t0 = time.monotonic()
        try:
            action, tasks = await self._decide(content)
            duration_ms = (time.monotonic() - t0) * 1000

            if action != "run" or not self.on_execute:
                await self._emit_heartbeat(action, tasks, duration_ms=duration_ms)
                return None

            result = await self.on_execute(tasks)
            duration_ms = (time.monotonic() - t0) * 1000
            await self._emit_heartbeat("run", tasks, duration_ms=duration_ms)
            return result
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            await self._emit_heartbeat("run", error=str(exc), duration_ms=duration_ms)
            raise
