"""Tests for Phase 2: Multi-Agent Core (Router, AgentRegistry, ChainManager, DelegateTool)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import (
    AgentConfig,
    AgentsConfig,
    AgentDefaults,
    Config,
    TeamConfig,
)


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def bus():
    return MessageBus()


@pytest.fixture
def mock_provider():
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return provider


@pytest.fixture
def basic_config(tmp_path):
    """Config with no agents or teams (backward compatible)."""
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    return config


@pytest.fixture
def multi_agent_config(tmp_path):
    """Config with named agents and a team."""
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    config.agents.agents = {
        "coder": AgentConfig(model="test-model/coder", temperature=0.2),
        "reviewer": AgentConfig(model="test-model/reviewer", temperature=0.1),
    }
    config.agents.teams = {
        "dev": TeamConfig(leader="coder", agents=["coder", "reviewer"], approval_mode="auto"),
        "strict": TeamConfig(leader="coder", agents=["coder", "reviewer"], approval_mode="confirm"),
    }
    return config


@pytest.fixture
def registry(multi_agent_config, mock_provider, bus):
    from nanobot.agent.registry import AgentRegistry

    return AgentRegistry(
        config=multi_agent_config,
        provider=mock_provider,
        bus=bus,
    )


@pytest.fixture
def basic_registry(basic_config, mock_provider, bus):
    from nanobot.agent.registry import AgentRegistry

    return AgentRegistry(
        config=basic_config,
        provider=mock_provider,
        bus=bus,
    )


# ═══════════════════════════════════════════════════════════
# Config Schema Tests
# ═══════════════════════════════════════════════════════════


class TestConfigSchema:
    """Test AgentConfig, TeamConfig additions to config schema."""

    def test_agent_config_defaults_to_none(self):
        cfg = AgentConfig()
        assert cfg.model is None
        assert cfg.workspace is None
        assert cfg.system_prompt is None
        assert cfg.tools is None
        assert cfg.temperature is None

    def test_agent_config_custom_values(self):
        cfg = AgentConfig(model="gpt-4o", temperature=0.5, max_tokens=2048)
        assert cfg.model == "gpt-4o"
        assert cfg.temperature == 0.5
        assert cfg.max_tokens == 2048

    def test_team_config_defaults(self):
        cfg = TeamConfig(leader="alpha")
        assert cfg.leader == "alpha"
        assert cfg.agents == []
        assert cfg.approval_mode == "auto"

    def test_team_config_confirm_mode(self):
        cfg = TeamConfig(leader="a", agents=["a", "b"], approval_mode="confirm")
        assert cfg.approval_mode == "confirm"

    def test_agents_config_has_agents_and_teams(self):
        ac = AgentsConfig(
            agents={"coder": AgentConfig(model="x")},
            teams={"dev": TeamConfig(leader="coder")},
        )
        assert "coder" in ac.agents
        assert "dev" in ac.teams

    def test_config_backward_compatible_no_agents(self):
        """Zero-agent config still works — AgentsConfig.agents defaults to {}."""
        config = Config()
        assert config.agents.agents == {}
        assert config.agents.teams == {}


# ═══════════════════════════════════════════════════════════
# AgentRegistry Tests
# ═══════════════════════════════════════════════════════════


class TestAgentRegistry:
    """Test agent registry with lazy instantiation."""

    def test_agent_ids_includes_default(self, registry):
        ids = registry.agent_ids
        assert "default" in ids
        assert "coder" in ids
        assert "reviewer" in ids

    def test_has_agent(self, registry):
        assert registry.has_agent("default")
        assert registry.has_agent("coder")
        assert registry.has_agent("reviewer")
        assert not registry.has_agent("unknown")

    def test_has_team(self, registry):
        assert registry.has_team("dev")
        assert registry.has_team("strict")
        assert not registry.has_team("unknown")

    def test_resolve_default_config(self, basic_registry, basic_config):
        resolved = basic_registry.resolve_agent_config("default")
        assert resolved["model"] == basic_config.agents.defaults.model
        assert resolved["system_prompt_override"] is None
        assert resolved["tool_allowlist"] is None

    def test_resolve_named_agent_config(self, registry):
        resolved = registry.resolve_agent_config("coder")
        assert resolved["model"] == "test-model/coder"
        assert resolved["temperature"] == 0.2

    def test_resolve_named_agent_fallback(self, registry):
        """Named agent without explicit max_tokens falls back to defaults."""
        resolved = registry.resolve_agent_config("coder")
        assert resolved["max_tokens"] == registry._config.agents.defaults.max_tokens

    def test_resolve_workspace_for_named_agent(self, registry, multi_agent_config):
        resolved = registry.resolve_agent_config("coder")
        expected = Path(multi_agent_config.agents.defaults.workspace).expanduser() / "coder"
        assert resolved["workspace"] == expected

    def test_team_ids(self, registry):
        assert "dev" in registry.team_ids
        assert "strict" in registry.team_ids

    def test_get_cached_before_create(self, registry):
        assert registry.get_cached("coder") is None


# ═══════════════════════════════════════════════════════════
# Router Tests
# ═══════════════════════════════════════════════════════════


class TestRouterParsing:
    """Test Router prefix parsing logic."""

    def _make_router(self, registry):
        from nanobot.agent.chain import ChainManager
        from nanobot.agent.router import Router

        chain_mgr = ChainManager(registry=registry, bus=MagicMock())
        return Router(bus=MagicMock(), registry=registry, chain_manager=chain_mgr)

    def test_parse_no_prefix(self, registry):
        router = self._make_router(registry)
        result = router.parse("hello world")
        assert result.kind == "default"
        assert result.content == "hello world"

    def test_parse_agent_prefix(self, registry):
        router = self._make_router(registry)
        result = router.parse("@coder write some code")
        assert result.kind == "agent"
        assert result.agent_id == "coder"
        assert result.content == "write some code"

    def test_parse_team_prefix(self, registry):
        router = self._make_router(registry)
        result = router.parse("@dev build a feature")
        assert result.kind == "team"
        assert result.team_id == "dev"
        assert result.content == "build a feature"

    def test_parse_chain_prefix(self, registry):
        router = self._make_router(registry)
        result = router.parse("#my-chain approve")
        assert result.kind == "chain"
        assert result.chain_name == "my-chain"
        assert result.content == "approve"

    def test_parse_chain_cancel(self, registry):
        router = self._make_router(registry)
        result = router.parse("#task cancel")
        assert result.kind == "chain"
        assert result.chain_name == "task"
        assert result.content == "cancel"

    def test_parse_chain_skip(self, registry):
        router = self._make_router(registry)
        result = router.parse("#task skip coder")
        assert result.kind == "chain"
        assert result.chain_name == "task"
        assert result.content == "skip coder"

    def test_parse_unknown_at_prefix_is_default(self, registry):
        """Unknown @prefix routes to default with full content."""
        router = self._make_router(registry)
        result = router.parse("@someone hello")
        assert result.kind == "default"
        assert result.content == "@someone hello"

    def test_parse_empty_body_after_agent(self, registry):
        """@agent with no body uses full content."""
        router = self._make_router(registry)
        result = router.parse("@coder")
        assert result.kind == "agent"
        assert result.agent_id == "coder"
        assert result.content == "@coder"

    def test_parse_preserves_multiline(self, registry):
        router = self._make_router(registry)
        result = router.parse("@coder line1\nline2\nline3")
        assert result.kind == "agent"
        assert "line2" in result.content

    def test_parse_chain_empty_body(self, registry):
        router = self._make_router(registry)
        result = router.parse("#my-chain")
        assert result.kind == "chain"
        assert result.chain_name == "my-chain"
        assert result.content == ""


# ═══════════════════════════════════════════════════════════
# ChainManager Tests
# ═══════════════════════════════════════════════════════════


class TestChainManager:
    """Test chain lifecycle, approval, and interception."""

    def _make_chain_manager(self, registry, bus):
        from nanobot.agent.chain import ChainManager
        return ChainManager(registry=registry, bus=bus)

    def test_find_chain_for_agent_empty(self, registry, bus):
        cm = self._make_chain_manager(registry, bus)
        assert cm.find_chain_for_agent("coder") is None

    def test_active_chain_count_starts_zero(self, registry, bus):
        cm = self._make_chain_manager(registry, bus)
        assert cm.active_chain_count == 0

    def test_needs_approval_auto(self, registry, bus):
        from nanobot.agent.chain import ChainContext
        cm = self._make_chain_manager(registry, bus)
        ctx = ChainContext(chain_id="test", approval_mode="auto")
        assert cm._needs_approval(ctx) is False

    def test_needs_approval_confirm(self, registry, bus):
        from nanobot.agent.chain import ChainContext
        cm = self._make_chain_manager(registry, bus)
        ctx = ChainContext(chain_id="test", approval_mode="confirm")
        assert cm._needs_approval(ctx) is True

    def test_needs_approval_first_only_first_call(self, registry, bus):
        from nanobot.agent.chain import ChainContext
        cm = self._make_chain_manager(registry, bus)
        ctx = ChainContext(chain_id="test", approval_mode="first_only", agents_called=["a"])
        assert cm._needs_approval(ctx) is True

    def test_needs_approval_first_only_subsequent(self, registry, bus):
        from nanobot.agent.chain import ChainContext
        cm = self._make_chain_manager(registry, bus)
        ctx = ChainContext(chain_id="test", approval_mode="first_only", agents_called=["a", "b"])
        assert cm._needs_approval(ctx) is False

    @pytest.mark.asyncio
    async def test_handle_approval_unknown_chain(self, registry, bus):
        cm = self._make_chain_manager(registry, bus)
        msg = InboundMessage(channel="test", sender_id="user", chat_id="123", content="#unknown")
        await cm.handle_approval("unknown", msg, "")
        out = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
        assert "No pending chain" in out.content

    @pytest.mark.asyncio
    async def test_cleanup_expired_empty(self, registry, bus):
        cm = self._make_chain_manager(registry, bus)
        count = await cm.cleanup_expired()
        assert count == 0

    @pytest.mark.asyncio
    async def test_cleanup_expired_removes_old_chain(self, registry, bus):
        from nanobot.agent.chain import ChainContext
        cm = self._make_chain_manager(registry, bus)
        ctx = ChainContext(
            chain_id="old-chain",
            team_id="dev",
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        cm._chains["old-chain"] = ctx
        count = await cm.cleanup_expired()
        assert count == 1
        assert cm.active_chain_count == 0


# ═══════════════════════════════════════════════════════════
# ChainContext Tests
# ═══════════════════════════════════════════════════════════


class TestChainContext:
    """Test ChainContext data model."""

    def test_chain_context_defaults(self):
        from nanobot.agent.chain import ChainContext
        ctx = ChainContext(chain_id="abc")
        assert ctx.status == "active"
        assert ctx.agents_called == []
        assert ctx.outputs == {}
        assert ctx.pending_agent is None
        assert ctx.approval_mode == "auto"
        assert ctx.started_at is not None
        assert ctx.expires_at > ctx.started_at

    def test_chain_context_ttl(self):
        from nanobot.agent.chain import ChainContext, _CHAIN_TTL
        ctx = ChainContext(chain_id="abc")
        delta = ctx.expires_at - ctx.started_at
        assert abs(delta - _CHAIN_TTL) < timedelta(seconds=1)


# ═══════════════════════════════════════════════════════════
# DelegateTool Tests
# ═══════════════════════════════════════════════════════════


class TestDelegateTool:
    """Test DelegateTool properties and validation."""

    def test_delegate_tool_name(self, registry):
        from nanobot.agent.tools.delegate import DelegateTool
        tool = DelegateTool(registry=registry)
        assert tool.name == "delegate"

    def test_delegate_tool_description_lists_agents(self, registry):
        from nanobot.agent.tools.delegate import DelegateTool
        tool = DelegateTool(registry=registry)
        desc = tool.description
        assert "coder" in desc
        assert "reviewer" in desc

    def test_delegate_tool_parameters(self, registry):
        from nanobot.agent.tools.delegate import DelegateTool
        tool = DelegateTool(registry=registry)
        params = tool.parameters
        assert "agent_id" in params["properties"]
        assert "task" in params["properties"]
        assert "context" in params["properties"]
        assert params["required"] == ["agent_id", "task"]

    @pytest.mark.asyncio
    async def test_delegate_to_unknown_agent(self, registry):
        from nanobot.agent.tools.delegate import DelegateTool
        tool = DelegateTool(registry=registry)
        result = await tool.execute(agent_id="nonexistent", task="test")
        assert "Error: Unknown agent" in result

    def test_delegate_set_context(self, registry):
        from nanobot.agent.tools.delegate import DelegateTool
        tool = DelegateTool(registry=registry)
        tool.set_context("telegram", "12345")
        assert tool._origin_channel == "telegram"
        assert tool._origin_chat_id == "12345"


# ═══════════════════════════════════════════════════════════
# Router Event Emission Tests
# ═══════════════════════════════════════════════════════════


class TestRouterEvents:
    """Test that Router emits message.routed events."""

    @pytest.mark.asyncio
    async def test_dispatch_emits_message_routed(self, registry, bus):
        from nanobot.agent.chain import ChainManager
        from nanobot.agent.router import Router
        from nanobot.events.emitter import EventEmitter

        emitter = EventEmitter()
        captured = []

        async def _capture(event):
            captured.append(event)

        emitter.on("message.routed", _capture)

        chain_mgr = ChainManager(registry=registry, bus=bus)
        router = Router(bus=bus, registry=registry, chain_manager=chain_mgr, emitter=emitter)

        msg = InboundMessage(
            channel="test", sender_id="user", chat_id="123", content="hello",
        )

        # Mock the agent dispatch to avoid full processing
        with patch.object(router, "_dispatch_to_agent", new_callable=AsyncMock):
            await router._dispatch(msg)

        assert len(captured) == 1
        assert captured[0].event_type.value == "message.routed"
        assert captured[0].payload["kind"] == "default"

    @pytest.mark.asyncio
    async def test_dispatch_agent_prefix_event(self, registry, bus):
        from nanobot.agent.chain import ChainManager
        from nanobot.agent.router import Router
        from nanobot.events.emitter import EventEmitter

        emitter = EventEmitter()
        captured = []

        async def _capture(event):
            captured.append(event)

        emitter.on("message.routed", _capture)

        chain_mgr = ChainManager(registry=registry, bus=bus)
        router = Router(bus=bus, registry=registry, chain_manager=chain_mgr, emitter=emitter)

        msg = InboundMessage(
            channel="test", sender_id="user", chat_id="123", content="@coder write code",
        )

        with patch.object(router, "_dispatch_to_agent", new_callable=AsyncMock):
            await router._dispatch(msg)

        assert len(captured) == 1
        assert captured[0].payload["kind"] == "agent"
        assert captured[0].payload["agent_id"] == "coder"


# ═══════════════════════════════════════════════════════════
# Integration Tests
# ═══════════════════════════════════════════════════════════


class TestRouteResult:
    """Test RouteResult data class."""

    def test_route_result_defaults(self):
        from nanobot.agent.router import RouteResult
        r = RouteResult(kind="default", content="hello")
        assert r.agent_id is None
        assert r.team_id is None
        assert r.chain_name is None

    def test_route_result_agent(self):
        from nanobot.agent.router import RouteResult
        r = RouteResult(kind="agent", agent_id="coder", content="task")
        assert r.kind == "agent"
        assert r.agent_id == "coder"


class TestBackwardCompatibility:
    """Ensure zero-agent config works identically to pre-Phase 2 behavior."""

    def test_zero_agent_config_has_default(self, basic_registry):
        ids = basic_registry.agent_ids
        assert ids == ["default"]

    def test_zero_agent_no_teams(self, basic_registry):
        assert basic_registry.team_ids == []

    def test_zero_agent_resolve_uses_defaults(self, basic_registry, basic_config):
        resolved = basic_registry.resolve_agent_config("default")
        assert resolved["model"] == basic_config.agents.defaults.model
        assert resolved["workspace"] == basic_config.workspace_path

    def test_unknown_agent_falls_back_to_default(self, basic_registry):
        """has_agent returns False for unknown, get_or_create falls back."""
        assert not basic_registry.has_agent("nonexistent")


class TestChainManagerCleanup:
    """Test chain cleanup internals."""

    def test_cleanup_removes_agent_chain_mapping(self, registry, bus):
        from nanobot.agent.chain import ChainContext, ChainManager
        cm = ChainManager(registry=registry, bus=bus)
        ctx = ChainContext(chain_id="c1", chain_name="test-chain")
        cm._chains["c1"] = ctx
        cm._named_chains["test-chain"] = "c1"
        cm._agent_chains["coder"] = "c1"

        cm._cleanup_chain(ctx)
        assert "c1" not in cm._chains
        assert "test-chain" not in cm._named_chains
        assert "coder" not in cm._agent_chains
