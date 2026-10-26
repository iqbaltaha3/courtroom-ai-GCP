"""Persistent Case Library — index of all cases with metadata for archive UI."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from app.config import DATA_DIR, STORAGE_DIR

LIBRARY_DB = DATA_DIR / "library.db"


def _now() -> str:
    """Return the current UTC timestamp for saved records."""
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    """Open the case-library index with named result columns."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(LIBRARY_DB))
    connection.row_factory = sqlite3.Row
    return connection


def init_library() -> None:
    """Create the case-library table if it is missing."""
    connection = _connect()
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS cases (
            case_id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT 'Untitled case',
            status TEXT NOT NULL DEFAULT 'created',
            selected_judge TEXT,
            verdict TEXT,
            confidence TEXT,
            narrative_preview TEXT,
            tags TEXT DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            notes TEXT DEFAULT ''
        )
        """
    )
    connection.commit()
    connection.close()


def upsert_case(
    case_id: str,
    *,
    title: Optional[str] = None,
    status: Optional[str] = None,
    selected_judge: Optional[str] = None,
    verdict: Optional[str] = None,
    confidence: Optional[str] = None,
    narrative_preview: Optional[str] = None,
    tags: Optional[list[str]] = None,
    notes: Optional[str] = None,
    completed: bool = False,
) -> dict[str, Any]:
    """Create a library entry or update only the supplied metadata fields."""
    init_library()
    connection = _connect()
    row = connection.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
    now = _now()
    if row is None:
        connection.execute(
            """
            INSERT INTO cases (
                case_id, title, status, selected_judge, verdict, confidence,
                narrative_preview, tags, created_at, updated_at, completed_at, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                case_id,
                title or "Untitled case",
                status or "created",
                selected_judge,
                verdict,
                confidence,
                (narrative_preview or "")[:500],
                json.dumps(tags or []),
                now,
                now,
                now if completed else None,
                notes or "",
            ),
        )
    else:
        # Omitted fields keep their existing values; every SQL value is parameterized.
        updates = {
            "title": title,
            "status": status,
            "selected_judge": selected_judge,
            "verdict": verdict,
            "confidence": confidence,
            "narrative_preview": narrative_preview,
            "tags": tags,
            "notes": notes,
        }
        updates = {key: value for key, value in updates.items() if value is not None}
        if "narrative_preview" in updates:
            updates["narrative_preview"] = updates["narrative_preview"][:500]
        if "tags" in updates:
            updates["tags"] = json.dumps(updates["tags"])
        updates["updated_at"] = now
        if completed:
            updates["completed_at"] = now
        fields = ", ".join(f"{key} = ?" for key in updates)
        connection.execute(f"UPDATE cases SET {fields} WHERE case_id = ?", [*updates.values(), case_id])
    connection.commit()
    out = dict(connection.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone())
    connection.close()
    return _decode(out)


def _decode(row):
    """Convert SQLite rows and their JSON tags into ordinary API dictionaries."""
    result = dict(row)
    try:
        result["tags"] = json.loads(result.get("tags") or "[]")
    except json.JSONDecodeError:
        result["tags"] = []
    return result


def list_cases(
    status: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Filter saved cases by status and search text, returning newest entries first."""
    init_library()
    connection = _connect()
    sql = "SELECT * FROM cases WHERE 1=1"
    params: list[Any] = []
    if status:
        sql += " AND status = ?"
        params.append(status)
    if q:
        sql += " AND (title LIKE ? OR narrative_preview LIKE ? OR verdict LIKE ? OR notes LIKE ?)"
        like = f"%{q}%"
        params.extend([like, like, like, like])
    sql += " ORDER BY COALESCE(completed_at, updated_at) DESC LIMIT ?"
    params.append(limit)
    rows = connection.execute(sql, params).fetchall()
    connection.close()
    return [_decode(row) for row in rows]


def get_case_meta(case_id: str) -> Optional[dict[str, Any]]:
    """Read one saved case and decode its JSON tags."""
    init_library()
    connection = _connect()
    row = connection.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
    connection.close()
    return _decode(row) if row else None


def delete_case(case_id: str) -> bool:
    """Delete a library entry and its case-storage folder."""
    init_library()
    connection = _connect()
    cur = connection.execute("DELETE FROM cases WHERE case_id = ?", (case_id,))
    connection.commit()
    deleted = cur.rowcount > 0
    connection.close()
    # remove storage folder
    case_dir = STORAGE_DIR / case_id
    if case_dir.exists():
        import shutil

        shutil.rmtree(case_dir, ignore_errors=True)
    return deleted
