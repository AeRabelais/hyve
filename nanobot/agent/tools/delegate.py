"""Delegate tool for named agent-to-agent delegation (SubagentManager Mode 2)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool

if TYPE_CHECKING:
    from nanobot.agent.registry import AgentRegistry


class DelegateTool(Tool):
    """
    Tool that delegates work to a named agent via the AgentRegistry.

    Unlike the ``spawn`` tool (Mode 1 — anonymous background workers),
    ``delegate`` routes to a fully configured named agent with its own
    workspace, model, memory, and tools.

    The delegation runs synchronously from the caller's perspective —
    the result is returned inline, not announced asynchronously.
    """

    def __init__(self, registry: AgentRegistry) -> None:
        self._registry = registry
        self._origin_channel = "cli"
        self._origin_chat_id = "direct"

    def set_context(self, channel: str, chat_id: str) -> None:
        """Set origin context for the delegation."""
        self._origin_channel = channel
        self._origin_chat_id = chat_id

    @property
    def name(self) -> str:
        return "delegate"

    @property
    def description(self) -> str:
        agents = [aid for aid in self._registry.agent_ids if aid != "default"]
        agent_list = ", ".join(agents) if agents else "(none configured)"
        return (
            "Delegate a task to a named agent. The target agent has its own "
            "workspace, model, memory, and tools. The result is returned "
            f"directly. Available agents: {agent_list}"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "The ID of the target agent to delegate to",
                },
                "task": {
                    "type": "string",
                    "description": "The task description or message for the target agent",
                },
                "context": {
                    "type": "string",
                    "description": "Optional context from the originating agent to help the target",
                },
            },
            "required": ["agent_id", "task"],
        }

    async def execute(
        self,
        agent_id: str,
        task: str,
        context: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Delegate a task to a named agent and return its response."""
        if not self._registry.has_agent(agent_id):
            available = ", ".join(self._registry.agent_ids)
            return f"Error: Unknown agent '{agent_id}'. Available agents: {available}"

        from nanobot.bus.events import InboundMessage

        # Package the delegation message
        content = task
        if context:
            content = f"[Context from delegating agent]: {context}\n\n{task}"

        msg = InboundMessage(
            channel=self._origin_channel,
            sender_id="delegate",
            chat_id=self._origin_chat_id,
            content=content,
        )

        try:
            agent = self._registry.get_or_create(agent_id)
            await agent._connect_mcp()
            response = await agent.process_message(msg)
            if response and response.content:
                return response.content
            return "Agent completed the task but produced no output."
        except Exception as e:
            return f"Error delegating to agent '{agent_id}': {e}"
