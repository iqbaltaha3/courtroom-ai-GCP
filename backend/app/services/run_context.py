"""Per-run state propagated to worker threads by asyncio.to_thread."""

from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event


class SimulationCancelled(RuntimeError):
    """Signal that the user requested the current simulation to stop."""

    pass


@dataclass
class RunContext:
    """Per-run database path, judge selection, cancellation signal, and usage metrics."""

    db_path: Path
    judge: str | None = None
    cancelled: Event = field(default_factory=Event)
    metrics: list[dict] = field(default_factory=list)


current_run: ContextVar[RunContext | None] = ContextVar("courtroom_run", default=None)
current_stage: ContextVar[str] = ContextVar("courtroom_stage", default="ingestion")


def check_cancelled() -> None:
    """Stop work when the current run has received a cancellation request."""
    context = current_run.get()
    if context and context.cancelled.is_set():
        raise SimulationCancelled("Simulation terminated by user.")
