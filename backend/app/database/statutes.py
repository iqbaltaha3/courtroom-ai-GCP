"""
Local statute retriever over the Chroma SQLite DB (BNS / BNSS / BSA).

Uses the existing FTS5 index + metadata tables — no chromadb Python package
and no embedding model required at runtime. Returns the actual section text
so the Legal Research agent can ground its statutory analysis in the real Acts.
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Optional

from app.config import DATA_DIR

# Default location relative to this file → backend/data/chroma.sqlite3
_DEFAULT_DB = DATA_DIR / "statutes.sqlite3"
DB_PATH = Path(os.getenv("STATUTE_DB_PATH", str(_DEFAULT_DB)))

# Act aliases so callers can pass common names
_ACT_ALIASES = {
    "bns": "BNS",
    "bharatiya nyaya sanhita": "BNS",
    "nyaya sanhita": "BNS",
    "bnss": "BNSS",
    "bharatiya nagarik suraksha sanhita": "BNSS",
    "nagarik suraksha": "BNSS",
    "bsa": "BSA",
    "bharatiya sakshya adhiniyam": "BSA",
    "sakshya": "BSA",
}


def _normalize_act(act: Optional[str]) -> Optional[str]:
    """Convert supported law aliases to their canonical Act codes."""
    if not act:
        return None
    key = act.strip().lower()
    return _ACT_ALIASES.get(key, act.strip().upper())


def _connect() -> sqlite3.Connection:
    """Open the local statute database with named result columns."""
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Statute DB not found at {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_meta(conn: sqlite3.Connection, emb_id: int) -> dict[str, str]:
    """Read the metadata fields attached to a statute section."""
    rows = conn.execute(
        "SELECT key, string_value FROM embedding_metadata WHERE id = ?",
        (emb_id,),
    ).fetchall()
    return {r["key"]: r["string_value"] for r in rows}


def _extract_section_numbers(text: str) -> list[str]:
    """Pull likely section numbers from free text (e.g. 'BNS 303', 'Section 316')."""
    found = re.findall(
        r"(?:section|sec\.?|s\.?)\s*(\d{1,4}[A-Za-z]?)",
        text,
        flags=re.IGNORECASE,
    )
    found += re.findall(r"\b(\d{2,4}[A-Za-z]?)\b", text)
    seen = set()
    out = []
    for n in found:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out[:8]


def _keyword_score(text: str, tokens: list[str]) -> int:
    """Count query words that occur in the supplied section text."""
    low = text.lower()
    return sum(1 for t in tokens if t.lower() in low)


def _lookup(conn, key, value, act, limit, exact=False):
    """Look up metadata values with an optional Act filter and parameterized values."""
    operator = "=" if exact else "LIKE"
    sql = f"""SELECT e.id FROM embeddings e
        JOIN embedding_metadata m ON e.id=m.id AND m.key=? AND m.string_value {operator} ?"""
    params = [key, value]
    if act:
        sql += " JOIN embedding_metadata a ON e.id=a.id AND a.key='act' AND a.string_value=?"
        params.append(act)
    return conn.execute(sql + " LIMIT ?", [*params, limit]).fetchall()


def search_statutes(query, *, act=None, section_numbers=None, limit=8):
    """Search exact sections, title prefixes, full text, and finally loose keywords."""
    if not query and not section_numbers:
        return []
    try:
        conn = _connect()
    except FileNotFoundError:
        return []
    act = _normalize_act(act)
    results, seen = [], set()

    def add(emb_id, hint, meta=None):
        """Shape a new result while preserving the first matching search method."""
        if emb_id in seen:
            return
        meta = meta or _fetch_meta(conn, emb_id)
        results.append(
            {
                "act": meta.get("act", ""),
                "section_number": meta.get("section_number", ""),
                "chapter_title": meta.get("chapter_title", ""),
                "chapter_number": meta.get("chapter_number", ""),
                "text": meta.get("chroma:document", ""),
                "source_url": meta.get("source_url", ""),
                "score_hint": hint,
            }
        )
        seen.add(emb_id)

    try:
        # Explicit or detected section numbers take priority over keyword matches.
        sections = list(section_numbers or []) + (_extract_section_numbers(query) if query else [])
        for section in dict.fromkeys(sections):
            cleaned = re.sub(r"[^\dA-Za-z.-]", "", str(section)).lstrip("0") or str(section)
            for row in _lookup(conn, "section_number", cleaned, act, -1, exact=True):
                add(row["id"], "exact")

        stop_words = {
            "section",
            "shall",
            "under",
            "this",
            "that",
            "with",
            "from",
            "such",
            "person",
            "persons",
            "offence",
            "offences",
            "act",
        }
        words = re.findall(r"[A-Za-z]{4,}", query)
        title_stop = stop_words | {"whoever", "intending", "dishonestly", "movable", "property"}
        title_words = [word for word in words if word.lower() not in title_stop][:4]
        for word in title_words:
            if len(results) >= limit:
                break
            for row in _lookup(conn, "chroma:document", f"{word}%", act, 5):
                add(row["id"], "title")

        # The existing SQLite FTS index supplies candidates; title overlap improves ordering.
        tokens = [word for word in words if word.lower() not in stop_words][:8]
        if tokens and len(results) < limit:
            try:
                rows = conn.execute(
                    """SELECT rowid, string_value FROM embedding_fulltext_search
                    WHERE string_value MATCH ? LIMIT 40""",
                    (" OR ".join(tokens),),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            scored = []
            for row in rows:
                if row["rowid"] in seen:
                    continue
                meta = _fetch_meta(conn, row["rowid"])
                if act and meta.get("act") != act:
                    continue
                text = meta.get("chroma:document", row["string_value"] or "")
                score = _keyword_score(text, tokens)
                title = text.split(".—")[0].split(".")[0].strip().lower()
                if any(token.lower() in title for token in tokens):
                    score += 5
                number = int(re.sub(r"[^0-9]", "", meta.get("section_number", "0") or "0") or 0)
                score += int(number >= 20)
                scored.append((score, row["rowid"], meta))
            for score, emb_id, meta in sorted(scored, key=lambda item: item[0], reverse=True):
                add(emb_id, f"fts:{score}", meta)
                if len(results) >= limit:
                    break

        # Loose matching covers terms that the FTS tokenizer did not return.
        keywords = sorted(set(re.findall(r"[A-Za-z]{5,}", query)), key=lambda word: (-len(word), word))[:3]
        for word in keywords:
            if len(results) >= limit:
                break
            for row in _lookup(conn, "chroma:document", f"%{word}%", act, limit * 2):
                add(row["id"], "like")
                if len(results) >= limit:
                    break
        return results[:limit]
    finally:
        conn.close()


def format_statute_context(sections: list[dict[str, Any]], max_chars: int = 3500) -> str:
    """Render retrieved sections into a compact block for the LLM prompt."""
    if not sections:
        return "No matching statutory text is available locally for these Act/section references."

    parts: list[str] = []
    total = 0
    for s in sections:
        header = f"[{s.get('act', '?')} Section {s.get('section_number', '?')}]"
        if s.get("chapter_title"):
            header += f" — {s['chapter_title']}"
        body = (s.get("text") or "").strip()
        chunk = f"{header}\n{body}"
        if total + len(chunk) > max_chars:
            remaining = max_chars - total
            if remaining > 100:
                parts.append(chunk[:remaining] + "…")
            break
        parts.append(chunk)
        total += len(chunk) + 2

    return "\n\n".join(parts)


def get_db_stats() -> dict[str, Any]:
    """Quick health check used by /health or tests."""
    try:
        conn = _connect()
        counts = dict(
            conn.execute(
                "SELECT string_value, COUNT(*) FROM embedding_metadata WHERE key='act' GROUP BY string_value"
            ).fetchall()
        )
        total = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        conn.close()
        return {"ok": True, "path": str(DB_PATH), "total_sections": total, "by_act": counts}
    except Exception as e:
        return {"ok": False, "error": str(e), "path": str(DB_PATH)}


_ACT_INFO = {
    "BNS": "Bharatiya Nyaya Sanhita, 2023",
    "BNSS": "Bharatiya Nagarik Suraksha Sanhita, 2023",
    "BSA": "Bharatiya Sakshya Adhiniyam, 2023",
}


def _section_sort_key(sn: str):
    """Sort numeric section labels before non-numeric labels."""
    m = re.match(r"(\d+)([A-Za-z]*)", str(sn or ""))
    return (int(m.group(1)), m.group(2).lower()) if m else (10**9, str(sn))


def get_all_sections() -> dict[str, Any]:
    """Every section in the DB, grouped by act and ordered by section number."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, key, string_value FROM embedding_metadata "
            "WHERE key IN ('act','section_number','chapter_number','chapter_title',"
            "'chroma:document','page_number','source_url')"
        ).fetchall()
    finally:
        conn.close()

    by_id: dict[int, dict[str, str]] = {}
    for r in rows:
        by_id.setdefault(r["id"], {})[r["key"]] = r["string_value"] or ""

    grouped: dict[str, list[dict[str, str]]] = {}
    sources: dict[str, str] = {}
    for meta in by_id.values():
        act = meta.get("act", "")
        if not act or not meta.get("chroma:document"):
            continue
        sources.setdefault(act, meta.get("source_url", ""))
        grouped.setdefault(act, []).append(
            {
                "n": meta.get("section_number", ""),
                "c": meta.get("chapter_number", ""),
                "ct": meta.get("chapter_title", ""),
                "t": meta.get("chroma:document", ""),
                "p": meta.get("page_number", ""),
            }
        )

    acts = []
    for code in [c for c in ("BNS", "BNSS", "BSA") if c in grouped] + sorted(
        c for c in grouped if c not in ("BNS", "BNSS", "BSA")
    ):
        secs = sorted(grouped[code], key=lambda s: _section_sort_key(s["n"]))
        acts.append(
            {
                "code": code,
                "name": _ACT_INFO.get(code, code),
                "source": sources.get(code, ""),
                "sections": secs,
            }
        )
    return {"acts": acts}
