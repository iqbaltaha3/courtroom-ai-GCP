"""Background pipeline runner — reuses app.simulation.pipeline stages."""

from __future__ import annotations

import asyncio
import os
import re
import traceback
import uuid
from typing import Optional

from app.config import STORAGE_DIR
from app.database import library as library_store
from app.database.cases import (
    get_connection,
    get_report,
    init_schema,
    save_artifact,
)
from app.documents.ingestion import ingest_documents
from app.services.run_context import RunContext, SimulationCancelled, current_run, current_stage
from app.services.task_registry import StageState, _now, registry
from app.simulation.pipeline import PIPELINE_STEPS

# Pause between stage groups so Vertex RPM/TPM quotas are less likely to 429.
STAGE_GAP_SECONDS = float(os.getenv("STAGE_GAP_SECONDS", "8"))
# Extra pause before the Judge stage (often the heaviest structured call).
JUDGE_GAP_SECONDS = float(os.getenv("JUDGE_GAP_SECONDS", "15"))


async def _emit(case_id: str, **payload) -> None:
    """Send one simulation progress event through the case registry."""
    await registry.broadcast(case_id, payload)


def _is_cancelled(case_id: str) -> bool:
    """Check whether the user has requested cancellation for this case."""
    state = registry.get(case_id)
    return bool(state and state.cancel_requested)


def _init_stages() -> list[StageState]:
    """Create the initial visible state for every configured simulation stage."""
    return [StageState(key=key, title=title) for title, key, _ in PIPELINE_STEPS]


def _execution_groups():
    """Group independent consultants and researchers while preserving stage dependencies."""
    by_key = {key: (title, key, fn) for title, key, fn in PIPELINE_STEPS}
    return [
        [by_key["clarify_extract"]],
        [by_key["case_manager"]],
        [by_key["prosecution_consultant"], by_key["defense_consultant"]],
        [by_key["prosecution_research"], by_key["defense_research"]],
        [by_key["pros_r1"]],
        [by_key["def_r1"]],
        [by_key["pros_r2"]],
        [by_key["def_r2"]],
        [by_key["judge"]],
    ]


async def _finish_terminated(case_id: str, judge: Optional[str], narrative: str) -> None:
    """Mark the run and active stages as stopped, then save the final status."""
    state = registry.get(case_id)
    if state:
        state.status = "terminated"
        state.error = "Simulation terminated by user."
        state.current_stage = None
        state.run_finished_at = _now()
        for stage in state.stages:
            if stage.status == "running":
                stage.status = "error"
                stage.error = state.error
                stage.finished_at = _now()
    await _emit(case_id, type="run_terminated", status="terminated", error="Simulation terminated by user.")
    library_store.upsert_case(case_id, status="terminated", selected_judge=judge, narrative_preview=narrative)


async def run_pipeline(
    case_id: str,
    judge: Optional[str],
    allegation: list[tuple[str, bytes]],
    petition: list[tuple[str, bytes]],
    evidence: list[tuple[str, bytes]],
    narrative: str = "",
) -> None:
    """Bind per-case context, execute the run, and persist metrics and final state."""
    context = RunContext(STORAGE_DIR / case_id / "case.db", judge)
    token = current_run.set(context)
    state = registry.get(case_id)
    if state:
        state.runtime = context
        if state.cancel_requested:
            context.cancelled.set()
    try:
        await _run_pipeline(case_id, judge, allegation, petition, evidence, narrative)
    finally:
        conn = get_connection()
        try:
            init_schema(conn)
            save_artifact(conn, "llm_metrics", context.metrics)
            if state:
                save_artifact(conn, "run_state", state.to_dict())
        finally:
            conn.close()
            if state:
                state.runtime = None
            current_run.reset(token)


