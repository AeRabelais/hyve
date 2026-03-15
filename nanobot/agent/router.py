"""Router layer: parses message prefixes and dispatches to the right agent/team/chain."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage

if TYPE_CHECKING:
    from nanobot.agent.chain import ChainManager
    from nanobot.agent.registry import AgentRegistry
    from nanobot.bus.queue import MessageBus
    from nanobot.events.emitter import EventEmitter


# ── Prefix patterns ────────────────────────────────────────

_AT_PREFIX = re.compile(r"^@(\S+)\s*(.*)", re.DOTALL)
_CHAIN_PREFIX = re.compile(r"^#(\S+)\s*(.*)", re.DOTALL)


@dataclass
class RouteResult:
    """Result of parsing a message's routing prefix."""

    kind: str  # "agent", "team", "chain", "default"
    agent_id: str | None = None
    team_id: str | None = None
    chain_name: str | None = None
    content: str = ""  # Message body with prefix stripped


class Router:
    """
    Routes inbound messages to agents, teams, or chains.

    Sits between the MessageBus and AgentLoop instances. Replaces
    the single-agent ``AgentLoop.run()`` as the bus consumer in
    multi-agent gateway mode.

    Prefix dispatch rules:
        @agent_id ...  → dispatch to named agent via AgentRegistry
        @team_id ...   → resolve team leader, start chain via ChainManager
        #chain-name .. → ChainManager.handle_approval()
        (no prefix)    → dispatch to default agent
    """

    def __init__(
        self,
        bus: MessageBus,
        registry: AgentRegistry,
        chain_manager: ChainManager,
        emitter: EventEmitter | None = None,
    ) -> None:
        self._bus = bus
        self._registry = registry
        self._chain_manager = chain_manager
        self._emitter = emitter
        self._running = False
        self._active_tasks: dict[str, list[asyncio.Task]] = {}

    # ── Public API ─────────────────────────────────────────

    async def run(self) -> None:
        """Main loop: consume inbound messages and route them."""
        self._running = True
        logger.info("Router started")

        # Ensure default agent has MCP connected
        default_agent = self._registry.get_or_create("default")
        await default_agent._connect_mcp()

        while self._running:
            try:
                msg = await asyncio.wait_for(self._bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            # Global commands handled before routing
            if msg.content.strip().lower() == "/stop":
                await self._handle_stop(msg)
                continue

            task = asyncio.create_task(self._dispatch(msg))
            self._active_tasks.setdefault(msg.session_key, []).append(task)
            task.add_done_callback(
                lambda t, k=msg.session_key: (
                    self._active_tasks.get(k, []).remove(t)
                    if t in self._active_tasks.get(k, [])
                    else None
                )
            )

    def stop(self) -> None:
        """Stop the router loop."""
        self._running = False
        logger.info("Router stopping")

    # ── Prefix parsing ─────────────────────────────────────

    def parse(self, content: str) -> RouteResult:
        """Parse routing prefix from message content."""
        stripped = content.strip()

        # Check @prefix first
        if m := _AT_PREFIX.match(stripped):
            name = m.group(1)
            body = m.group(2).strip()

            if self._registry.has_agent(name):
                return RouteResult(kind="agent", agent_id=name, content=body or stripped)

            if self._registry.has_team(name):
                return RouteResult(kind="team", team_id=name, content=body or stripped)

            # Unknown @prefix — treat as default (the @ is part of content)
            logger.debug("Unknown @prefix '{}', routing to default", name)
            return RouteResult(kind="default", content=stripped)

        # Check #chain-name prefix
        if m := _CHAIN_PREFIX.match(stripped):
            chain_name = m.group(1)
            body = m.group(2).strip()
            return RouteResult(kind="chain", chain_name=chain_name, content=body)

        # No prefix — default agent
        return RouteResult(kind="default", content=stripped)

    # ── Dispatch ───────────────────────────────────────────

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Route a single inbound message."""
        try:
            route = self.parse(msg.content)

            # Emit message.routed event
            if self._emitter:
                from nanobot.events.models import Event, EventType

                await self._emitter.emit(Event(
                    event_type=EventType.MESSAGE_ROUTED,
                    payload={
                        "kind": route.kind,
                        "agent_id": route.agent_id,
                        "team_id": route.team_id,
                        "chain_name": route.chain_name,
                        "channel": msg.channel,
                        "sender_id": msg.sender_id,
                    },
                ))

            if route.kind == "agent":
                await self._dispatch_to_agent(route.agent_id, msg, route.content)

            elif route.kind == "team":
                await self._dispatch_to_team(route.team_id, msg, route.content)

            elif route.kind == "chain":
                await self._dispatch_chain_command(route.chain_name, msg, route.content)

            else:  # "default"
                await self._dispatch_to_agent("default", msg, route.content)

        except asyncio.CancelledError:
            logger.info("Task cancelled for session {}", msg.session_key)
            raise
        except Exception:
            logger.exception("Error routing message for session {}", msg.session_key)
            await self._bus.publish_outbound(OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="Sorry, I encountered an error.",
            ))

    async def _dispatch_to_agent(
        self, agent_id: str, msg: InboundMessage, content: str,
    ) -> None:
        """Dispatch message to a specific agent."""
        agent = self._registry.get_or_create(agent_id)
        await agent._connect_mcp()

        # Create a modified message with prefix-stripped content
        routed_msg = InboundMessage(
            channel=msg.channel,
            sender_id=msg.sender_id,
            chat_id=msg.chat_id,
            content=content,
            timestamp=msg.timestamp,
            media=msg.media,
            metadata=msg.metadata,
            session_key_override=msg.session_key_override,
        )

        response = await agent.process_message(routed_msg)
        if response is not None:
            # Check for @-mentions in response for chain interception
            await self._maybe_intercept_response(agent_id, response, msg)
        elif msg.channel == "cli":
            await self._bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id,
                content="", metadata=msg.metadata or {},
            ))

    async def _dispatch_to_team(
        self, team_id: str, msg: InboundMessage, content: str,
    ) -> None:
        """Start a chain with the team's leader agent."""
        chain_ctx = await self._chain_manager.start_chain(team_id, msg, content)
        if chain_ctx is None:
            await self._bus.publish_outbound(OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=f"Unknown team '{team_id}'.",
            ))

    async def _dispatch_chain_command(
        self, chain_name: str, msg: InboundMessage, body: str,
    ) -> None:
        """Handle a #chain-name approval/control command."""
        await self._chain_manager.handle_approval(chain_name, msg, body)

    async def _maybe_intercept_response(
        self,
        agent_id: str,
        response: OutboundMessage,
        original_msg: InboundMessage,
    ) -> None:
        """
        Check if the agent's response contains @-mentions for delegation.

        If the agent is part of an active chain and its output mentions
        another agent, delegate to the next agent. Otherwise, publish
        the response to the user.
        """
        # Check if there's an active chain for this agent
        chain_ctx = self._chain_manager.find_chain_for_agent(agent_id)

        if chain_ctx and response.content:
            intercepted = await self._chain_manager.intercept_response(
                chain_ctx, agent_id, response.content, original_msg,
            )
            if intercepted:
                return  # Chain manager handled it

        # No chain interception — publish directly to user
        await self._bus.publish_outbound(response)

    # ── Stop handling ──────────────────────────────────────

    async def _handle_stop(self, msg: InboundMessage) -> None:
        """Cancel all active tasks for the session across all agents."""
        tasks = self._active_tasks.pop(msg.session_key, [])
        cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

        # Also cancel subagents for each cached agent
        sub_cancelled = 0
        for agent in [self._registry.get_cached(aid) for aid in self._registry.agent_ids]:
            if agent:
                sub_cancelled += await agent.subagents.cancel_by_session(msg.session_key)

        total = cancelled + sub_cancelled
        content = f"⏹ Stopped {total} task(s)." if total else "No active task to stop."
        await self._bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=content,
        ))
