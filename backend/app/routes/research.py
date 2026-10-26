"""HTTP endpoints for research."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.schemas import ResearchJobOut, ResearchStartIn, ResearchStartOut
from app.services import research_runner

router = APIRouter(prefix="/api/v1/research", tags=["research"])


@router.post("/runs", response_model=ResearchStartOut)
async def start_research(body: ResearchStartIn, background_tasks: BackgroundTasks):
    """Create the polling record before handing work to a background thread."""
    job = research_runner.create_job(body.query)
    background_tasks.add_task(research_runner.run_research, body.query, job["job_id"])
    return ResearchStartOut(job_id=job["job_id"], status="queued")


@router.get("/runs/{job_id}", response_model=ResearchJobOut)
def get_research_job(job_id: str):
    """Return a research job status and result for polling."""
    job = research_runner.get_job(job_id)
    if not job:
        raise HTTPException(404, "Research job not found")
    return ResearchJobOut(**job)


@router.get("/runs", response_model=list[ResearchJobOut])
def list_research_jobs(limit: int = 30):
    """Return recent research jobs for the history view."""
    return [ResearchJobOut(**j) for j in research_runner.list_jobs(limit=limit)]
