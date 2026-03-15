"""Agent registry for managing named agents with lazy instantiation."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus
    from nanobot.config.schema import AgentConfig, Config
    from nanobot.cron.service import CronService
    from nanobot.events.emitter import EventEmitter
    from nanobot.providers.base import LLMProvider
    from nanobot.session.manager import SessionManager


class AgentRegistry:
    """
    Registry of named agents with lazy instantiation.

    Each agent has its own model, workspace, tools, system prompt, session state,
    and ContextBuilder. Agents are created on first use to avoid spinning up
    resources at boot time.

    When no agents are configured (zero-agent config), the "default" agent
    uses ``AgentDefaults`` — preserving backward compatibility.
    """

    def __init__(
        self,
        config: Config,
        provider: LLMProvider,
        bus: MessageBus,
        emitter: EventEmitter | None = None,
        cron_service: CronService | None = None,
        session_manager: SessionManager | None = None,
    ) -> None:
        self._config = config
        self._provider = provider
        self._bus = bus
        self._emitter = emitter
        self._cron_service = cron_service
        self._session_manager = session_manager
        self._agents: dict[str, AgentLoop] = {}

    @property
    def agent_ids(self) -> list[str]:
        """All configured agent IDs (including implicit 'default')."""
        ids = list(self._config.agents.agents.keys())
        if "default" not in ids:
            ids.insert(0, "default")
        return ids

    @property
    def team_ids(self) -> list[str]:
        """All configured team IDs."""
        return list(self._config.agents.teams.keys())

    def has_agent(self, agent_id: str) -> bool:
        """Check if an agent ID is configured (or is 'default')."""
        return agent_id == "default" or agent_id in self._config.agents.agents

    def has_team(self, team_id: str) -> bool:
        """Check if a team ID is configured."""
        return team_id in self._config.agents.teams

    def resolve_agent_config(self, agent_id: str) -> dict[str, Any]:
        """
        Resolve effective configuration for an agent.

        Per-agent values override defaults. Returns a flat dict of kwargs
        suitable for ``AgentLoop.__init__``.
        """
        defaults = self._config.agents.defaults
        agent_cfg = self._config.agents.agents.get(agent_id)

        # Resolve workspace path
        if agent_cfg and agent_cfg.workspace:
            workspace = Path(agent_cfg.workspace).expanduser()
        elif agent_id != "default" and agent_cfg:
            # Named agents without explicit workspace get a sub-directory
            workspace = self._config.workspace_path / agent_id
        else:
            workspace = self._config.workspace_path

        def _pick(field: str, default_val: Any) -> Any:
            """Pick agent-specific value or fall back to default."""
            if agent_cfg:
                val = getattr(agent_cfg, field, None)
                if val is not None:
                    return val
            return default_val

        return {
            "workspace": workspace,
            "model": _pick("model", defaults.model),
            "temperature": _pick("temperature", defaults.temperature),
            "max_tokens": _pick("max_tokens", defaults.max_tokens),
            "max_iterations": _pick("max_iterations", defaults.max_tool_iterations),
            "memory_window": _pick("memory_window", defaults.memory_window),
            "reasoning_effort": _pick("reasoning_effort", defaults.reasoning_effort),
            "system_prompt_override": agent_cfg.system_prompt if agent_cfg else None,
            "tool_allowlist": agent_cfg.tools if agent_cfg else None,
        }

    def get_or_create(self, agent_id: str) -> AgentLoop:
        """
        Get or lazily create an ``AgentLoop`` for the given agent ID.

        The first call for a given ``agent_id`` creates the loop; subsequent
        calls return the cached instance.
        """
        if agent_id in self._agents:
            return self._agents[agent_id]

        if not self.has_agent(agent_id):
            logger.warning("Unknown agent '{}', falling back to default", agent_id)
            agent_id = "default"
            if agent_id in self._agents:
                return self._agents[agent_id]

        resolved = self.resolve_agent_config(agent_id)
        workspace = resolved["workspace"]
        workspace.mkdir(parents=True, exist_ok=True)

        from nanobot.agent.loop import AgentLoop
        from nanobot.session.manager import SessionManager
        from nanobot.utils.helpers import sync_workspace_templates

        sync_workspace_templates(workspace)

        agent = AgentLoop(
            bus=self._bus,
            provider=self._provider,
            workspace=workspace,
            model=resolved["model"],
            temperature=resolved["temperature"],
            max_tokens=resolved["max_tokens"],
            max_iterations=resolved["max_iterations"],
            memory_window=resolved["memory_window"],
            reasoning_effort=resolved["reasoning_effort"],
            brave_api_key=self._config.tools.web.search.api_key or None,
            web_proxy=self._config.tools.web.proxy or None,
            exec_config=self._config.tools.exec,
            cron_service=self._cron_service,
            restrict_to_workspace=self._config.tools.restrict_to_workspace,
            session_manager=self._session_manager or SessionManager(workspace),
            mcp_servers=self._config.tools.mcp_servers,
            channels_config=self._config.channels,
            emitter=self._emitter,
        )
        agent.agent_id = agent_id

        # Also sync agent_id to ToolRegistry
        agent.tools._agent_id = agent_id

        self._agents[agent_id] = agent
        logger.info("Created agent '{}' (model={}, workspace={})", agent_id, resolved["model"], workspace)
        return agent

    def get_cached(self, agent_id: str) -> AgentLoop | None:
        """Return a cached agent if it exists, without creating one."""
        return self._agents.get(agent_id)

    async def close_all(self) -> None:
        """Close MCP connections for all cached agents."""
        for agent_id, agent in self._agents.items():
            try:
                await agent.close_mcp()
            except Exception as e:
                logger.error("Error closing agent '{}': {}", agent_id, e)

    def stop_all(self) -> None:
        """Stop all cached agents."""
        for agent in self._agents.values():
            agent.stop()
