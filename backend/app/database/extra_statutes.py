"""
Extra Indian statutes for the Statute Library UI only.

Loaded from data/extra_laws/*.json into a local SQLite FTS index.
The simulation and research workflows use the core statute datasets.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Optional

from app.config import DATA_DIR

EXTRA_DIR = DATA_DIR / "extra_laws"
EXTRA_DB = DATA_DIR / "extra_statutes.db"

_CORE_ACTS = {"BNS", "BNSS", "BSA"}


def _connect() -> sqlite3.Connection:
    """Open the local statute database with named result columns."""
    EXTRA_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(EXTRA_DB))
    conn.row_factory = sqlite3.Row
    return conn


def init_extra_statutes(force: bool = False) -> int:
    """Build FTS index from JSON files. Returns section count."""
    conn = _connect()
    if not force:
        try:
            n = conn.execute("SELECT COUNT(*) AS c FROM sections").fetchone()["c"]
            if n > 0:
                conn.close()
                return n
        except sqlite3.OperationalError:
            pass

    conn.executescript(
        """
        DROP TABLE IF EXISTS sections;
        DROP TABLE IF EXISTS sections_fts;
        CREATE TABLE sections (
            id INTEGER PRIMARY KEY,
            act TEXT NOT NULL,
            section TEXT NOT NULL,
            title TEXT,
            text TEXT NOT NULL,
            keywords TEXT
        );
        CREATE VIRTUAL TABLE sections_fts USING fts5(
            act, section, title, text, keywords,
            content='sections', content_rowid='id'
        );
        """
    )

    count = 0
    if not EXTRA_DIR.is_dir():
        conn.commit()
        conn.close()
        return 0

    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")

    for path in sorted(EXTRA_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        sections = data.get("sections") if isinstance(data, dict) else data
        if not isinstance(sections, list):
            continue
        batch = 0
        for s in sections:
            if not isinstance(s, dict):
                continue
            act = (s.get("act") or path.stem).strip()
            section = str(s.get("section_number") or s.get("section") or "").strip()
            title = (s.get("section_title") or s.get("title") or "").strip()
            body = (s.get("text") or "").strip()
            if not body:
                continue
            kw = s.get("keywords") or []
            if isinstance(kw, list):
                kw = ", ".join(str(x) for x in kw)
            else:
                kw = str(kw)
            cur = conn.execute(
                "INSERT INTO sections (act, section, title, text, keywords) VALUES (?,?,?,?,?)",
                (act, section or "—", title, body, kw),
            )
            rid = cur.lastrowid
            conn.execute(
                "INSERT INTO sections_fts (rowid, act, section, title, text, keywords) VALUES (?,?,?,?,?,?)",
                (rid, act, section or "", title, body, kw),
            )
            count += 1
            batch += 1
            if batch % 50 == 0:
                conn.commit()
        conn.commit()

    conn.close()
    return count


def list_extra_acts() -> list[str]:
    """List the imported law names available for browsing."""
    init_extra_statutes()
    conn = _connect()
    try:
        rows = conn.execute("SELECT DISTINCT act FROM sections ORDER BY act COLLATE NOCASE").fetchall()
        return [r["act"] for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def list_extra_sections(act: str, limit: int = 500) -> list[dict[str, Any]]:
    """Browse sections for a single extra act, ordered by section number when possible."""
    init_extra_statutes()
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT act, section, title, text
            FROM sections
            WHERE act LIKE ?
            ORDER BY
              CAST(section AS INTEGER),
              section COLLATE NOCASE
            LIMIT ?
            """,
            (f"%{act}%", limit),
        ).fetchall()
        return [
            {
                "act": r["act"],
                "section": r["section"],
                "chapter_title": r["title"] or "",
                "text": r["text"],
            }
            for r in rows
        ]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def search_extra_statutes(query, act=None, limit=12):
    """Search imported law text with SQLite FTS and a loose-match fallback."""
    init_extra_statutes()
    query = (query or "").strip()
    if not query:
        return []
    conn = _connect()
    filtered = bool(act and act not in ("All", "all"))
    try:
        tokens = re.findall(r"[A-Za-z0-9]+", query)
        sql = """SELECT s.act, s.section, s.title, s.text, bm25(sections_fts) AS score
            FROM sections_fts JOIN sections s ON s.id=sections_fts.rowid
            WHERE sections_fts MATCH ?"""
        params = [" ".join(tokens) if tokens else query]
        if filtered:
            sql += " AND s.act LIKE ?"
            params.append(f"%{act}%")
        rows = conn.execute(sql + " ORDER BY score LIMIT ?", [*params, limit]).fetchall()
        if not rows:
            # Numeric sections and unmatched words can still match ordinary stored text.
            sql = "SELECT act, section, title, text, 0 AS score FROM sections WHERE "
            fields = ["section", "title", "text", "keywords"]
            params = []
            if filtered:
                sql += "act LIKE ? AND "
                params.append(f"%{act}%")
            else:
                fields.append("act")
            sql += "(" + " OR ".join(f"{field} LIKE ?" for field in fields) + ") LIMIT ?"
            params.extend([f"%{query}%"] * len(fields))
            rows = conn.execute(sql, [*params, limit]).fetchall()
        return [
            {
                "act": row["act"],
                "section": row["section"],
                "chapter_title": row["title"] or "",
                "title": row["title"] or "",
                "text": row["text"],
                "score": float(row["score"] or 0),
                "source": "extra",
            }
            for row in rows
        ]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def is_core_act(act: Optional[str]) -> bool:
    """Check whether the requested law is BNS, BNSS, or BSA."""
    if not act:
        return False
    a = act.strip().upper()
    return a in _CORE_ACTS
