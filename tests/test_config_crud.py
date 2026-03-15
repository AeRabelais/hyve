"""Tests for dashboard config CRUD API endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from nanobot.config.schema import Config


# ── Helpers ────────────────────────────────────────────────


def _make_app(tmp_path: Path, initial_config: dict | None = None):
    """Create a test dashboard app with a temp config file."""
    from nanobot.events.emitter import EventEmitter
    from nanobot.events.store import EventStore
    from nanobot.dashboard.server import create_app

    config_path = tmp_path / "config.json"
    if initial_config:
        config_path.write_text(json.dumps(initial_config, indent=2))
    else:
        config_path.write_text("{}")

    emitter = EventEmitter()
    store = EventStore(tmp_path / "events.db")
    config = Config.model_validate(initial_config or {})
    app = create_app(emitter, store, config=config, config_path=config_path)
    return app, config_path


def _read_config(config_path: Path) -> dict:
    """Read and parse config file."""
    return json.loads(config_path.read_text())


# ── Tests ──────────────────────────────────────────────────


class TestGetConfigFull:
    """GET /api/config/full"""

    def test_returns_full_config(self, tmp_path):
        from starlette.testclient import TestClient

        initial = {
            "agents": {
                "defaults": {"model": "anthropic/claude-sonnet-4-20250514", "workspace": "/tmp/ws"},
                "agents": {"coder": {"model": "deepseek/deepseek-chat"}},
                "teams": {},
            },
            "gateway": {"port": 18790},
        }
        app, _ = _make_app(tmp_path, initial)
        client = TestClient(app)

        resp = client.get("/api/config/full")
        assert resp.status_code == 200
        data = resp.json()
        # Check that defaults come through
        assert data["agents"]["defaults"]["model"] == "anthropic/claude-sonnet-4-20250514"
        # Check that agent config comes through
        assert "coder" in data["agents"]["agents"]

    def test_empty_config(self, tmp_path):
        from starlette.testclient import TestClient

        app, _ = _make_app(tmp_path)
        client = TestClient(app)

        resp = client.get("/api/config/full")
        assert resp.status_code == 200
        data = resp.json()
        # Should return valid default config
        assert "agents" in data
        assert "providers" in data


class TestPutConfigFull:
    """PUT /api/config/full"""

    def test_saves_valid_config(self, tmp_path):
        from starlette.testclient import TestClient

        app, config_path = _make_app(tmp_path)
        client = TestClient(app)

        new_config = {
            "agents": {
                "defaults": {"model": "openai/gpt-4o", "workspace": "/tmp/new-ws"},
            },
            "gateway": {"port": 19000},
        }
        resp = client.put("/api/config/full", json=new_config)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

        # Verify file was actually written
        saved = _read_config(config_path)
        assert saved["agents"]["defaults"]["model"] == "openai/gpt-4o"
        assert saved["gateway"]["port"] == 19000

    def test_rejects_invalid_config(self, tmp_path):
        from starlette.testclient import TestClient

        app, _ = _make_app(tmp_path)
        client = TestClient(app)

        # Invalid: gateway.port should be int
        resp = client.put("/api/config/full", json={"gateway": {"port": "not_a_number"}})
        assert resp.status_code == 400


class TestPostAgent:
    """POST /api/config/agents"""

    def test_create_agent(self, tmp_path):
        from starlette.testclient import TestClient

        app, config_path = _make_app(tmp_path)
        client = TestClient(app)

        resp = client.post("/api/config/agents", json={
            "agentId": "researcher",
            "model": "anthropic/claude-sonnet-4-20250514",
            "temperature": 0.3,
            "skills": ["github", "memory"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["agentId"] == "researcher"

        # Verify persisted
        saved = _read_config(config_path)
        assert "researcher" in saved["agents"]["agents"]
        assert saved["agents"]["agents"]["researcher"]["model"] == "anthropic/claude-sonnet-4-20250514"
        assert saved["agents"]["agents"]["researcher"]["skills"] == ["github", "memory"]

    def test_update_existing_agent(self, tmp_path):
        from starlette.testclient import TestClient

        initial = {
            "agents": {
                "agents": {"coder": {"model": "deepseek/deepseek-chat"}},
            },
        }
        app, config_path = _make_app(tmp_path, initial)
        client = TestClient(app)

        resp = client.post("/api/config/agents", json={
            "agentId": "coder",
            "model": "anthropic/claude-sonnet-4-20250514",
        })
        assert resp.status_code == 200

        saved = _read_config(config_path)
        assert saved["agents"]["agents"]["coder"]["model"] == "anthropic/claude-sonnet-4-20250514"

    def test_missing_agent_id(self, tmp_path):
        from starlette.testclient import TestClient

        app, _ = _make_app(tmp_path)
        client = TestClient(app)

        resp = client.post("/api/config/agents", json={"model": "test"})
        assert resp.status_code == 400
        assert "agentId" in resp.json()["error"]


class TestDeleteAgent:
    """DELETE /api/config/agents/{agent_id}"""

    def test_delete_agent(self, tmp_path):
        from starlette.testclient import TestClient

        initial = {
            "agents": {
                "agents": {
                    "coder": {"model": "test-model"},
                    "writer": {"model": "test-model-2"},
                },
            },
        }
        app, config_path = _make_app(tmp_path, initial)
        client = TestClient(app)

        resp = client.delete("/api/config/agents/coder")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        saved = _read_config(config_path)
        assert "coder" not in saved["agents"]["agents"]
        assert "writer" in saved["agents"]["agents"]

    def test_delete_nonexistent_agent(self, tmp_path):
        from starlette.testclient import TestClient

        app, _ = _make_app(tmp_path)
        client = TestClient(app)

        resp = client.delete("/api/config/agents/nonexistent")
        assert resp.status_code == 404


class TestPostTeam:
    """POST /api/config/teams"""

    def test_create_team(self, tmp_path):
        from starlette.testclient import TestClient

        initial = {
            "agents": {
                "agents": {"coder": {}, "reviewer": {}},
            },
        }
        app, config_path = _make_app(tmp_path, initial)
        client = TestClient(app)

        resp = client.post("/api/config/teams", json={
            "teamName": "dev-team",
            "leader": "coder",
            "agents": ["coder", "reviewer"],
            "approvalMode": "confirm",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["teamName"] == "dev-team"

        saved = _read_config(config_path)
        team = saved["agents"]["teams"]["dev-team"]
        assert team["leader"] == "coder"
        assert team["agents"] == ["coder", "reviewer"]
        assert team["approvalMode"] == "confirm"

    def test_missing_team_name(self, tmp_path):
        from starlette.testclient import TestClient

        app, _ = _make_app(tmp_path)
        client = TestClient(app)

        resp = client.post("/api/config/teams", json={"leader": "test"})
        assert resp.status_code == 400
        assert "teamName" in resp.json()["error"]


class TestDeleteTeam:
    """DELETE /api/config/teams/{team_id}"""

    def test_delete_team(self, tmp_path):
        from starlette.testclient import TestClient

        initial = {
            "agents": {
                "teams": {
                    "dev-team": {"leader": "coder", "agents": ["coder"]},
                },
            },
        }
        app, config_path = _make_app(tmp_path, initial)
        client = TestClient(app)

        resp = client.delete("/api/config/teams/dev-team")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        saved = _read_config(config_path)
        assert "dev-team" not in saved["agents"]["teams"]

    def test_delete_nonexistent_team(self, tmp_path):
        from starlette.testclient import TestClient

        app, _ = _make_app(tmp_path)
        client = TestClient(app)

        resp = client.delete("/api/config/teams/nonexistent")
        assert resp.status_code == 404


class TestExistingApiConfig:
    """GET /api/config (existing endpoint, bug fix validation)"""

    def test_teams_have_correct_fields(self, tmp_path):
        from starlette.testclient import TestClient

        initial = {
            "agents": {
                "agents": {"coder": {"model": "test"}},
                "teams": {
                    "dev-team": {
                        "leader": "coder",
                        "agents": ["coder"],
                        "approvalMode": "confirm",
                    },
                },
            },
        }
        app, _ = _make_app(tmp_path, initial)
        client = TestClient(app)

        resp = client.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()

        team = data["teams"]["dev-team"]
        assert team["leader"] == "coder"
        assert team["agents"] == ["coder"]
        assert team["approval_mode"] == "confirm"
        # Old buggy fields should NOT be present
        assert "mode" not in team
        assert "approval" not in team
