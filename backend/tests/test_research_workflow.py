"""Exercise research orchestration and validation without contacting AI providers."""

import json
from types import SimpleNamespace

import pytest

from app.research import extraction, planning, ranking, reasoning, retrieval, search, web_search
from app.research.models import CandidateAssessment, CaseFactExtraction, PrecedentRanking, ResearchPlan
from app.services import research_runner

# Keep real validation functions while the workflow fixture stubs provider stages.
rank_precedents = ranking.rank_precedents
generate_research_plan = planning.generate_research_plan
build_statute_context = retrieval.build_statute_context


@pytest.fixture
def research_steps(monkeypatch):
    """Supply a small statute and search source while keeping model output typed."""
    facts = CaseFactExtraction(
        summary="An alleged offence.",
        parties_involved=[],
        factual_allegations="The alleged facts.",
        alleged_offences="Alleged offence",
        legal_issues=[],
        disputed_facts=[],
    )
    plan = ResearchPlan(
        actual_alleged_offences=[],
        relevant_retrieved_sections=[],
        irrelevant_retrieved_sections=[],
        historical_equivalents=[],
        legal_concepts=[],
        research_questions=[],
        search_queries=[f"query {i}" for i in range(5)],
    )
    source = {
        "candidate_id": 1,
        "title": "Source",
        "url": "https://sci.gov.in/source",
        "content": "Retrieved text",
        "domain": "sci.gov.in",
        "search_query": "query 0",
        "tavily_score": 1,
    }
    assessment = CandidateAssessment(
        candidate_id=1,
        source_type="supreme_court_judgment",
        relevance_score=90,
        source_quality_score=90,
        judicial_authority_score=90,
        factual_similarity_score=90,
        keep_for_reasoner=True,
        reason="Relevant",
    )
    monkeypatch.setattr(research_runner, "GCP_PROJECT_ID", "test-project")
    monkeypatch.setattr(research_runner, "TAVILY_API_KEY", "test-key")
    monkeypatch.setattr(search, "load_index", lambda: (None, None, []))
    monkeypatch.setattr(extraction, "extract_case_facts", lambda query: facts)
    monkeypatch.setattr(retrieval, "retrieve_statutes", lambda *args: [])
    monkeypatch.setattr(retrieval, "build_statute_context", lambda *args: "Statute context")
    monkeypatch.setattr(planning, "generate_research_plan", lambda *args: plan)
    monkeypatch.setattr(web_search, "search_tavily_parallel", lambda queries: [source])
    monkeypatch.setattr(ranking, "rank_precedents", lambda *args: PrecedentRanking(assessments=[assessment]))
    captured = []

    def reason(*args):
        """Capture the original question reaching the final reasoner."""
        captured.append(args)
        return {"memo": "A supported answer."}

    monkeypatch.setattr(reasoning, "final_legal_reasoning_with_query", reason)
    research_runner._jobs.clear()
    yield plan, source, captured
    research_runner._jobs.clear()


def test_research_job_preserves_creation_time_and_serializes_nested_models(research_steps):
    """Typed assessments must become JSON values before polling returns the result."""
    job = research_runner.create_job("What remedy follows from these facts?")
    created = job["created_at"]
    result = research_runner.run_research(job["query"], job["job_id"])
    assert result is job and result["status"] == "completed"
    assert result["created_at"] == created
    assert result["stages"]["ranking"]["data"]["selected"][0]["ranker_assessment"]["candidate_id"] == 1
    assert research_steps[2][0][-1] == job["query"]
    json.dumps(result)


def test_ranking_failure_is_labeled_and_research_can_complete(research_steps, monkeypatch):
    """A heuristic fallback must remain visible rather than being labeled model-ranked."""

    def fail(*args):
        """Simulate an unavailable ranking model."""
        raise ValueError("Ranking unavailable")

    monkeypatch.setattr(ranking, "rank_precedents", fail)
    job = research_runner.run_research("What remedy follows from these facts?")
    assert job["status"] == "completed"
    stage = job["stages"]["ranking"]
    assert "fallback" in stage["message"]
    assert stage["data"]["selected"][0]["ranker_assessment"] is None


def test_research_failure_marks_active_stage_and_job(research_steps, monkeypatch):
    """A planner failure must stop execution before final reasoning."""

    def fail(*args):
        """Simulate invalid planner output."""
        raise ValueError("Invalid plan")

    monkeypatch.setattr(planning, "generate_research_plan", fail)
    job = research_runner.run_research("What remedy follows from these facts?")
    assert job["status"] == "failed" and job["error"] == "Invalid plan"
    assert job["stages"]["planning"]["status"] == "error"
    assert not research_steps[2]


@pytest.mark.parametrize("ids", [[1, 1], [1, 99], [1]])
def test_ranker_rejects_duplicate_unknown_and_missing_candidate_ids(research_steps, monkeypatch, ids):
    """Every supplied source must receive exactly one corresponding assessment."""
    plan, source, _ = research_steps

    def assess(*args, **kwargs):
        """Return a deliberately invalid set of candidate IDs."""
        return PrecedentRanking(
            assessments=[
                CandidateAssessment(
                    candidate_id=value,
                    source_type="legal_blog",
                    relevance_score=90,
                    source_quality_score=90,
                    judicial_authority_score=100,
                    factual_similarity_score=90,
                    keep_for_reasoner=True,
                    reason="Relevant",
                )
                for value in ids
            ]
        )

    monkeypatch.setattr(ranking, "call_structured", assess)
    with pytest.raises(ValueError, match="exactly once"):
        rank_precedents("offence", "facts", plan, [source, {**source, "candidate_id": 2}])


def test_planner_enforces_five_nonempty_queries(research_steps, monkeypatch):
    """Whitespace-only entries do not count as real search requests."""
    plan = research_steps[0].model_copy(update={"search_queries": ["one", " ", "three"]})
    monkeypatch.setattr(planning, "call_structured", lambda *args, **kwargs: plan)
    with pytest.raises(ValueError, match="exactly 5"):
        generate_research_plan("offence", "facts", "statutes")


def test_authority_cap_limits_blog_scores():
    """A model's inflated score cannot bypass the configured source-type ceiling."""
    assert ranking.apply_authority_cap("legal_blog", 100) == 20
    assert ranking.apply_authority_cap("social_media", 100) == 0


def test_empty_statute_retrieval_does_not_query_chroma():
    """No matches should produce explicit missing context, not an empty-ID database error."""
    assert build_statute_context([], SimpleNamespace()) == "No matching statutes were retrieved."
