"""HTTP endpoints for library."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.config import STORAGE_DIR
from app.database import library as library_store
from app.database.cases import get_all_reports, get_connection
from app.schemas import LibraryCaseOut, LibraryListOut, LibraryUpdateIn
from app.services.task_registry import registry

router = APIRouter(prefix="/api/v1/library", tags=["library"])


@router.get("/cases", response_model=LibraryListOut)
def list_library(
    status: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    """Return saved cases matching the requested filters."""
    library_store.init_library()
    rows = library_store.list_cases(status=status, q=q, limit=limit)
    cases = [LibraryCaseOut(**r) for r in rows]
    return LibraryListOut(cases=cases, total=len(cases))


@router.get("/cases/{case_id}", response_model=LibraryCaseOut)
def get_library_case(case_id: str):
    """Return metadata for one saved case or report that it is missing."""
    meta = library_store.get_case_meta(case_id)
    if not meta:
        raise HTTPException(404, "Case not in library")
    return LibraryCaseOut(**meta)


@router.patch("/cases/{case_id}", response_model=LibraryCaseOut)
def update_library_case(case_id: str, body: LibraryUpdateIn):
    """Apply editable title, tag, and note changes to a saved case."""
    if not library_store.get_case_meta(case_id):
        raise HTTPException(404, "Case not in library")
    row = library_store.upsert_case(
        case_id,
        title=body.title,
        tags=body.tags,
        notes=body.notes,
    )
    return LibraryCaseOut(**row)


@router.delete("/cases/{case_id}")
def delete_library_case(case_id: str):
    """Delete an inactive case and remove its in-memory state."""
    state = registry.get(case_id)
    if state and state.status in ("running", "ingesting"):
        raise HTTPException(409, "Terminate the simulation before deleting this case")
    ok = library_store.delete_case(case_id)
    if not ok and case_id not in registry.cases:
        raise HTTPException(404, "Case not found")
    # drop from memory registry if present
    registry.cases.pop(case_id, None)
    return {"ok": True, "case_id": case_id}


@router.get("/cases/{case_id}/reports")
def library_reports(case_id: str):
    """Return reports for an archived case from snapshot DB or live DB."""
    meta = library_store.get_case_meta(case_id)
    if not meta:
        raise HTTPException(404, "Case not in library")
    path = STORAGE_DIR / case_id / "case.db"
    reports = {}
    if path.exists():
        conn = get_connection(path)
        try:
            reports = get_all_reports(conn)
        finally:
            conn.close()
    return {"case_id": case_id, "reports": reports, "meta": meta}
