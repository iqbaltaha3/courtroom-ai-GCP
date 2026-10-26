"""HTTP endpoints for cases."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from app.config import MAX_UPLOAD_MB, STORAGE_DIR
from app.database import library as library_store
from app.database.cases import get_all_reports, get_artifact, get_connection, get_report
from app.schemas import CaseCreateOut, CaseOut, ReportOut, ReportsOut, RunStartOut
from app.services.runner import run_pipeline
from app.services.task_registry import registry

router = APIRouter(prefix="/api/v1", tags=["cases"])

MAX_BYTES = MAX_UPLOAD_MB * 1024 * 1024
UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024


@router.get("/cases/{case_id}/agent-details")
def get_agent_details(case_id: str):
    """Return live usage or saved per-agent details without exposing source documents."""
    from app.services.agent_details import agent_details

    try:
        case_id = str(uuid.UUID(case_id))
    except ValueError:
        raise HTTPException(404, "Case not found")
    state = registry.get(case_id)
    if not state and not library_store.get_case_meta(case_id):
        raise HTTPException(404, "Case not found")
    path = STORAGE_DIR / case_id / "case.db"
    if not path.exists():
        return {"agents": {}}
    conn = get_connection(path)
    try:
        saved = get_artifact(conn, "run_state", {})
        snapshot = state.to_dict() if state else saved
        metrics = (
            list(state.runtime.metrics) if state and state.runtime else get_artifact(conn, "llm_metrics", [])
        )
        statuses = {s["key"]: s["status"] for s in snapshot.get("stages", [])}
        statuses["ingestion"] = (
            "running"
            if snapshot.get("status") == "ingesting"
            else ("done" if get_all_reports(conn) else "pending")
        )
        return {"agents": agent_details(conn, metrics, statuses)}
    finally:
        conn.close()


@router.post("/cases", response_model=CaseCreateOut)
def create_case():
    """Assign a case ID, create upload folders, and register the new case."""
    case_id = str(uuid.uuid4())
    case_dir = STORAGE_DIR / case_id
    for sub in ("allegation", "petition", "evidence"):
        (case_dir / "uploads" / sub).mkdir(parents=True, exist_ok=True)
    registry.create(case_id)
    library_store.upsert_case(case_id, status="created", title=f"Case {case_id[:8]}")
    return CaseCreateOut(case_id=case_id, status="created")


@router.get("/cases/{case_id}", response_model=CaseOut)
def get_case(case_id: str):
    """Return live run state or recover saved state from the case database."""
    state = registry.get(case_id)
    if state:
        return CaseOut(**state.to_dict())
    meta = library_store.get_case_meta(case_id)
    if not meta:
        raise HTTPException(404, "Case not found")
    path = STORAGE_DIR / case_id / "case.db"
    if path.exists():
        conn = get_connection(path)
        try:
            saved_state = get_artifact(conn, "run_state")
        finally:
            conn.close()
        if saved_state:
            return CaseOut(**saved_state)
    return CaseOut(
        case_id=case_id,
        status=meta.get("status") or "created",
        selected_judge=meta.get("selected_judge"),
        progress_pct=100 if meta.get("status") == "completed" else 0,
        stages=[],
    )


@router.post("/cases/{case_id}/documents")
async def upload_documents(
    case_id: str,
    allegation: list[UploadFile] = File(default=[]),
    petition: list[UploadFile] = File(default=[]),
    evidence: list[UploadFile] = File(default=[]),
):
    """Stream uploaded documents to disk while preserving existing files on failure."""
    state = registry.get(case_id)
    if not state:
        raise HTTPException(404, "Case not found")
    if state.status in ("running", "ingesting"):
        raise HTTPException(409, "Case is currently running")

    case_dir = STORAGE_DIR / case_id / "uploads"
    if not allegation and not petition and not evidence:
        # The browser begins a new selection with an empty inventory reset.
        # Discard abandoned partial transfers from the previous attempt.
        staging = STORAGE_DIR / case_id / "upload_chunks"
        if staging.is_dir():
            for temporary in staging.iterdir():
                if temporary.is_file() and temporary.suffix in (".part", ".json"):
                    temporary.unlink()

    async def _save(files: list[UploadFile], kind: str) -> list[str]:
        """Write each upload to a temporary file and replace its destination only after success."""
        names = []
        dest = case_dir / kind
        dest.mkdir(parents=True, exist_ok=True)
        for f in files or []:
            name = Path(f.filename or "doc.pdf").name
            path = dest / name
            # Stream into a separate file so an oversized/interrupted upload
            # cannot overwrite an existing document or consume its size in RAM.
            import tempfile

            temporary_path = None
            try:
                with tempfile.NamedTemporaryFile(dir=dest, delete=False) as output:
                    temporary_path = Path(output.name)
                    size = 0
                    while chunk := await f.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise HTTPException(400, f"{f.filename} exceeds {MAX_UPLOAD_MB}MB limit")
                        output.write(chunk)
                temporary_path.replace(path)
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
            names.append(name)
        return names

    state.uploads["allegation"] = await _save(allegation, "allegation")
    state.uploads["petition"] = await _save(petition, "petition")
    state.uploads["evidence"] = await _save(evidence, "evidence")
    return {"ok": True, "uploads": state.uploads}


@router.post("/cases/{case_id}/documents/chunk")
async def upload_document_chunk(
    case_id: str,
    chunk: UploadFile = File(...),
    upload_id: str = Form(...),
    kind: str = Form(...),
    filename: str = Form(...),
    offset: int = Form(...),
    total_size: int = Form(...),
):
    """Reassemble a file using requests below Cloud Run's HTTP/1 body limit."""
    state = registry.get(case_id)
    if not state:
        raise HTTPException(404, "Case not found")
    if state.status in ("running", "ingesting"):
        raise HTTPException(409, "Case is currently running")
    if kind not in ("allegation", "petition", "evidence"):
        raise HTTPException(400, "Invalid document type")
    try:
        upload_key = str(uuid.UUID(upload_id))
    except ValueError:
        raise HTTPException(400, "Invalid upload ID")
    name = Path(filename).name
    if not name or name in (".", "..") or offset < 0 or total_size < 0:
        raise HTTPException(400, "Invalid filename or upload size")
    if total_size > MAX_BYTES:
        raise HTTPException(400, f"{name} exceeds {MAX_UPLOAD_MB}MB limit")
    data = await chunk.read(UPLOAD_CHUNK_BYTES + 1)
    if len(data) > UPLOAD_CHUNK_BYTES or offset + len(data) > total_size:
        raise HTTPException(400, "Chunk exceeds the allowed upload size")
    if not data and total_size:
        raise HTTPException(400, "Empty upload chunk")

    staging = STORAGE_DIR / case_id / "upload_chunks"
    staging.mkdir(parents=True, exist_ok=True)
    part = staging / f"{upload_key}.part"
    metadata_path = staging / f"{upload_key}.json"
    metadata = {"kind": kind, "filename": name, "total_size": total_size}
    if metadata_path.exists():
        if json.loads(metadata_path.read_text()) != metadata:
            raise HTTPException(409, "Upload metadata changed; restart the upload")
    elif offset != 0:
        raise HTTPException(409, "Upload must start at offset zero")
    received = part.stat().st_size if part.exists() else 0
    if offset != received:
        raise HTTPException(409, f"Expected upload offset {received}")
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with part.open("ab") as output:
        output.write(data)
    received += len(data)
    complete = received == total_size
    if complete:
        dest = STORAGE_DIR / case_id / "uploads" / kind
        dest.mkdir(parents=True, exist_ok=True)
        part.replace(dest / name)
        metadata_path.unlink(missing_ok=True)
        if name not in state.uploads[kind]:
            state.uploads[kind].append(name)
    return {"ok": True, "received": received, "complete": complete}


