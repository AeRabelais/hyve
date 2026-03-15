"""RecallTool — lets agents query the distilled memory store on demand.

Multi-strategy search: FTS5 for structured lookups, LIKE fallback for
fuzzy queries. Automatically refreshes accessed_at for returned facts
to keep actively-queried knowledge alive.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool

if TYPE_CHECKING:
    from pathlib import Path


class RecallTool(Tool):
    """Tool for agents to search their distilled memory (facts store).

    Searches via FTS5 full-text search first, falls back to LIKE
    substring matching. Refreshes TTL on every returned fact.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path

    @property
    def name(self) -> str:
        return "recall"

    @property
    def description(self) -> str:
        return (
            "Search your long-term memory for facts about people, projects, "
            "decisions, conventions, and preferences. Use this BEFORE answering "
            "questions about prior work, past decisions, or people you've interacted with. "
            "Returns structured facts with category, entity, key, value, and rationale."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search terms (e.g. 'authentication decision', person name, project name)",
                },
                "category": {
                    "type": "string",
                    "enum": ["person", "project", "decision", "convention", "preference", "task"],
                    "description": "Optional: filter by fact category",
                },
                "entity": {
                    "type": "string",
                    "description": "Optional: filter by entity name (exact match)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results to return (default: 10)",
                },
            },
            "required": ["query"],
        }

    async def execute(
        self,
        query: str,
        category: str | None = None,
        entity: str | None = None,
        limit: int = 10,
        **kwargs: Any,
    ) -> str:
        """Search distilled memory and return formatted results."""
        try:
            from nanobot.memory.db.connection import get_engine
            from nanobot.memory.db.queries import refresh_accessed_at, search_facts

            conn = get_engine(self._db_path)
            results, strategy = search_facts(
                conn, query, category=category, entity=entity, limit=limit,
            )

            if not results:
                return f"No matching facts found for: {query}"

            # Refresh TTL for returned facts
            fact_ids = [f.id for f in results]
            refresh_accessed_at(fact_ids, conn)

            # Format as markdown table
            lines = [
                f"## Recall Results ({len(results)} match{'es' if len(results) != 1 else ''}"
                f' for "{query}") — strategy: {strategy}',
                "",
                "| # | Category | Entity | Key | Value | Rationale | Tier |",
                "|---|----------|--------|-----|-------|-----------|------|",
            ]

            for i, f in enumerate(results, 1):
                value = f.value[:60] + "…" if len(f.value) > 60 else f.value
                rationale = (f.rationale[:60] + "…" if f.rationale and len(f.rationale) > 60
                             else f.rationale or "—")
                cat = f.category.value if hasattr(f.category, "value") else str(f.category)
                tier = f.decay_tier.value if hasattr(f.decay_tier, "value") else str(f.decay_tier)
                lines.append(
                    f"| {i} | {cat} | {f.entity or '—'} | {f.key} "
                    f"| {value} | {rationale} | {tier} |"
                )

            lines.append(f"\n_TTL refreshed for {len(results)} fact(s)_")
            return "\n".join(lines)

        except Exception as e:
            return f"Error searching memory: {e}"
