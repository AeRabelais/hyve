"""Tests for Phase 3: Layered Memory System.

Covers: DB schema, connection, FTS5 triggers, queries (upsert, search, dedup,
refresh, prune), generator (index + detail files), classifier, pruner, RecallTool,
and config schema additions.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nanobot.config.schema import Config, DecayTTLConfig, MemoryConfig


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test_memory.db"


@pytest.fixture
def conn(db_path):
    """Initialize a fresh test database and return the connection."""
    from nanobot.memory.db.connection import init_db, _connections
    # Clear any cached connections
    _connections.clear()
    c = init_db(db_path)
    yield c
    c.close()
    _connections.clear()


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


def _insert_fact(conn, *, id=None, category="person", entity=None, key="test_key",
                 value="test_value", decay_tier="permanent", ttl_seconds=None,
                 rationale=None, agent_id=None, tags="[]", accessed_at=None):
    """Helper to insert a fact directly."""
    import uuid
    fid = id or str(uuid.uuid4())
    now = (accessed_at or datetime.now(UTC)).isoformat()
    conn.execute(
        "INSERT INTO facts (id, created_at, updated_at, accessed_at, agent_id, "
        "category, entity, key, value, rationale, decay_tier, ttl_seconds, "
        "source_event_id, tags) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (fid, now, now, now, agent_id, category, entity, key, value,
         rationale, decay_tier, ttl_seconds, None, tags),
    )
    conn.commit()
    return fid


# ═══════════════════════════════════════════════════════════
# Config Schema Tests
# ═══════════════════════════════════════════════════════════


class TestMemoryConfig:
    def test_memory_config_defaults(self):
        mc = MemoryConfig()
        assert mc.enabled is False
        assert mc.distillation_model is None
        assert mc.decay.stable_ttl_days == 90
        assert mc.decay.active_ttl_days == 14
        assert mc.decay.session_ttl_hours == 24
        assert mc.decay.checkpoint_ttl_hours == 4
        assert mc.index.max_tokens == 3000
        assert mc.index.active_context_slots == 3

    def test_config_has_memory_field(self):
        config = Config()
        assert hasattr(config, "memory")
        assert isinstance(config.memory, MemoryConfig)
        assert config.memory.enabled is False

    def test_decay_ttl_config(self):
        cfg = DecayTTLConfig(stable_ttl_days=60, active_ttl_days=7)
        assert cfg.stable_ttl_days == 60
        assert cfg.active_ttl_days == 7


# ═══════════════════════════════════════════════════════════
# DB Schema & Connection Tests
# ═══════════════════════════════════════════════════════════


class TestDBInit:
    def test_init_db_creates_tables(self, conn):
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = [t[0] for t in tables]
        assert "facts" in table_names
        assert "memory_events" in table_names

    def test_init_db_creates_fts5(self, conn):
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'facts_fts%'"
        ).fetchall()
        assert len(tables) > 0

    def test_init_db_creates_triggers(self, conn):
        triggers = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        ).fetchall()
        trigger_names = [t[0] for t in triggers]
        assert "facts_ai" in trigger_names
        assert "facts_ad" in trigger_names
        assert "facts_au" in trigger_names

    def test_init_db_creates_indexes(self, conn):
        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
        ).fetchall()
        index_names = [i[0] for i in indexes]
        assert "idx_facts_category_entity" in index_names
        assert "idx_facts_decay_tier" in index_names


# ═══════════════════════════════════════════════════════════
# Data Model Tests
# ═══════════════════════════════════════════════════════════


class TestModels:
    def test_decay_tier_enum(self):
        from nanobot.memory.db.schema import DecayTier
        assert DecayTier.permanent.value == "permanent"
        assert DecayTier.checkpoint.value == "checkpoint"

    def test_fact_category_enum(self):
        from nanobot.memory.db.schema import FactCategory
        assert FactCategory.person.value == "person"
        assert FactCategory.decision.value == "decision"

    def test_fact_default_values(self):
        from nanobot.memory.db.schema import Fact
        f = Fact()
        assert f.id is not None
        assert f.tags == "[]"
        assert f.key == ""

    def test_memory_event_default(self):
        from nanobot.memory.db.schema import MemoryEvent, MemoryEventType
        e = MemoryEvent()
        assert e.event_type == MemoryEventType.conversation
        assert e.content == ""


# ═══════════════════════════════════════════════════════════
# FTS5 Trigger Tests
# ═══════════════════════════════════════════════════════════


class TestFTS5:
    def test_insert_syncs_to_fts5(self, conn):
        _insert_fact(conn, entity="Alice", key="role", value="engineer")
        rows = conn.execute(
            "SELECT * FROM facts_fts WHERE facts_fts MATCH 'engineer'"
        ).fetchall()
        assert len(rows) == 1

    def test_update_syncs_to_fts5(self, conn):
        fid = _insert_fact(conn, entity="Alice", key="role", value="engineer")
        conn.execute("UPDATE facts SET value = 'manager' WHERE id = ?", (fid,))
        conn.commit()

        old = conn.execute("SELECT * FROM facts_fts WHERE facts_fts MATCH 'engineer'").fetchall()
        new = conn.execute("SELECT * FROM facts_fts WHERE facts_fts MATCH 'manager'").fetchall()
        assert len(old) == 0
        assert len(new) == 1

    def test_delete_syncs_to_fts5(self, conn):
        fid = _insert_fact(conn, entity="Bob", key="role", value="designer")
        conn.execute("DELETE FROM facts WHERE id = ?", (fid,))
        conn.commit()

        rows = conn.execute("SELECT * FROM facts_fts WHERE facts_fts MATCH 'designer'").fetchall()
        assert len(rows) == 0

    def test_fts5_multi_column_search(self, conn):
        _insert_fact(conn, entity="BOOLE", key="tech_stack", value="Python + FastAPI",
                     category="project", rationale="modern web framework")
        # Search by entity
        assert len(conn.execute("SELECT * FROM facts_fts WHERE facts_fts MATCH 'BOOLE'").fetchall()) == 1
        # Search by value
        assert len(conn.execute("SELECT * FROM facts_fts WHERE facts_fts MATCH 'FastAPI'").fetchall()) == 1
        # Search by rationale
        assert len(conn.execute("SELECT * FROM facts_fts WHERE facts_fts MATCH 'modern'").fetchall()) == 1


# ═══════════════════════════════════════════════════════════
# Query Tests
# ═══════════════════════════════════════════════════════════


class TestQueries:
    def test_upsert_insert(self, conn):
        from nanobot.memory.db.queries import upsert_fact
        from nanobot.memory.db.schema import FactCategory
        fid, is_new = upsert_fact("Alice", "birthday", "June 3rd", conn,
                                  category=FactCategory.person)
        conn.commit()
        assert is_new is True
        row = conn.execute("SELECT value FROM facts WHERE id = ?", (fid,)).fetchone()
        assert row[0] == "June 3rd"

    def test_upsert_updates_existing(self, conn):
        from nanobot.memory.db.queries import upsert_fact
        from nanobot.memory.db.schema import FactCategory
        fid1, _ = upsert_fact("Alice", "birthday", "June 3rd", conn, category=FactCategory.person)
        conn.commit()
        fid2, is_new = upsert_fact("Alice", "birthday", "June 4th", conn, category=FactCategory.person)
        conn.commit()
        assert is_new is False
        assert fid2 == fid1
        row = conn.execute("SELECT value FROM facts WHERE id = ?", (fid1,)).fetchone()
        assert row[0] == "June 4th"

    def test_refresh_accessed_at(self, conn):
        from nanobot.memory.db.queries import refresh_accessed_at
        fid = _insert_fact(conn, entity="Alice", key="role", value="engineer",
                          accessed_at=datetime(2020, 1, 1, tzinfo=UTC))
        old = conn.execute("SELECT accessed_at FROM facts WHERE id = ?", (fid,)).fetchone()[0]
        refresh_accessed_at([fid], conn)
        new = conn.execute("SELECT accessed_at FROM facts WHERE id = ?", (fid,)).fetchone()[0]
        assert new > old

    def test_insert_memory_event(self, conn):
        from nanobot.memory.db.queries import insert_memory_event
        from nanobot.memory.db.schema import MemoryEventType
        eid = insert_memory_event(conn, agent_id="coder",
                                  event_type=MemoryEventType.conversation,
                                  content="Hello world")
        row = conn.execute("SELECT content FROM memory_events WHERE id = ?", (eid,)).fetchone()
        assert row[0] == "Hello world"

    def test_get_all_live_facts_excludes_expired(self, conn):
        from nanobot.memory.db.queries import get_all_live_facts
        # Permanent fact — always live
        _insert_fact(conn, entity="A", key="k1", value="v1", decay_tier="permanent")
        # Expired fact — accessed long ago with short TTL
        _insert_fact(conn, entity="B", key="k2", value="v2", decay_tier="session",
                     ttl_seconds=1, accessed_at=datetime(2020, 1, 1, tzinfo=UTC))
        facts = get_all_live_facts(conn)
        assert len(facts) == 1
        assert facts[0].entity == "A"

    def test_group_facts_by_category(self, conn):
        from nanobot.memory.db.queries import get_all_live_facts, group_facts_by_category
        _insert_fact(conn, entity="A", key="k1", value="v1", category="person")
        _insert_fact(conn, entity="B", key="k2", value="v2", category="project")
        facts = get_all_live_facts(conn)
        grouped = group_facts_by_category(facts)
        assert "person" in grouped
        assert "project" in grouped

    def test_get_entity_fact_counts(self, conn):
        from nanobot.memory.db.queries import get_all_live_facts, get_entity_fact_counts
        _insert_fact(conn, entity="Alice", key="k1", value="v1", category="person")
        _insert_fact(conn, entity="Alice", key="k2", value="v2", category="person")
        _insert_fact(conn, entity="Bob", key="k3", value="v3", category="person")
        facts = get_all_live_facts(conn)
        counts = get_entity_fact_counts(facts)
        assert counts[("person", "Alice")] == 2
        assert counts[("person", "Bob")] == 1

    def test_search_facts_like_fallback(self, conn):
        from nanobot.memory.db.queries import search_facts
        _insert_fact(conn, entity="Alice", key="birthday", value="June 3rd", category="person")
        results, strategy = search_facts(conn, "June")
        assert len(results) == 1
        assert strategy in ("FTS5", "LIKE")

    def test_search_facts_fts5(self, conn):
        from nanobot.memory.db.queries import search_facts
        _insert_fact(conn, entity="BOOLE", key="tech_stack", value="Python FastAPI", category="project")
        results, strategy = search_facts(conn, "FastAPI")
        assert len(results) == 1
        assert results[0].entity == "BOOLE"

    def test_search_facts_by_category_filter(self, conn):
        from nanobot.memory.db.queries import search_facts
        _insert_fact(conn, entity="A", key="k1", value="v1", category="person")
        _insert_fact(conn, entity="B", key="k2", value="v2", category="project")
        results, _ = search_facts(conn, "", category="person")
        assert len(results) == 1
        assert results[0].entity == "A"

    def test_search_facts_no_results(self, conn):
        from nanobot.memory.db.queries import search_facts
        results, strategy = search_facts(conn, "nonexistent")
        assert len(results) == 0
        assert strategy == "none"

    def test_get_db_stats(self, conn):
        from nanobot.memory.db.queries import get_db_stats
        _insert_fact(conn, entity="A", key="k1", value="v1")
        stats = get_db_stats(conn)
        assert stats["total_facts"] == 1
        assert stats["total_events"] == 0


# ═══════════════════════════════════════════════════════════
# Pruner Tests
# ═══════════════════════════════════════════════════════════


class TestPruner:
    def test_prune_expired_facts(self, conn):
        from nanobot.memory.pruner import prune_expired_facts
        # Expired: session tier, 1s TTL, accessed 2h ago
        _insert_fact(conn, entity="A", key="k1", value="v1", decay_tier="session",
                     ttl_seconds=1, accessed_at=datetime.now(UTC) - timedelta(hours=2))
        # Live: permanent
        _insert_fact(conn, entity="B", key="k2", value="v2", decay_tier="permanent")
        pruned = prune_expired_facts(conn)
        assert len(pruned) == 1
        assert pruned[0].entity == "A"
        # Verify deletion
        remaining = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        assert remaining == 1

    def test_prune_dry_run(self, conn):
        from nanobot.memory.pruner import prune_expired_facts
        _insert_fact(conn, entity="A", key="k1", value="v1", decay_tier="session",
                     ttl_seconds=1, accessed_at=datetime.now(UTC) - timedelta(hours=2))
        pruned = prune_expired_facts(conn, dry_run=True)
        assert len(pruned) == 1
        # Should NOT have deleted
        remaining = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        assert remaining == 1

    def test_prune_nothing_to_prune(self, conn):
        from nanobot.memory.pruner import prune_expired_facts
        _insert_fact(conn, entity="A", key="k1", value="v1", decay_tier="permanent")
        pruned = prune_expired_facts(conn)
        assert len(pruned) == 0

    def test_prune_preserves_permanent(self, conn):
        from nanobot.memory.pruner import prune_expired_facts
        _insert_fact(conn, entity="A", key="k1", value="v1", decay_tier="permanent",
                     ttl_seconds=None)
        pruned = prune_expired_facts(conn)
        assert len(pruned) == 0


# ═══════════════════════════════════════════════════════════
# Generator Tests
# ═══════════════════════════════════════════════════════════


class TestGenerator:
    def test_run_generation_empty_db(self, workspace, db_path, conn):
        from nanobot.memory.generator import run_generation
        result = run_generation(workspace, db_path=db_path)
        assert result.facts_rendered == 0
        assert result.index_files == 0

    def test_run_generation_creates_memory_md(self, workspace, db_path, conn):
        from nanobot.memory.generator import run_generation
        _insert_fact(conn, entity="Alice", key="role", value="engineer", category="person")
        result = run_generation(workspace, db_path=db_path)
        assert result.index_files == 1
        assert (workspace / "MEMORY.md").exists()
        content = (workspace / "MEMORY.md").read_text()
        assert "Alice" in content
        assert "Memory Index" in content

    def test_run_generation_creates_detail_files(self, workspace, db_path, conn):
        from nanobot.memory.generator import run_generation
        # Need ≥3 facts for detail file
        for i in range(4):
            _insert_fact(conn, entity="Alice", key=f"fact_{i}", value=f"value_{i}", category="person")
        result = run_generation(workspace, db_path=db_path)
        assert result.detail_files_written > 0
        assert (workspace / "memory" / "people" / "alice.md").exists()

    def test_run_generation_creates_project_detail(self, workspace, db_path, conn):
        from nanobot.memory.generator import run_generation
        for i in range(4):
            _insert_fact(conn, entity="BOOLE", key=f"fact_{i}", value=f"value_{i}", category="project")
        result = run_generation(workspace, db_path=db_path)
        assert (workspace / "memory" / "projects" / "boole.md").exists()

    def test_run_generation_creates_decisions(self, workspace, db_path, conn):
        from nanobot.memory.generator import run_generation
        _insert_fact(conn, entity="auth", key="approach", value="Use JWT tokens",
                     category="decision", rationale="Stateless, scalable")
        result = run_generation(workspace, db_path=db_path)
        assert result.index_files == 1
        decisions_dir = workspace / "memory" / "decisions"
        assert decisions_dir.exists()
        assert len(list(decisions_dir.glob("*.md"))) > 0

    def test_run_generation_creates_sprint_context(self, workspace, db_path, conn):
        from nanobot.memory.generator import run_generation
        _insert_fact(conn, entity="sprint", key="goal", value="Ship auth",
                     category="task", decay_tier="active", ttl_seconds=86400 * 14)
        result = run_generation(workspace, db_path=db_path)
        sprint_file = workspace / "memory" / "context" / "current-sprint.md"
        assert sprint_file.exists()
        assert "Ship auth" in sprint_file.read_text()

    def test_slugify(self):
        from nanobot.memory.generator import _slugify
        assert _slugify("Linda Chen") == "linda-chen"
        assert _slugify("BOOLE (v2)") == "boole-v2"
        assert _slugify("") == "unnamed"

    def test_detail_path(self):
        from nanobot.memory.generator import _detail_path
        assert _detail_path("person", "Alice") == "memory/people/alice.md"
        assert _detail_path("project", "BOOLE") == "memory/projects/boole.md"

    def test_index_has_active_context_section(self, workspace, db_path, conn):
        from nanobot.memory.generator import run_generation
        _insert_fact(conn, entity="A", key="k", value="v", category="person")
        run_generation(workspace, db_path=db_path)
        content = (workspace / "MEMORY.md").read_text()
        assert "Active Context" in content

    def test_index_has_drill_down_rules(self, workspace, db_path, conn):
        from nanobot.memory.generator import run_generation
        _insert_fact(conn, entity="A", key="k", value="v", category="person")
        run_generation(workspace, db_path=db_path)
        content = (workspace / "MEMORY.md").read_text()
        assert "Drill-Down Rules" in content


# ═══════════════════════════════════════════════════════════
# Classifier Tests
# ═══════════════════════════════════════════════════════════


class TestClassifier:
    def test_compute_ttl_permanent(self):
        from nanobot.memory.classifier import compute_ttl_seconds
        from nanobot.memory.db.schema import DecayTier
        assert compute_ttl_seconds(DecayTier.permanent) is None

    def test_compute_ttl_stable(self):
        from nanobot.memory.classifier import compute_ttl_seconds
        from nanobot.memory.db.schema import DecayTier
        ttl = compute_ttl_seconds(DecayTier.stable)
        assert ttl == 90 * 86400

    def test_compute_ttl_active(self):
        from nanobot.memory.classifier import compute_ttl_seconds
        from nanobot.memory.db.schema import DecayTier
        ttl = compute_ttl_seconds(DecayTier.active)
        assert ttl == 14 * 86400

    def test_compute_ttl_session(self):
        from nanobot.memory.classifier import compute_ttl_seconds
        from nanobot.memory.db.schema import DecayTier
        ttl = compute_ttl_seconds(DecayTier.session)
        assert ttl == 24 * 3600

    def test_compute_ttl_checkpoint(self):
        from nanobot.memory.classifier import compute_ttl_seconds
        from nanobot.memory.db.schema import DecayTier
        ttl = compute_ttl_seconds(DecayTier.checkpoint)
        assert ttl == 4 * 3600

    def test_compute_ttl_custom_config(self):
        from nanobot.memory.classifier import compute_ttl_seconds
        from nanobot.memory.db.schema import DecayTier
        cfg = DecayTTLConfig(stable_ttl_days=30, active_ttl_days=7)
        assert compute_ttl_seconds(DecayTier.stable, cfg) == 30 * 86400
        assert compute_ttl_seconds(DecayTier.active, cfg) == 7 * 86400

    def test_parse_tier_valid(self):
        from nanobot.memory.classifier import _parse_tier
        from nanobot.memory.db.schema import DecayTier
        assert _parse_tier("permanent") == DecayTier.permanent
        assert _parse_tier("  ACTIVE  ") == DecayTier.active
        assert _parse_tier('"session"') == DecayTier.session

    def test_parse_tier_invalid(self):
        from nanobot.memory.classifier import _parse_tier
        assert _parse_tier("garbage") is None
        assert _parse_tier("") is None


# ═══════════════════════════════════════════════════════════
# RecallTool Tests
# ═══════════════════════════════════════════════════════════


class TestRecallTool:
    def test_recall_tool_properties(self):
        from nanobot.agent.tools.recall import RecallTool
        tool = RecallTool()
        assert tool.name == "recall"
        assert "memory" in tool.description.lower()
        params = tool.parameters
        assert "query" in params["properties"]
        assert params["required"] == ["query"]

    @pytest.mark.asyncio
    async def test_recall_tool_no_results(self, db_path, conn):
        from nanobot.agent.tools.recall import RecallTool
        tool = RecallTool(db_path=db_path)
        result = await tool.execute(query="nonexistent")
        assert "No matching facts" in result

    @pytest.mark.asyncio
    async def test_recall_tool_finds_facts(self, db_path, conn):
        from nanobot.agent.tools.recall import RecallTool
        _insert_fact(conn, entity="Alice", key="role", value="senior engineer", category="person")
        tool = RecallTool(db_path=db_path)
        result = await tool.execute(query="Alice")
        assert "Alice" in result
        assert "senior engineer" in result
        assert "Recall Results" in result

    @pytest.mark.asyncio
    async def test_recall_tool_with_category_filter(self, db_path, conn):
        from nanobot.agent.tools.recall import RecallTool
        _insert_fact(conn, entity="A", key="k1", value="v1", category="person")
        _insert_fact(conn, entity="B", key="k2", value="v2", category="project")
        tool = RecallTool(db_path=db_path)
        result = await tool.execute(query="", category="person")
        assert "A" in result
        assert "B" not in result


# ═══════════════════════════════════════════════════════════
# Integration Tests
# ═══════════════════════════════════════════════════════════


class TestEndToEnd:
    def test_full_lifecycle(self, workspace, db_path, conn):
        """Insert facts → generate → verify files → prune expired."""
        from nanobot.memory.generator import run_generation
        from nanobot.memory.pruner import prune_expired_facts

        # Insert a mix of permanent and expiring facts
        for i in range(5):
            _insert_fact(conn, entity="Alice", key=f"fact_{i}", value=f"about Alice #{i}",
                        category="person", decay_tier="permanent")
        _insert_fact(conn, entity="sprint", key="current_task", value="Build auth module",
                     category="task", decay_tier="active", ttl_seconds=86400 * 14)
        _insert_fact(conn, entity="debug", key="error_log", value="NullPointerException in auth.py",
                     category="task", decay_tier="session", ttl_seconds=1,
                     accessed_at=datetime.now(UTC) - timedelta(hours=2))

        # Generate
        result = run_generation(workspace, db_path=db_path)
        assert result.index_files == 1
        assert result.detail_files_written > 0
        assert (workspace / "MEMORY.md").exists()
        assert (workspace / "memory" / "people" / "alice.md").exists()

        # Prune expired
        pruned = prune_expired_facts(conn)
        assert len(pruned) == 1  # The session fact expired

        # Verify remaining
        remaining = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        assert remaining == 6  # 5 permanent Alice + 1 active sprint

    def test_dedup_across_multiple_inserts(self, conn):
        """Ensure upsert deduplicates correctly."""
        from nanobot.memory.db.queries import upsert_fact
        from nanobot.memory.db.schema import FactCategory

        # First insert
        fid1, new1 = upsert_fact("Alice", "role", "engineer", conn, category=FactCategory.person)
        conn.commit()
        assert new1 is True

        # Second insert with same entity+key — should update
        fid2, new2 = upsert_fact("Alice", "role", "senior engineer", conn, category=FactCategory.person)
        conn.commit()
        assert new2 is False
        assert fid2 == fid1

        # Different key — should be new
        fid3, new3 = upsert_fact("Alice", "department", "Platform", conn, category=FactCategory.person)
        conn.commit()
        assert new3 is True
        assert fid3 != fid1

        total = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        assert total == 2

    def test_ttl_refresh_keeps_facts_alive(self, conn):
        """Facts that are accessed should survive pruning."""
        from nanobot.memory.db.queries import refresh_accessed_at
        from nanobot.memory.pruner import prune_expired_facts

        # Create a session fact with short TTL
        fid = _insert_fact(conn, entity="debug", key="state", value="investigating",
                          category="task", decay_tier="session", ttl_seconds=86400,
                          accessed_at=datetime.now(UTC) - timedelta(hours=12))

        # Refresh access — should reset TTL clock
        refresh_accessed_at([fid], conn)

        # Should NOT be pruned now
        pruned = prune_expired_facts(conn)
        assert len(pruned) == 0
