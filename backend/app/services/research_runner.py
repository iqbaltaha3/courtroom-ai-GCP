"""Track research jobs and run one shared extract-to-memo workflow."""

import logging
import uuid

from fastapi.encoders import jsonable_encoder

from app.config import GCP_PROJECT_ID, MAX_CANDIDATES_TO_RANK, TAVILY_API_KEY
from app.services.task_registry import _now

logger = logging.getLogger(__name__)
_jobs = {}


def create_job(query, job_id=None):
    """Register a queued job before background execution starts."""
    job_id = job_id or str(uuid.uuid4())
    job = {
        "job_id": job_id,
        "query": query,
        "status": "queued",
        "created_at": _now(),
        "updated_at": _now(),
        "stages": {},
        "result": None,
        "error": None,
    }
    _jobs[job_id] = job
    return job


def get_job(job_id):
    """Return a job for API polling."""
    return _jobs.get(job_id)


def list_jobs(limit=50):
    """Return newest jobs first."""
    return sorted(_jobs.values(), key=lambda job: job["created_at"], reverse=True)[:limit]


def run_research(query, job_id=None):
    """Extract facts, retrieve law, search precedents, rank sources, and write a memo."""
    job = get_job(job_id) or create_job(query, job_id)
    job["status"] = "running"

    def set_stage(key, status, data=None, message=""):
        """Store JSON-safe stage details for the polling endpoint."""
        job["stages"][key] = {"status": status, "data": jsonable_encoder(data), "message": message}
        job["updated_at"] = _now()

    try:
        # Heavy index imports wait until research is requested, keeping startup lightweight.
        from app.research.extraction import extract_case_facts
        from app.research.planning import generate_research_plan
        from app.research.ranking import rank_precedents, select_final_candidates
        from app.research.reasoning import final_legal_reasoning_with_query
        from app.research.retrieval import build_statute_context, retrieve_statutes
        from app.research.search import load_index
        from app.research.web_search import deduplicate_results, search_tavily_parallel

        if not GCP_PROJECT_ID or not TAVILY_API_KEY:
            raise ValueError("GCP_PROJECT_ID and TAVILY_API_KEY are required for research")

        set_stage("extraction", "running", message="Extracting case facts")
        facts = extract_case_facts(query)
        offences = facts.alleged_offences or facts.summary or query
        allegations = facts.factual_allegations or facts.summary or query
        set_stage("extraction", "done", facts, "Case facts extracted")

        set_stage("statutes", "running", message="Hybrid BM25 + vector statute retrieval")
        collection, bm25, corpus_ids = load_index()
        statutes = retrieve_statutes(offences, allegations, collection, bm25, corpus_ids)
        statute_context = build_statute_context(statutes, collection)
        set_stage("statutes", "done", statutes, "Statutes retrieved")

        set_stage("planning", "running", message="Generating research plan")
        plan = generate_research_plan(offences, allegations, statute_context)
        set_stage("planning", "done", plan, "Research plan ready")

        set_stage("web_search", "running", message="Live precedent search (Tavily)")
        candidates = deduplicate_results(search_tavily_parallel(plan.search_queries))[:MAX_CANDIDATES_TO_RANK]
        preview = [
            {"title": item["title"], "url": item["url"], "content": item["content"][:400]}
            for item in candidates
        ]
        set_stage("web_search", "done", preview, f"{len(candidates)} candidates")

        # Ranking failure uses an explicitly labeled source-quality heuristic.
        set_stage("ranking", "running", message="Ranking with authority caps")
        ranking, ranking_message = None, "Precedents ranked"
        if candidates:
            try:
                ranking = rank_precedents(offences, allegations, plan, candidates)
            except Exception as exc:
                logger.exception("Model ranking failed; using source-quality fallback")
                ranking_message = f"Source-quality fallback used: {exc}"
        selected = select_final_candidates(candidates, ranking) if candidates else []
        set_stage("ranking", "done", {"ranking": ranking, "selected": selected}, ranking_message)

        set_stage("reasoning", "running", message="Final legal reasoning")
        result = final_legal_reasoning_with_query(
            offences, allegations, statute_context, plan, selected, query
        )
        set_stage("reasoning", "done", result, "Memo complete")
        job.update(status="completed", result=jsonable_encoder(result))
    except Exception as exc:
        logger.exception("Legal research failed")
        # Mark the active stage as failed so the UI does not show it running forever.
        for stage in job["stages"].values():
            if stage["status"] == "running":
                stage.update(status="error", message=str(exc))
        job.update(status="failed", error=str(exc))
    job["updated_at"] = _now()
    return job
