"""Hierarchical Core Knowledge generator (Layer 4).

Queries the facts table and produces Obsidian-compatible markdown files:

    {workspace}/
      MEMORY.md                          ← index (always loaded by agent)
      memory/
        people/{slug}.md                 ← person detail files
        projects/{slug}.md               ← project detail files
        decisions/{YYYY-MM}.md           ← monthly decision log
        context/current-sprint.md        ← active-tier facts

Design:
  * No LLM calls — pure query + template, fast and free
  * Idempotent — each run overwrites; stale files are cleaned
  * TTL-aware — only live (non-expired) facts are included
  * Refreshes accessed_at for rendered facts to keep them alive
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from nanobot.memory.db.connection import get_engine
from nanobot.memory.db.queries import (
    get_all_live_facts,
    get_entity_fact_counts,
    get_top_accessed_entities,
    group_facts_by_category,
    refresh_accessed_at,
)
from nanobot.memory.db.schema import DecayTier, Fact, FactCategory


# ── Constants ──────────────────────────────────────────────

_DETAIL_CATEGORIES = frozenset({"person", "project"})
_MIN_FACTS_FOR_DETAIL = 3


@dataclass
class GenerationResult:
    workspaces_written: int = 0
    index_files: int = 0
    detail_files_written: int = 0
    detail_files_cleaned: int = 0
    facts_rendered: int = 0
    archived_entities: int = 0
    errors: list[str] = field(default_factory=list)


# ── Helpers ────────────────────────────────────────────────


def _estimate_tokens(text: str) -> int:
    return len(text) // 4


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return slug or "unnamed"


def _detail_path(category: str, entity: str) -> str:
    slug = _slugify(entity)
    if category == "person":
        return f"memory/people/{slug}.md"
    elif category == "project":
        return f"memory/projects/{slug}.md"
    return f"memory/{category}/{slug}.md"


def _format_date(dt: datetime | str | None) -> str:
    if dt is None:
        return "—"
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            return dt[:10] if len(dt) >= 10 else dt
    return dt.strftime("%Y-%m-%d")


def _parse_tags(tags_json: str) -> list[str]:
    try:
        parsed = json.loads(tags_json)
        return [str(t) for t in parsed] if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


# ── Detail file builders ──────────────────────────────────


def _build_person_detail(entity: str, facts: list[Fact]) -> str:
    lines = [f"# {entity}", f"Last updated: {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}", "",
             "## Facts", "| Key | Value | Last Verified |", "|-----|-------|---------------|"]
    for f in sorted(facts, key=lambda x: x.key):
        lines.append(f"| {f.key} | {f.value} | {_format_date(f.updated_at)} |")
    tags = set()
    for f in facts:
        tags.update(_parse_tags(f.tags))
    if tags:
        lines.extend(["", "## Tags", ", ".join(sorted(tags))])
    lines.append("")
    return "\n".join(lines)


def _build_project_detail(entity: str, facts: list[Fact]) -> str:
    lines = [f"# {entity}", f"Last updated: {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}", ""]
    key_map = {f.key.lower(): f.value for f in facts}
    if "status" in key_map:
        lines.append(f"**Status:** {key_map['status']}  ")
    if "tech_stack" in key_map:
        lines.append(f"**Stack:** {key_map['tech_stack']}  ")
    if key_map.get("status") or key_map.get("tech_stack"):
        lines.append("")
    lines.extend(["## Facts", "| Key | Value | Last Verified |", "|-----|-------|---------------|"])
    for f in sorted(facts, key=lambda x: x.key):
        lines.append(f"| {f.key} | {f.value} | {_format_date(f.updated_at)} |")
    lines.append("")
    return "\n".join(lines)


def _build_decisions_detail(year_month: str, facts: list[Fact]) -> str:
    lines = [f"# Decisions & Conventions — {year_month}",
             f"Last updated: {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}", "",
             "| Type | Entity | Decision / Convention | Rationale | Date |",
             "|------|--------|----------------------|-----------|------|"]
    for f in sorted(facts, key=lambda x: str(x.created_at)):
        cat = f.category.value if isinstance(f.category, FactCategory) else str(f.category)
        lines.append(f"| {cat} | {f.entity or '—'} | {f.value} | {f.rationale or '—'} | {_format_date(f.created_at)} |")
    lines.append("")
    return "\n".join(lines)


def _build_current_sprint(facts: list[Fact]) -> str:
    lines = ["# Current Sprint Context",
             f"Last updated: {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}", "",
             "Active-tier facts representing current work context.", "",
             "| Category | Entity | Key | Value |",
             "|----------|--------|-----|-------|"]
    for f in sorted(facts, key=lambda x: (str(x.category), x.entity or "", x.key)):
        cat = f.category.value if isinstance(f.category, FactCategory) else str(f.category)
        lines.append(f"| {cat} | {f.entity or '—'} | {f.key} | {f.value} |")
    lines.append("")
    return "\n".join(lines)


# ── Index builder ──────────────────────────────────────────


def _build_index(
    facts: list[Fact],
    entities_with_detail: set[str],
    active_context_files: list[str],
    *,
    max_decisions: int = 10,
) -> str:
    grouped = group_facts_by_category(facts)
    parts = [f"# Memory Index\nLast updated: {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"]

    # People
    person_by_entity: dict[str, list[Fact]] = defaultdict(list)
    for f in grouped.get("person", []):
        if f.entity:
            person_by_entity[f.entity].append(f)
    if person_by_entity:
        lines = ["## People", "| Name | Key Facts | Detail |", "|------|-----------|--------|"]
        for entity in sorted(person_by_entity):
            efacts = person_by_entity[entity]
            summary = ", ".join(f"{f.key}: {f.value[:40]}" for f in sorted(efacts, key=lambda x: x.key)[:3])
            detail = _detail_path("person", entity) if entity in entities_with_detail else "—"
            lines.append(f"| {entity} | {summary} | {detail} |")
        parts.append("\n".join(lines) + "\n")

    # Projects
    project_by_entity: dict[str, list[Fact]] = defaultdict(list)
    for f in grouped.get("project", []):
        if f.entity:
            project_by_entity[f.entity].append(f)
    if project_by_entity:
        lines = ["## Projects", "| Project | Status | Stack | Detail |", "|---------|--------|-------|--------|"]
        for entity in sorted(project_by_entity):
            efacts = project_by_entity[entity]
            key_map = {f.key.lower(): f.value for f in efacts}
            detail = _detail_path("project", entity) if entity in entities_with_detail else "—"
            lines.append(f"| {entity} | {key_map.get('status', '—')} | {key_map.get('tech_stack', '—')} | {detail} |")
        parts.append("\n".join(lines) + "\n")

    # Recent Decisions
    combined_decisions = grouped.get("decision", []) + grouped.get("convention", [])
    if combined_decisions:
        combined_decisions.sort(key=lambda f: str(f.created_at), reverse=True)
        display = combined_decisions[:max_decisions]
        lines = ["## Recent Decisions", "| Decision | Rationale | Date |", "|----------|-----------|------|"]
        for f in display:
            lines.append(f"| {f.value} | {f.rationale or '—'} | {_format_date(f.created_at)} |")
        parts.append("\n".join(lines) + "\n")

    # Active Context
    lines = ["## Active Context (always load these)"]
    if active_context_files:
        for path in active_context_files:
            lines.append(f"- {path}")
    else:
        lines.append("- _(no active context files yet)_")
    parts.append("\n".join(lines) + "\n")

    # Drill-down rules
    parts.append("## Drill-Down Rules\n- Mention a person by name → load memory/people/{name}.md\n"
                 "- Mention a project by name → load memory/projects/{name}.md\n"
                 "- Ask about a past decision → load memory/decisions/{month}.md\n")

    return "\n".join(parts)


# ── Active context rotation ────────────────────────────────


def _compute_active_context(
    facts: list[Fact],
    active_context_slots: int,
    entities_with_detail: set[str],
) -> list[str]:
    context_files: list[str] = []
    has_active = any(
        (f.decay_tier == DecayTier.active if isinstance(f.decay_tier, DecayTier)
         else str(f.decay_tier) == "active")
        for f in facts
    )
    if has_active:
        context_files.append("memory/context/current-sprint.md")

    remaining = active_context_slots - len(context_files)
    if remaining > 0:
        top = get_top_accessed_entities(facts, n=remaining + 5)
        for cat, entity, _ in top:
            if len(context_files) >= active_context_slots:
                break
            if entity in entities_with_detail:
                path = _detail_path(cat, entity)
                if path not in context_files:
                    context_files.append(path)
    return context_files


# ── Detail file orchestration ──────────────────────────────


def _generate_detail_files(
    workspace: Path,
    facts: list[Fact],
    entities_with_detail: set[str],
) -> tuple[int, int]:
    grouped = group_facts_by_category(facts)
    written = 0
    cleaned = 0

    # People
    people_dir = workspace / "memory" / "people"
    people_dir.mkdir(parents=True, exist_ok=True)
    person_by_entity: dict[str, list[Fact]] = defaultdict(list)
    for f in grouped.get("person", []):
        if f.entity:
            person_by_entity[f.entity].append(f)

    current_slugs: set[str] = set()
    for entity, efacts in person_by_entity.items():
        if entity not in entities_with_detail:
            continue
        slug = _slugify(entity)
        current_slugs.add(slug)
        (people_dir / f"{slug}.md").write_text(_build_person_detail(entity, efacts), encoding="utf-8")
        written += 1
    for existing in people_dir.glob("*.md"):
        if existing.stem not in current_slugs:
            existing.unlink()
            cleaned += 1

    # Projects
    projects_dir = workspace / "memory" / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    project_by_entity: dict[str, list[Fact]] = defaultdict(list)
    for f in grouped.get("project", []):
        if f.entity:
            project_by_entity[f.entity].append(f)

    current_slugs = set()
    for entity, efacts in project_by_entity.items():
        if entity not in entities_with_detail:
            continue
        slug = _slugify(entity)
        current_slugs.add(slug)
        (projects_dir / f"{slug}.md").write_text(_build_project_detail(entity, efacts), encoding="utf-8")
        written += 1
    for existing in projects_dir.glob("*.md"):
        if existing.stem not in current_slugs:
            existing.unlink()
            cleaned += 1

    # Monthly decisions
    decisions_dir = workspace / "memory" / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    decision_facts = grouped.get("decision", []) + grouped.get("convention", [])
    by_month: dict[str, list[Fact]] = defaultdict(list)
    for f in decision_facts:
        created = f.created_at
        if isinstance(created, str):
            try:
                created = datetime.fromisoformat(created)
            except ValueError:
                continue
        by_month[created.strftime("%Y-%m")].append(f)

    current_months: set[str] = set()
    for ym, mfacts in by_month.items():
        current_months.add(ym)
        (decisions_dir / f"{ym}.md").write_text(_build_decisions_detail(ym, mfacts), encoding="utf-8")
        written += 1
    for existing in decisions_dir.glob("*.md"):
        if existing.stem not in current_months:
            existing.unlink()
            cleaned += 1

    # Current sprint
    context_dir = workspace / "memory" / "context"
    context_dir.mkdir(parents=True, exist_ok=True)
    active_facts = [f for f in facts if (f.decay_tier == DecayTier.active if isinstance(f.decay_tier, DecayTier) else str(f.decay_tier) == "active")]
    if active_facts:
        (context_dir / "current-sprint.md").write_text(_build_current_sprint(active_facts), encoding="utf-8")
        written += 1
    elif (context_dir / "current-sprint.md").exists():
        (context_dir / "current-sprint.md").unlink()
        cleaned += 1

    return written, cleaned


# ── Main entry point ───────────────────────────────────────


def run_generation(
    workspace: Path,
    db_path: Path | None = None,
    max_tokens: int = 3000,
    active_context_slots: int = 3,
    agent_id: str | None = None,
) -> GenerationResult:
    """Run a full knowledge generation cycle for a workspace.

    1. Query all live facts
    2. Determine entities with enough facts for detail files (≥3)
    3. Compute active context rotation
    4. Generate MEMORY.md index with token cap
    5. Generate detail files
    6. Refresh accessed_at for all rendered facts
    """
    result = GenerationResult()
    conn = get_engine(db_path)

    all_facts = get_all_live_facts(conn, agent_id=agent_id)
    if not all_facts:
        logger.info("Generation: no live facts in database")
        return result

    result.facts_rendered = len(all_facts)

    # Entities qualifying for detail files
    counts = get_entity_fact_counts(all_facts)
    entities_with_detail: set[str] = set()
    for (cat, entity), count in counts.items():
        if cat in _DETAIL_CATEGORIES and entity != "__none__" and count >= _MIN_FACTS_FOR_DETAIL:
            entities_with_detail.add(entity)

    # Active context rotation
    active_context_files = _compute_active_context(all_facts, active_context_slots, entities_with_detail)

    # Build index
    index_content = _build_index(all_facts, entities_with_detail, active_context_files)

    # Token cap enforcement (simple: reduce decisions if over)
    if _estimate_tokens(index_content) > max_tokens:
        index_content = _build_index(all_facts, entities_with_detail, active_context_files, max_decisions=5)

    try:
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "MEMORY.md").write_text(index_content, encoding="utf-8")
        result.index_files = 1

        w, c = _generate_detail_files(workspace, all_facts, entities_with_detail)
        result.detail_files_written = w
        result.detail_files_cleaned = c
        result.workspaces_written = 1

        # Refresh accessed_at
        fact_ids = [f.id for f in all_facts]
        refresh_accessed_at(fact_ids, conn)

        logger.info("Generated files for workspace: index + {} detail, {} cleaned", w, c)

    except Exception as exc:
        error_msg = f"Generation failed: {exc}"
        logger.error(error_msg)
        result.errors.append(error_msg)

    return result
