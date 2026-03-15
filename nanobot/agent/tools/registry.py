"""Tool registry for dynamic tool management."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool

if TYPE_CHECKING:
    from nanobot.events.emitter import EventEmitter


class ToolRegistry:
    """
    Registry for agent tools.

    Allows dynamic registration and execution of tools.
    """

    def __init__(self, emitter: EventEmitter | None = None):
        self._tools: dict[str, Tool] = {}
        self.emitter: EventEmitter | None = emitter
        # TODO(Phase 2): Sync with AgentLoop.agent_id from AgentRegistry
        self._agent_id: str = "default"

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def get_definitions(self) -> list[dict[str, Any]]:
        """Get all tool definitions in OpenAI format."""
        return [tool.to_schema() for tool in self._tools.values()]

    async def execute(self, name: str, params: dict[str, Any]) -> str:
        """Execute a tool by name with given parameters."""
        _HINT = "\n\n[Analyze the error above and try a different approach.]"

        tool = self._tools.get(name)
        if not tool:
            return f"Error: Tool '{name}' not found. Available: {', '.join(self.tool_names)}"

        # ── Emit tool.called ──
        if self.emitter:
            from nanobot.events.models import Event, EventType

            await self.emitter.emit(Event(
                event_type=EventType.TOOL_CALLED,
                agent_id=self._agent_id,
                payload={"tool_name": name, "args": _sanitize_args(params)},
            ))

        t0 = time.monotonic()
        try:
            errors = tool.validate_params(params)
            if errors:
                result = f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors) + _HINT
                await self._emit_tool_result(name, t0, success=False, error="validation_error")
                return result
            result = await tool.execute(**params)
            is_error = isinstance(result, str) and result.startswith("Error")
            await self._emit_tool_result(name, t0, success=not is_error)
            if is_error:
                return result + _HINT
            return result
        except Exception as e:
            await self._emit_tool_result(name, t0, success=False, error=str(e))
            return f"Error executing {name}: {str(e)}" + _HINT

    async def _emit_tool_result(
        self, name: str, t0: float, *, success: bool, error: str | None = None,
    ) -> None:
        """Emit a tool.result event with timing info."""
        if not self.emitter:
            return
        from nanobot.events.models import Event, EventType

        duration_ms = (time.monotonic() - t0) * 1000
        payload: dict[str, Any] = {
            "tool_name": name,
            "duration_ms": round(duration_ms, 1),
            "success": success,
        }
        if error:
            payload["error"] = error
        await self.emitter.emit(Event(
            event_type=EventType.TOOL_RESULT,
            agent_id=self._agent_id,
            payload=payload,
        ))

    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools


def _sanitize_args(params: dict[str, Any]) -> dict[str, Any]:
    """Truncate large argument values for event payloads."""
    sanitized: dict[str, Any] = {}
    for k, v in params.items():
        if isinstance(v, str) and len(v) > 200:
            sanitized[k] = v[:200] + "…"
        else:
            sanitized[k] = v
    return sanitized