@router.post("/cases/{case_id}/runs", response_model=RunStartOut)
async def start_run(
    case_id: str,
    judge: Optional[str] = Form(None),
    narrative: Optional[str] = Form(""),
):
    """Validate case inputs and schedule the simulation on the running event loop."""
    state = registry.get(case_id)
    if not state:
        raise HTTPException(404, "Case not found")
    if state.status in ("running", "ingesting"):
        raise HTTPException(409, "A run is already in progress")
    if state.runtime and state.cancel_requested and state.runtime.cancelled.is_set():
        raise HTTPException(409, "The previous run is still stopping; start a new case")

    case_dir = STORAGE_DIR / case_id / "uploads"

    def _load(kind: str):
        """Read only the document names registered for the selected upload category."""
        folder = case_dir / kind
        out = []
        if folder.is_dir():
            for name in state.uploads[kind]:
                p = folder / Path(name).name
                if p.is_file():
                    out.append((p.name, p.read_bytes()))
        return out

    allegation = _load("allegation")
    petition = _load("petition")
    evidence = _load("evidence")
    narr = (narrative or state.narrative or "").strip()

    if not allegation and not petition and not evidence and not narr:
        raise HTTPException(400, "Upload at least one document or provide a narrative")

    state.narrative = narr
    state.cancel_requested = False
    state.status = "ingesting"
    state.run_id = str(uuid.uuid4())
    title = (narr.strip()[:80] if narr.strip() else None) or f"Case {case_id[:8]}"
    library_store.upsert_case(
        case_id,
        title=title,
        status="running",
        selected_judge=judge,
        narrative_preview=narr,
    )

    async def _job():
        """Run the scheduled case simulation in the background."""
        await run_pipeline(case_id, judge, allegation, petition, evidence, narr)

    # Fire-and-forget on the running event loop
    asyncio.create_task(_job())

    return RunStartOut(case_id=case_id, run_id=state.run_id, status="ingesting")


