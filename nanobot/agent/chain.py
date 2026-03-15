"""Chain manager for multi-agent chain coordination."""

from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage

if TYPE_CHECKING:
    from nanobot.agent.registry import AgentRegistry
    from nanobot.bus.queue import MessageBus
    from nanobot.events.emitter import EventEmitter


# ── Constants ──────────────────────────────────────────────

_CHAIN_TTL = timedelta(hours=24)
_AT_MENTION = re.compile(r"@(\S+)")


# ── Chain context ──────────────────────────────────────────


@dataclass
class ChainContext:
    """Mutable state for an active multi-agent chain."""

    chain_id: str
    chain_name: str | None = None  # Set when using #chain-name approval
    team_id: str = ""
    leader_id: str = ""
    agents_called: list[str] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)  # agent_id → output
    pending_agent: str | None = None  # Next agent waiting to execute
    pending_content: str | None = None  # Content for pending agent
    approval_mode: str = "auto"  # auto / confirm / first_only
    origin_channel: str = ""
    origin_chat_id: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime = field(default_factory=lambda: datetime.now(UTC) + _CHAIN_TTL)
    status: str = "active"  # active / awaiting_approval / completed / cancelled


class ChainManager:
    """
    Manages multi-agent chain execution.

    Chains are sequences of agent interactions coordinated by the
    ChainManager. They support:
    - Sequential execution: user → agent A → agent B → user
    - Fan-out execution: agent A → [B, C] → combine → user
    - Response interception: check output for @-mentions
    - Approval workflows: #chain-name prefix for manual approval
    - Chain timeout/expiry (24h default)

    Chains are created when:
    1. A user sends ``@team_id message`` → Router calls ``start_chain()``
    2. An agent's response mentions another agent via @-mention while in a chain
    """

    def __init__(
        self,
        registry: AgentRegistry,
        bus: MessageBus,
        emitter: EventEmitter | None = None,
    ) -> None:
        self._registry = registry
        self._bus = bus
        self._emitter = emitter
        self._chains: dict[str, ChainContext] = {}  # chain_id → context
        self._named_chains: dict[str, str] = {}  # chain_name → chain_id
        self._agent_chains: dict[str, str] = {}  # agent_id → chain_id (active)

    # ── Chain lifecycle ────────────────────────────────────

    async def start_chain(
        self,
        team_id: str,
        msg: InboundMessage,
        content: str,
    ) -> ChainContext | None:
        """
        Start a new chain for a team.

        Routes the message to the team's leader agent. If approval_mode
        is not ``auto``, the chain waits for user approval before
        proceeding to subsequent agents.
        """
        from nanobot.config.schema import TeamConfig

        teams = self._registry._config.agents.teams
        team_cfg = teams.get(team_id)
        if not team_cfg:
            logger.warning("Unknown team '{}'", team_id)
            return None

        chain_id = str(uuid.uuid4())[:12]
        ctx = ChainContext(
            chain_id=chain_id,
            team_id=team_id,
            leader_id=team_cfg.leader,
            approval_mode=team_cfg.approval_mode,
            origin_channel=msg.channel,
            origin_chat_id=msg.chat_id,
        )
        self._chains[chain_id] = ctx

        # Emit chain.delegated event
        await self._emit_chain_event("chain.delegated", ctx, {
            "team_id": team_id,
            "leader_id": team_cfg.leader,
            "content_preview": content[:100],
        })

        logger.info(
            "Chain {} started for team '{}', leader='{}', approval={}",
            chain_id, team_id, team_cfg.leader, team_cfg.approval_mode,
        )

        # Dispatch to the leader agent
        await self._execute_agent_in_chain(ctx, team_cfg.leader, content, msg)
        return ctx

    async def handle_approval(
        self,
        chain_name: str,
        msg: InboundMessage,
        body: str,
    ) -> None:
        """
        Handle a ``#chain-name`` approval/control command.

        Supported forms:
        - ``#name`` — approve and continue
        - ``#name guidance text`` — approve with additional context
        - ``#name cancel`` — cancel the chain
        - ``#name skip agent-id`` — skip a specific agent
        """
        chain_id = self._named_chains.get(chain_name)
        if not chain_id or chain_id not in self._chains:
            await self._bus.publish_outbound(OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=f"No pending chain named '{chain_name}'.",
            ))
            return

        ctx = self._chains[chain_id]
        body_lower = body.strip().lower()

        if body_lower == "cancel":
            ctx.status = "cancelled"
            self._cleanup_chain(ctx)
            await self._emit_chain_event("chain.completed", ctx, {"status": "cancelled"})
            await self._bus.publish_outbound(OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=f"Chain '{chain_name}' cancelled.",
            ))
            return

        if body_lower.startswith("skip "):
            skip_agent = body[5:].strip()
            if ctx.pending_agent == skip_agent:
                ctx.pending_agent = None
                ctx.pending_content = None
                await self._bus.publish_outbound(OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=f"Skipped agent '{skip_agent}' in chain '{chain_name}'.",
                ))
            else:
                await self._bus.publish_outbound(OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=f"Agent '{skip_agent}' is not pending in chain '{chain_name}'.",
                ))
            return

        # Approve — continue with pending delegation
        if ctx.status != "awaiting_approval" or not ctx.pending_agent:
            await self._bus.publish_outbound(OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=f"Chain '{chain_name}' is not awaiting approval.",
            ))
            return

        ctx.status = "active"
        await self._emit_chain_event("chain.approved", ctx, {
            "agent_id": ctx.pending_agent,
            "guidance": body if body else None,
        })

        # If user provided guidance, append it to the content
        content = ctx.pending_content or ""
        if body:
            content = f"{content}\n\n[User guidance]: {body}"

        pending_agent = ctx.pending_agent
        ctx.pending_agent = None
        ctx.pending_content = None

        # Create a synthetic InboundMessage for the agent
        synth_msg = InboundMessage(
            channel=ctx.origin_channel,
            sender_id=msg.sender_id,
            chat_id=ctx.origin_chat_id,
            content=content,
        )
        await self._execute_agent_in_chain(ctx, pending_agent, content, synth_msg)

    # ── Response interception ──────────────────────────────

    def find_chain_for_agent(self, agent_id: str) -> ChainContext | None:
        """Find an active chain that the given agent is currently part of."""
        chain_id = self._agent_chains.get(agent_id)
        if chain_id:
            ctx = self._chains.get(chain_id)
            if ctx and ctx.status in ("active", "awaiting_approval"):
                return ctx
        return None

    async def intercept_response(
        self,
        ctx: ChainContext,
        agent_id: str,
        response_content: str,
        original_msg: InboundMessage,
    ) -> bool:
        """
        Check agent output for @-mentions and delegate if appropriate.

        Returns True if the response was intercepted (delegation happened),
        False if the response should be published directly to the user.
        """
        # Record agent output
        ctx.agents_called.append(agent_id)
        ctx.outputs[agent_id] = response_content

        # Remove agent from active tracking
        if self._agent_chains.get(agent_id) == ctx.chain_id:
            del self._agent_chains[agent_id]

        # Parse @-mentions from response
        mentions = _AT_MENTION.findall(response_content)
        target_agents = [
            m for m in mentions
            if self._registry.has_agent(m) and m != agent_id
        ]

        if not target_agents:
            # No delegation — chain complete, publish to user
            ctx.status = "completed"
            await self._emit_chain_event("chain.completed", ctx, {
                "status": "completed",
                "agents_called": ctx.agents_called,
            })
            self._cleanup_chain(ctx)
            return False

        if len(target_agents) == 1:
            # Sequential delegation to one agent
            target = target_agents[0]
            await self._handle_delegation(ctx, target, response_content, original_msg)
            return True

        # Fan-out: multiple agents mentioned
        await self._execute_fanout(ctx, target_agents, response_content, original_msg)
        return True

    # ── Execution helpers ──────────────────────────────────

    async def _execute_agent_in_chain(
        self,
        ctx: ChainContext,
        agent_id: str,
        content: str,
        msg: InboundMessage,
    ) -> None:
        """Execute an agent as part of a chain."""
        self._agent_chains[agent_id] = ctx.chain_id

        agent = self._registry.get_or_create(agent_id)
        await agent._connect_mcp()

        # Prefix content with chain context for the agent
        chain_header = (
            f"[Chain {ctx.chain_id} | Team: {ctx.team_id}]\n"
            f"Previous agents: {', '.join(ctx.agents_called) or 'none'}\n"
        )
        if ctx.outputs:
            last_agent = ctx.agents_called[-1] if ctx.agents_called else None
            if last_agent and last_agent in ctx.outputs:
                chain_header += f"Last output from @{last_agent}:\n{ctx.outputs[last_agent][:500]}\n"
        chain_header += f"\nYour task:\n{content}"

        routed_msg = InboundMessage(
            channel=msg.channel,
            sender_id=msg.sender_id,
            chat_id=msg.chat_id,
            content=chain_header,
            timestamp=msg.timestamp,
            media=msg.media,
            metadata=msg.metadata,
        )

        response = await agent.process_message(routed_msg)
        if response and response.content:
            intercepted = await self.intercept_response(ctx, agent_id, response.content, msg)
            if not intercepted:
                await self._bus.publish_outbound(response)
        elif response:
            await self._bus.publish_outbound(response)

    async def _handle_delegation(
        self,
        ctx: ChainContext,
        target_agent_id: str,
        content: str,
        msg: InboundMessage,
    ) -> None:
        """Handle delegation to a single agent, respecting approval mode."""
        needs_approval = self._needs_approval(ctx)

        if needs_approval:
            # Generate a chain name for approval if we don't have one
            if not ctx.chain_name:
                ctx.chain_name = f"chain-{ctx.chain_id[:6]}"
                self._named_chains[ctx.chain_name] = ctx.chain_id

            ctx.status = "awaiting_approval"
            ctx.pending_agent = target_agent_id
            ctx.pending_content = content

            await self._emit_chain_event("chain.awaiting_approval", ctx, {
                "pending_agent": target_agent_id,
                "chain_name": ctx.chain_name,
            })

            await self._bus.publish_outbound(OutboundMessage(
                channel=ctx.origin_channel,
                chat_id=ctx.origin_chat_id,
                content=(
                    f"🔗 Chain '{ctx.chain_name}' wants to delegate to @{target_agent_id}.\n"
                    f"Reply with:\n"
                    f"  `#{ctx.chain_name}` — approve\n"
                    f"  `#{ctx.chain_name} cancel` — cancel chain\n"
                    f"  `#{ctx.chain_name} skip {target_agent_id}` — skip this agent\n"
                    f"  `#{ctx.chain_name} your guidance here` — approve with context"
                ),
            ))
            return

        # Auto-approve — execute immediately
        await self._emit_chain_event("chain.delegated", ctx, {
            "from_agent": ctx.agents_called[-1] if ctx.agents_called else None,
            "to_agent": target_agent_id,
        })
        await self._execute_agent_in_chain(ctx, target_agent_id, content, msg)

    async def _execute_fanout(
        self,
        ctx: ChainContext,
        target_agents: list[str],
        content: str,
        msg: InboundMessage,
    ) -> None:
        """Execute multiple agents in parallel (fan-out) and combine results."""
        logger.info("Chain {} fan-out to: {}", ctx.chain_id, target_agents)

        # For fan-out, we run agents in parallel and combine their outputs
        tasks = []
        for target_id in target_agents:
            # Create a sub-context for tracking
            self._agent_chains[target_id] = ctx.chain_id

            agent = self._registry.get_or_create(target_id)
            await agent._connect_mcp()

            routed_msg = InboundMessage(
                channel=msg.channel,
                sender_id=msg.sender_id,
                chat_id=msg.chat_id,
                content=content,
                timestamp=msg.timestamp,
                media=msg.media,
                metadata=msg.metadata,
            )
            tasks.append(self._fanout_agent_task(agent, target_id, routed_msg, ctx))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Combine results
        combined_parts = []
        for target_id, result in zip(target_agents, results):
            if isinstance(result, Exception):
                combined_parts.append(f"**@{target_id}**: Error — {result}")
                logger.error("Fan-out agent '{}' failed: {}", target_id, result)
            elif result:
                combined_parts.append(f"**@{target_id}**:\n{result}")
                ctx.outputs[target_id] = result
                ctx.agents_called.append(target_id)

        combined = "\n\n---\n\n".join(combined_parts)
        ctx.status = "completed"
        await self._emit_chain_event("chain.completed", ctx, {
            "status": "completed",
            "agents_called": ctx.agents_called,
            "fanout": True,
        })
        self._cleanup_chain(ctx)

        await self._bus.publish_outbound(OutboundMessage(
            channel=ctx.origin_channel,
            chat_id=ctx.origin_chat_id,
            content=combined,
        ))

    async def _fanout_agent_task(
        self,
        agent: Any,
        agent_id: str,
        msg: InboundMessage,
        ctx: ChainContext,
    ) -> str | None:
        """Run a single agent in a fan-out and return its response content."""
        try:
            response = await agent.process_message(msg)
            return response.content if response else None
        finally:
            # Clean up agent chain tracking
            if self._agent_chains.get(agent_id) == ctx.chain_id:
                del self._agent_chains[agent_id]

    # ── Approval logic ─────────────────────────────────────

    def _needs_approval(self, ctx: ChainContext) -> bool:
        """Determine if the next delegation needs user approval."""
        if ctx.approval_mode == "auto":
            return False
        if ctx.approval_mode == "confirm":
            return True
        if ctx.approval_mode == "first_only":
            # Only the first delegation needs approval
            return len(ctx.agents_called) <= 1
        return False

    # ── Cleanup / expiry ───────────────────────────────────

    def _cleanup_chain(self, ctx: ChainContext) -> None:
        """Remove a chain from tracking."""
        self._chains.pop(ctx.chain_id, None)
        if ctx.chain_name:
            self._named_chains.pop(ctx.chain_name, None)
        # Remove any agent→chain mappings for this chain
        to_remove = [
            aid for aid, cid in self._agent_chains.items()
            if cid == ctx.chain_id
        ]
        for aid in to_remove:
            del self._agent_chains[aid]

    async def cleanup_expired(self) -> int:
        """Remove expired chains. Returns count of chains cleaned up."""
        now = datetime.now(UTC)
        expired = [
            ctx for ctx in self._chains.values()
            if now > ctx.expires_at
        ]
        for ctx in expired:
            logger.info("Chain {} expired (started {})", ctx.chain_id, ctx.started_at)
            ctx.status = "expired"
            await self._emit_chain_event("chain.completed", ctx, {"status": "expired"})
            self._cleanup_chain(ctx)
        return len(expired)

    @property
    def active_chain_count(self) -> int:
        """Number of currently active chains."""
        return len(self._chains)

    # ── Event helpers ──────────────────────────────────────

    async def _emit_chain_event(
        self, event_type_str: str, ctx: ChainContext, payload: dict[str, Any],
    ) -> None:
        """Emit a chain-related event."""
        if not self._emitter:
            return
        from nanobot.events.models import Event, EventType

        try:
            et = EventType(event_type_str)
        except ValueError:
            logger.warning("Unknown event type: {}", event_type_str)
            return

        await self._emitter.emit(Event(
            event_type=et,
            chain_id=ctx.chain_id,
            payload=payload,
        ))
