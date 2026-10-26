"""Find the page and authority identifiers allowed in reports."""

from __future__ import annotations

from app.database.cases import get_all_pages, get_artifact


def _reference_ids(conn):
    """Collect valid page IDs and the retrieved authority IDs allowed in reports."""
    record_ids = {f"P{p['id']}" for p in get_all_pages(conn)}
    authorities = {
        s["id"]
        for side in ("prosecution", "defense", "statutes")
        for s in get_artifact(conn, f"{side}_sources", {}).get("sources", [])
    }
    return record_ids, authorities
