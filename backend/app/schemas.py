"""Pydantic models define the HTTP request and response fields."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class HealthOut(BaseModel):
    """Configuration and local-data status returned by the health endpoint."""

    status: str
    llm_configured: bool
    llm_provider: str
    llm_model: str
    judges: int
    statute_ok: bool


class JudgeOut(BaseModel):
    """Identifier and readable name for a selectable judge profile."""

    id: str
    display_name: str


class CaseCreateOut(BaseModel):
    """Identifier and initial status of a newly created case."""

    case_id: str
    status: str


class CaseOut(BaseModel):
    """Public case state shared by REST responses and WebSocket snapshots."""

    case_id: str
    status: str
    selected_judge: Optional[str] = None
    run_id: Optional[str] = None
    progress_pct: int = 0
    current_stage: Optional[str] = None
    stages: list[dict[str, Any]] = Field(default_factory=list)
    error: Optional[str] = None
    cancel_requested: bool = False
    ingestion_pages_done: int = 0
    ingestion_pages_total: int = 0
    ingestion_current_page: Optional[str] = None
    run_started_at: Optional[str] = None
    run_finished_at: Optional[str] = None
    elapsed_seconds: Optional[float] = None


class RunStartOut(BaseModel):
    """Identifiers and status returned when a simulation is scheduled."""

    case_id: str
    run_id: str
    status: str


class ReportOut(BaseModel):
    """One stage report and its optional structured output."""

    stage_key: str
    content: str
    structured: Optional[dict[str, Any]] = None


class ReportsOut(BaseModel):
    """All report content for one case."""

    case_id: str
    reports: dict[str, str]


class StatuteHit(BaseModel):
    """One statute search result with its section text."""

    act: str
    section: str
    chapter_title: str = ""
    text: str
    score: float = 0.0


class StatuteSearchOut(BaseModel):
    """The original search query and matching statute sections."""

    query: str
    results: list[StatuteHit]


class ChatIn(BaseModel):
    """A user question about a completed case."""

    message: str


class ChatOut(BaseModel):
    """The grounded chat answer and any executed read-only query results."""

    answer: str
    sql: Optional[str] = None
    rows: Optional[list[Any]] = None


class LibraryCaseOut(BaseModel):
    """Saved case metadata shown in the library."""

    case_id: str
    title: str
    status: str
    selected_judge: Optional[str] = None
    verdict: Optional[str] = None
    confidence: Optional[str] = None
    narrative_preview: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    completed_at: Optional[str] = None
    notes: Optional[str] = None


class LibraryUpdateIn(BaseModel):
    """The case metadata fields users can edit."""

    title: Optional[str] = None
    tags: Optional[list[str]] = None
    notes: Optional[str] = None


class LibraryListOut(BaseModel):
    """A filtered list of saved cases and its returned count."""

    cases: list[LibraryCaseOut]
    total: int


class ResearchStartIn(BaseModel):
    """A legal research question with a minimum input length."""

    query: str = Field(..., min_length=10, description="Free-form legal research query")


class ResearchStartOut(BaseModel):
    """Queued research-job identifier and status."""

    job_id: str
    status: str


class ResearchJobOut(BaseModel):
    """Research progress, result, and failure details for polling."""

    job_id: str
    query: str
    status: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    stages: dict[str, Any] = Field(default_factory=dict)
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None


class ActsOut(BaseModel):
    """Names of available core and imported laws."""

    acts: list[str]
    core: list[str] = Field(default_factory=lambda: ["BNS", "BNSS", "BSA"])
    extra: list[str] = Field(default_factory=list)


class StatuteBrowseItem(BaseModel):
    """Section heading and text for statute browsing."""

    act: str
    section: str
    chapter_title: str = ""
    text: str


class StatuteBrowseOut(BaseModel):
    """The selected Act and its browsable sections."""

    act: str
    sections: list[StatuteBrowseItem] = Field(default_factory=list)
