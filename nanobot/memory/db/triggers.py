"""FTS5 virtual table and synchronisation triggers for the facts table.

The virtual table mirrors the searchable columns of ``facts`` and is kept
in sync via AFTER INSERT / UPDATE / DELETE triggers.

FTS5's ``content_rowid`` uses SQLite's implicit integer ``rowid`` which
exists on every regular table regardless of the declared primary key type.
"""

# ── FTS5 + Trigger SQL ────────────────────────────────────

FTS5_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    category,
    entity,
    key,
    value,
    rationale,
    content='facts',
    content_rowid='rowid'
);

-- Trigger: keep FTS5 in sync after INSERT
CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, category, entity, key, value, rationale)
    VALUES (new.rowid, new.category, new.entity, new.key, new.value, new.rationale);
END;

-- Trigger: keep FTS5 in sync after DELETE
CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, category, entity, key, value, rationale)
    VALUES ('delete', old.rowid, old.category, old.entity, old.key, old.value, old.rationale);
END;

-- Trigger: keep FTS5 in sync after UPDATE (delete old + insert new)
CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, category, entity, key, value, rationale)
    VALUES ('delete', old.rowid, old.category, old.entity, old.key, old.value, old.rationale);
    INSERT INTO facts_fts(rowid, category, entity, key, value, rationale)
    VALUES (new.rowid, new.category, new.entity, new.key, new.value, new.rationale);
END;
"""