@router.post("/cases/{case_id}/terminate")
async def terminate_run(case_id: str):
    """Request cancellation and notify subscribers while workers finish safely."""
    state = registry.get(case_id)
    if not state:
        raise HTTPException(404, "Case not found")
    if state.status not in ("ingesting", "running"):
        return {"case_id": case_id, "status": state.status, "ok": True}
    state.cancel_requested = True
    if state.runtime:
        state.runtime.cancelled.set()
    state.error = "Simulation terminated by user."
    library_store.upsert_case(case_id, status="terminated", selected_judge=state.selected_judge)
    await registry.broadcast(
        case_id,
        {
            "type": "termination_requested",
            "status": state.status,
            "error": state.error,
            "progress_pct": state.progress_pct,
        },
    )
    return {"case_id": case_id, "status": state.status, "ok": True}


@router.get("/cases/{case_id}/reports", response_model=ReportsOut)
def list_reports(case_id: str):
    """Return all saved stage reports for the selected case."""
    state = registry.get(case_id)
    if not state and not library_store.get_case_meta(case_id):
        raise HTTPException(404, "Case not found")
    try:
        path = STORAGE_DIR / case_id / "case.db"
        if not path.exists():
            return ReportsOut(case_id=case_id, reports={})
        conn = get_connection(path)
        reports = get_all_reports(conn)
        conn.close()
    except Exception as e:
        raise HTTPException(500, str(e))
    return ReportsOut(case_id=case_id, reports=reports)


@router.get("/cases/{case_id}/reports/{stage_key}", response_model=ReportOut)
def one_report(case_id: str, stage_key: str):
    """Return one stage report together with its structured model output."""
    state = registry.get(case_id)
    if not state and not library_store.get_case_meta(case_id):
        raise HTTPException(404, "Case not found")
    path = STORAGE_DIR / case_id / "case.db"
    if not path.exists():
        raise HTTPException(404, "Report not found")
    conn = get_connection(path)
    content = get_report(conn, stage_key) or ""
    row = conn.execute(
        "SELECT structured_json FROM reports WHERE report_type=? ORDER BY id DESC LIMIT 1", (stage_key,)
    ).fetchone()
    structured = json.loads(row[0]) if row and row[0] else None
    conn.close()
    if not content:
        raise HTTPException(404, "Report not found")
    return ReportOut(stage_key=stage_key, content=content, structured=structured)


@router.get("/cases/{case_id}/export.pdf")
def download_case_pdf(case_id: str):
    """Download the client-facing stage reports after the simulation finishes saving."""
    try:
        uuid.UUID(case_id)
    except ValueError:
        raise HTTPException(404, "Case not found")
    metadata = library_store.get_case_meta(case_id)
    if not metadata:
        raise HTTPException(404, "Case not found")
    state = registry.get(case_id)
    if metadata["status"] != "completed" or (state and (state.status != "completed" or state.runtime)):
        raise HTTPException(409, "The simulation must finish saving before its PDF can be downloaded")
    path = STORAGE_DIR / case_id / "case.db"
    if not path.is_file():
        raise HTTPException(404, "Case database not found")
    from app.services.pdf_export import build_case_pdf

    return Response(
        build_case_pdf(path, metadata),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="simulation-{case_id}.pdf"',
            "Cache-Control": "no-store",
        },
    )


@router.websocket("/cases/{case_id}/ws")
async def case_ws(websocket: WebSocket, case_id: str):
    # Always accept first. Closing before accept() is logged by Uvicorn as
    # "403 Forbidden", which floods the console when the frontend retries a
    # stale case_id after a backend restart.
    """Send a state snapshot and live progress events until the client disconnects."""
    await websocket.accept()
    state = registry.get(case_id)
    if not state:
        await websocket.close(code=4404)
        return
    q: asyncio.Queue = asyncio.Queue()
    state.subscribers.add(q)

    async def _sender() -> None:
        """Send queued progress events and keep idle WebSocket connections alive."""
        await websocket.send_json({"type": "snapshot", **state.to_dict()})
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=25.0)
            except asyncio.TimeoutError:
                event = {"type": "ping"}
            await websocket.send_json(event)

    async def _receiver() -> None:
        # We don't expect the client to send anything meaningful; this task
        # exists purely so we notice a disconnect promptly instead of only
        # finding out the next time _sender() tries (and fails) to send.
        """Wait for incoming messages so disconnects are detected promptly."""
        while True:
            await websocket.receive_text()

    sender_task = asyncio.create_task(_sender())
    receiver_task = asyncio.create_task(_receiver())
    try:
        done, pending = await asyncio.wait({sender_task, receiver_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        # Give cancelled tasks a chance to actually finish unwinding so we
        # don't leak "Task was destroyed but it is pending" warnings, and
        # surface any real (non-disconnect, non-cancellation) error.
        results = await asyncio.gather(*pending, return_exceptions=True)
        for result in [t.exception() for t in done] + list(results):
            if isinstance(result, Exception) and not isinstance(
                result, (WebSocketDisconnect, asyncio.CancelledError)
            ):
                raise result
    except WebSocketDisconnect:
        pass
    finally:
        sender_task.cancel()
        receiver_task.cancel()
        state.subscribers.discard(q)
