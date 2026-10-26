"""In-memory registry for case runs and WebSocket subscribers."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.services.run_context import RunContext


def _now() -> str:
    """Return the current UTC timestamp for saved records."""
    return datetime.now(timezone.utc).isoformat()


def _seconds(start, end):
    """Return a duration only when both timestamps can be parsed."""
    if not start or not end:
        return None
    try:
        return round((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds(), 1)
    except ValueError:
        return None


@dataclass
class StageState:
    """Timing, status, and error details for one visible simulation stage."""

    key: str
    title: str
    status: str = "pending"  # pending | running | done | error
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None


@dataclass
class CaseState:
    """Live case progress, uploaded filenames, and subscriber queues."""

    case_id: str
    status: str = "created"  # created | ingesting | running | completed | failed | terminated
    selected_judge: Optional[str] = None
    run_id: Optional[str] = None
    progress_pct: int = 0
    current_stage: Optional[str] = None
    stages: list[StageState] = field(default_factory=list)
    error: Optional[str] = None
    narrative: str = ""
    cancel_requested: bool = False
    runtime: Optional[RunContext] = None
    uploads: dict[str, list[str]] = field(
        default_factory=lambda: {"allegation": [], "petition": [], "evidence": []}
    )
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    # Live, per-page ingestion progress. These are set from a worker thread
    # via loop.call_soon_threadsafe, so plain attribute writes are fine, and
    # they're included in to_dict() so REST polling sees the same detail the
    # WebSocket does — not just an overall percentage that sits still for the
    # whole ingestion phase.
    ingestion_pages_done: int = 0
    ingestion_pages_total: int = 0
    ingestion_current_page: Optional[str] = None
    run_started_at: Optional[str] = None
    run_finished_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize public case state and timing without exposing thread objects or subscribers."""
        return {
            "case_id": self.case_id,
            "status": self.status,
            "selected_judge": self.selected_judge,
            "run_id": self.run_id,
            "progress_pct": self.progress_pct,
            "current_stage": self.current_stage,
            "stages": [
                {**asdict(stage), "duration_seconds": _seconds(stage.started_at, stage.finished_at)}
                for stage in self.stages
            ],
            "error": self.error,
            "cancel_requested": self.cancel_requested,
            "ingestion_pages_done": self.ingestion_pages_done,
            "ingestion_pages_total": self.ingestion_pages_total,
            "ingestion_current_page": self.ingestion_current_page,
            "run_started_at": self.run_started_at,
            "run_finished_at": self.run_finished_at,
            "elapsed_seconds": _seconds(self.run_started_at, self.run_finished_at or _now()),
        }


class Registry:
    """Store live case states and distribute their progress events."""

    def __init__(self) -> None:
        """Create an empty in-memory case registry."""
        self.cases: dict[str, CaseState] = {}

    def get(self, case_id: str) -> Optional[CaseState]:
        """Look up live state by case ID."""
        return self.cases.get(case_id)

    def create(self, case_id: str) -> CaseState:
        """Register a new case and its initial state."""
        state = CaseState(case_id=case_id)
        self.cases[case_id] = state
        return state

    def _broadcast_sync(self, case_id: str, event: dict[str, Any]) -> None:
        """Queue an event for each active subscriber on the event loop thread."""
        state = self.cases.get(case_id)
        if not state:
            return
        dead = []
        for q in list(state.subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            state.subscribers.discard(q)

    async def broadcast(self, case_id: str, event: dict[str, Any]) -> None:
        """Deliver a progress event to the case subscribers."""
        self._broadcast_sync(case_id, event)


registry = Registry()
