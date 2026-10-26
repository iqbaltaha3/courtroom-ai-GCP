"""
Case SQLite database manager.
Handles pages table, structured extraction tables, state, and final artifacts.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from app.services.run_context import check_cancelled, current_run


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open the requested case database; never silently select a shared fallback."""
    context = current_run.get()
    path = db_path or (context.db_path if context else None)
    if path is None:
        raise ValueError("Provide a case database path or an active RunContext")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Fixed table definitions are shared by initialization and read-only schema inspection.
SCHEMA = [
    """CREATE TABLE IF NOT EXISTS pages ( id INTEGER PRIMARY KEY AUTOINCREMENT, doc_type TEXT NOT NULL,
    doc_name TEXT NOT NULL, page_number INTEGER NOT NULL, text TEXT NOT NULL, source_hash TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(doc_type, doc_name, page_number) )""",
    """CREATE TABLE IF NOT EXISTS laws ( id INTEGER PRIMARY KEY AUTOINCREMENT, act TEXT, section TEXT,
    title TEXT, relevance TEXT, page_refs TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP )""",
    """CREATE TABLE IF NOT EXISTS witnesses ( id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, role TEXT,
    statement_summary TEXT, page_refs TEXT, reliability_notes TEXT, created_at TEXT DEFAULT
    CURRENT_TIMESTAMP )""",
    """CREATE TABLE IF NOT EXISTS evidence ( id INTEGER PRIMARY KEY AUTOINCREMENT, description TEXT,
    evidence_type TEXT, page_refs TEXT, relevance TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP )""",
    """CREATE TABLE IF NOT EXISTS entities ( id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, entity_type
    TEXT, role TEXT, page_refs TEXT, notes TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP )""",
    """CREATE TABLE IF NOT EXISTS timeline ( id INTEGER PRIMARY KEY AUTOINCREMENT, event_date TEXT,
    event_description TEXT, page_refs TEXT, sequence INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS case_meta ( id INTEGER PRIMARY KEY CHECK (id = 1), meta_json TEXT NOT
    NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP )""",
    """CREATE TABLE IF NOT EXISTS reports ( id INTEGER PRIMARY KEY AUTOINCREMENT, report_type TEXT NOT
    NULL, content TEXT NOT NULL, structured_json TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP )""",
    """CREATE TABLE IF NOT EXISTS artifacts ( key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at
    TEXT DEFAULT CURRENT_TIMESTAMP )""",
]


def init_schema(conn=None):
    """Create missing tables without replacing any saved case data."""
    owned = conn is None
    conn = conn or get_connection()
    try:
        for statement in SCHEMA:
            conn.execute(statement)
        conn.commit()
    finally:
        if owned:
            conn.close()


def clear_case_data(conn: Optional[sqlite3.Connection] = None) -> None:
    """Clear the current case tables before starting a fresh run."""
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    tables = [
        "pages",
        "laws",
        "witnesses",
        "evidence",
        "entities",
        "timeline",
        "case_meta",
        "reports",
        "artifacts",
    ]
    for t in tables:
        conn.execute(f"DELETE FROM {t}")
    conn.commit()
    if close:
        conn.close()


def bulk_insert_pages(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    """Save page text and source identifiers using parameterized inserts."""
    conn.executemany(
        """INSERT OR REPLACE INTO pages (doc_type, doc_name, page_number, text, source_hash)
           VALUES (:doc_type, :doc_name, :page_number, :text, :source_hash)""",
        rows,
    )
    conn.commit()


def get_all_pages(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return the original pages in stable document and page order."""
    cur = conn.execute("SELECT * FROM pages ORDER BY doc_type, doc_name, page_number")
    return [dict(r) for r in cur.fetchall()]


# Only these extracted tables may be written through save_structured.
EXTRACTED_COLUMNS = {
    "laws": "act, section, title, relevance, page_refs",
    "witnesses": "name, role, statement_summary, page_refs, reliability_notes",
    "evidence": "description, evidence_type, page_refs, relevance",
    "entities": "name, entity_type, role, page_refs, notes",
    "timeline": "event_date, event_description, page_refs, sequence",
}


def save_structured(conn, table, rows):
    """Insert validated model fields using a fixed table/column allowlist."""
    columns = EXTRACTED_COLUMNS[table]
    parameters = ", ".join(":" + column for column in columns.split(", "))
    conn.executemany(f"INSERT INTO {table} ({columns}) VALUES ({parameters})", rows)
    conn.commit()


def save_case_meta(conn: sqlite3.Connection, meta: dict[str, Any]) -> None:
    """Replace the single case-summary metadata record."""
    conn.execute("DELETE FROM case_meta")
    conn.execute(
        "INSERT INTO case_meta (id, meta_json) VALUES (1, ?)",
        (json.dumps(meta, ensure_ascii=False),),
    )
    conn.commit()


def save_report(
    conn: sqlite3.Connection, report_type: str, content: str, structured: Optional[dict] = None
) -> None:
    """Save a stage report unless the current run has been cancelled."""
    check_cancelled()
    conn.execute(
        """INSERT INTO reports (report_type, content, structured_json)
           VALUES (?, ?, ?)""",
        (report_type, content, json.dumps(structured, ensure_ascii=False) if structured else None),
    )
    conn.commit()


def get_report(conn: sqlite3.Connection, report_type: str) -> Optional[str]:
    """Read the latest report for one pipeline stage."""
    cur = conn.execute(
        "SELECT content FROM reports WHERE report_type = ? ORDER BY id DESC LIMIT 1",
        (report_type,),
    )
    row = cur.fetchone()
    return row["content"] if row else None


def get_all_reports(conn: sqlite3.Connection) -> dict[str, str]:
    """Return the most recent report content for each stage."""
    cur = conn.execute("SELECT report_type, content FROM reports ORDER BY id")
    return {r["report_type"]: r["content"] for r in cur.fetchall()}


def save_artifact(conn: sqlite3.Connection, key: str, value: Any) -> None:
    """Insert or replace a named JSON artifact such as a review or source list."""
    conn.execute(
        """INSERT INTO artifacts (key, value_json, updated_at)
           VALUES (?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=CURRENT_TIMESTAMP""",
        (key, json.dumps(value, ensure_ascii=False)),
    )
    conn.commit()


def get_artifact(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    """Read a JSON artifact, using the default if it is absent or malformed."""
    cur = conn.execute("SELECT value_json FROM artifacts WHERE key = ?", (key,))
    row = cur.fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value_json"])
    except json.JSONDecodeError:
        return default


def get_schema_description() -> str:
    """Describe the same tables used to initialize case databases."""
    return "SQLite case schema:\n" + "\n".join(SCHEMA)


def execute_readonly(sql: str, params: tuple = (), db_path: Optional[Path] = None) -> list[dict[str, Any]]:
    """Execute a read-only SQL query. Raises on write attempts."""
    forbidden = ("insert", "update", "delete", "drop", "alter", "create", "attach", "detach", "pragma")
    lower = sql.strip().lower()
    if any(lower.startswith(f) or f" {f} " in lower for f in forbidden):
        raise ValueError("Only SELECT queries are allowed.")
    conn = get_connection(db_path)
    conn.execute("PRAGMA query_only = ON")
    try:
        cur = conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