async def _run_pipeline(
    case_id: str,
    judge: Optional[str],
    allegation: list[tuple[str, bytes]],
    petition: list[tuple[str, bytes]],
    evidence: list[tuple[str, bytes]],
    narrative: str = "",
) -> None:
    """Coordinate ingestion, stage execution, progress updates, and library archival."""
    state = registry.get(case_id)
    if not state:
        return

    run_id = state.run_id or str(uuid.uuid4())
    state.run_id = run_id
    state.selected_judge = judge
    state.status = "ingesting"
    state.stages = _init_stages()
    state.progress_pct = 0
    state.error = None
    state.current_stage = "ingestion"
    state.run_started_at = _now()
    state.run_finished_at = None
    state.ingestion_pages_done = 0
    state.ingestion_pages_total = 0
    state.ingestion_current_page = None
    await _emit(case_id, type="run_started", run_id=run_id, status="ingesting", progress_pct=0)

    # Ingestion gets its own visible slice of the overall bar (2%-40%) rather
    # than a single "still ingesting" step, since transcribing many pages
    # through the LLM can legitimately take minutes and previously showed no
    # movement at all for the whole phase.
    INGEST_BASE_PCT = 2
    INGEST_SPAN_PCT = 38
    STAGES_BASE_PCT = INGEST_BASE_PCT + INGEST_SPAN_PCT  # 40

    context_path = STORAGE_DIR / case_id / "case.db"
    loop = asyncio.get_running_loop()

    def _on_ingest_progress(pages_done: int, pages_total: int, page_label: str) -> None:
        # Called from the ingestion worker thread — must not touch asyncio
        # primitives directly. call_soon_threadsafe hands the actual work
        # back to the event loop thread.
        """Schedule worker-thread page progress updates on the asyncio event loop."""

        def _apply():
            """Update live ingestion counts and notify subscribers on the event loop thread."""
            state.ingestion_pages_done = pages_done
            state.ingestion_pages_total = pages_total
            state.ingestion_current_page = page_label
            pct = INGEST_BASE_PCT
            if pages_total:
                pct = INGEST_BASE_PCT + int(INGEST_SPAN_PCT * pages_done / pages_total)
            state.progress_pct = pct
            registry._broadcast_sync(
                case_id,
                {
                    "type": "ingestion_progress",
                    "pages_done": pages_done,
                    "pages_total": pages_total,
                    "current_page": page_label,
                    "progress_pct": pct,
                },
            )

        loop.call_soon_threadsafe(_apply)

    try:
        if _is_cancelled(case_id):
            await _finish_terminated(case_id, judge, narrative)
            return

        state.progress_pct = INGEST_BASE_PCT
        await _emit(
            case_id,
            type="stage_update",
            stage="ingestion",
            status="running",
            progress_pct=INGEST_BASE_PCT,
            message="Ingesting documents",
        )
        # Pass the database path explicitly, like choosing the output of a data pipeline.
        info = await asyncio.to_thread(
            ingest_documents,
            context_path,
            allegation,
            petition,
            evidence,
            narrative=narrative,
            on_progress=_on_ingest_progress,
        )
        if info.get("ok") is False:
            raise ValueError(f"Document ingestion failed: {info.get('stats', {}).get('errors', [])}")
        if _is_cancelled(case_id):
            await _finish_terminated(case_id, judge, narrative)
            return

        state.status = "running"
        state.progress_pct = STAGES_BASE_PCT
        await _emit(
            case_id,
            type="stage_update",
            stage="ingestion",
            status="done",
            progress_pct=STAGES_BASE_PCT,
            message="Ingestion complete",
        )

        total = len(PIPELINE_STEPS)
        completed = 0
        stage_by_key = {stage.key: stage for stage in state.stages}

        async def _complete_stage(title: str, key: str, result) -> None:
            """Mark a stage complete and publish its report and updated progress."""
            nonlocal completed
            stage = stage_by_key[key]
            stage.status = "done"
            stage.finished_at = _now()
            completed += 1
            pct_done = int(STAGES_BASE_PCT + (completed / total) * (100 - STAGES_BASE_PCT))
            state.progress_pct = pct_done
            report = None
            try:
                conn = get_connection()
                report = get_report(conn, key)
                conn.close()
            except Exception:
                report = result.get("report") if isinstance(result, dict) else None
            if report:
                await _emit(case_id, type="stage_report", stage=key, title=title, report=report)
            await _emit(
                case_id,
                type="stage_update",
                stage=key,
                title=title,
                status="done",
                progress_pct=pct_done,
                message=title,
                has_report=bool(report),
            )

        async def _fail_stage(title: str, key: str, exc: Exception) -> None:
            """Publish a stage failure and save the failed case status."""
            stage = stage_by_key[key]
            stage.status = "error"
            stage.error = str(exc)
            stage.finished_at = _now()
            state.status = "failed"
            state.error = f"{title}: {exc}"
            await _emit(
                case_id,
                type="stage_update",
                stage=key,
                title=title,
                status="error",
                progress_pct=state.progress_pct,
                message=str(exc),
                error=str(exc),
            )
            await _emit(case_id, type="run_failed", error=state.error)
            library_store.upsert_case(
                case_id,
                status="failed",
                selected_judge=judge,
                narrative_preview=narrative,
            )

        groups = _execution_groups()
        for gi, group in enumerate(groups):
            if _is_cancelled(case_id):
                await _finish_terminated(case_id, judge, narrative)
                return

            # Pace requests: longer gap before Judge; normal gap between other groups.
            if gi > 0:
                gap = JUDGE_GAP_SECONDS if any(k == "judge" for _, k, _ in group) else STAGE_GAP_SECONDS
                if gap > 0:
                    await _emit(
                        case_id,
                        type="stage_update",
                        stage="pacing",
                        status="running",
                        progress_pct=state.progress_pct,
                        message=f"Pacing {int(gap)}s to stay under API quota…",
                    )
                    await asyncio.sleep(gap)

            pct = int(STAGES_BASE_PCT + (completed / total) * (100 - STAGES_BASE_PCT))
            state.progress_pct = pct
            state.current_stage = ",".join(key for _, key, _ in group)
            for title, key, _ in group:
                stage = stage_by_key[key]
                stage.status = "running"
                stage.started_at = _now()
                await _emit(
                    case_id,
                    type="stage_update",
                    stage=key,
                    title=title,
                    status="running",
                    progress_pct=pct,
                    message=title,
                    parallel_group=[g_key for _, g_key, _ in group],
                )

            async def _run_stage(title: str, key: str, fn):
                """Execute one synchronous stage in a worker thread and capture its outcome."""
                stage_token = current_stage.set(key)
                try:
                    result = await asyncio.to_thread(fn)
                    if not isinstance(result, dict) or result.get("ok") is not True:
                        raise ValueError(f"Stage returned an unsuccessful result: {result}")
                    return title, key, result, None
                except Exception as exc:
                    return title, key, None, exc
                finally:
                    current_stage.reset(stage_token)

            # Sequential execution limits API load; parallel mode drains the whole group.
            serial = os.getenv("STAGE_SERIAL", "1") != "0"

            async def results():
                """Yield stage outcomes in execution order or as parallel workers finish."""
                if serial:
                    for title, key, fn in group:
                        if _is_cancelled(case_id):
                            break
                        outcome = await _run_stage(title, key, fn)
                        yield outcome
                        if outcome[3] or _is_cancelled(case_id):
                            break
                        if STAGE_GAP_SECONDS > 0:
                            await asyncio.sleep(min(STAGE_GAP_SECONDS, 5.0))
                else:
                    tasks = [asyncio.create_task(_run_stage(*stage)) for stage in group]
                    for task in asyncio.as_completed(tasks):
                        yield await task

            first_error = None
            async for title, key, result, exc in results():
                stage = stage_by_key[key]
                if exc or _is_cancelled(case_id):
                    stage.status = "error"
                    stage.error = str(exc) if exc else "Simulation terminated by user."
                    stage.finished_at = _now()
                    if exc and first_error is None:
                        first_error = (title, key, exc)
                else:
                    await _complete_stage(title, key, result)

            if _is_cancelled(case_id):
                await _finish_terminated(case_id, judge, narrative)
                return
            if first_error is not None:
                title, key, exc = first_error
                await _fail_stage(title, key, exc)
                return

        state.status = "completed"
        state.progress_pct = 100
        state.current_stage = "judge"
        state.run_finished_at = _now()
        await _emit(
            case_id,
            type="run_completed",
            progress_pct=100,
            status="completed",
            elapsed_seconds=state.to_dict().get("elapsed_seconds"),
            stages=state.to_dict().get("stages"),
        )

        # Archive into Case Library
        verdict, confidence = None, None
        try:
            conn = get_connection()
            judge_text = get_report(conn, "judge") or ""
            conn.close()
            vm = re.search(r"Verdict:\s*(.+)", judge_text, re.I)
            cm = re.search(r"Confidence:\s*(.+)", judge_text, re.I)
            if vm:
                verdict = vm.group(1).strip()[:200]
            if cm:
                confidence = cm.group(1).strip()[:50]
        except Exception:
            pass
        title = (narrative.strip()[:80] if narrative.strip() else None) or f"Case {case_id[:8]}"
        library_store.upsert_case(
            case_id,
            title=title,
            status="completed",
            selected_judge=judge,
            verdict=verdict,
            confidence=confidence,
            narrative_preview=narrative,
            completed=True,
        )
    except Exception as e:
        if isinstance(e, SimulationCancelled) or _is_cancelled(case_id):
            await _finish_terminated(case_id, judge, narrative)
            return
        state.status = "failed"
        state.error = str(e)
        state.run_finished_at = _now()
        traceback.print_exc()
        await _emit(
            case_id, type="run_failed", error=str(e), elapsed_seconds=state.to_dict().get("elapsed_seconds")
        )
        library_store.upsert_case(case_id, status="failed", selected_judge=judge, narrative_preview=narrative)
