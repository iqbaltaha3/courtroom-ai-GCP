import asyncio
from types import SimpleNamespace

import pytest

from app.ai import llm
from app.database import cases as case_db
from app.services.run_context import RunContext, SimulationCancelled, current_run
from app.simulation import advocacy, extraction, judgment
from app.simulation.models import (
    ArgumentReport,
    JudicialDecision,
    JudicialReview,
    Submission,
    validate_references,
)


@pytest.fixture
def isolated_case(tmp_path):
    """Provide one page in an isolated per-run case database."""
    context = RunContext(tmp_path / "case.db")
    token = current_run.set(context)
    case_db.init_schema()
    conn = case_db.get_connection()
    case_db.bulk_insert_pages(
        conn,
        [
            {
                "doc_type": "allegation",
                "doc_name": "fir.pdf",
                "page_number": 1,
                "text": "Complainant Alice reports an alleged assault by Bob. Investigation commenced.",
                "source_hash": "test",
            }
        ],
    )
    conn.close()
    yield context
    current_run.reset(token)


def fake_client(responses, captured):
    """Return a fake completion client with a fixed response sequence."""
    stream = iter(responses)

    def create(**kwargs):
        """Return the next fake completion response and capture its request."""
        captured.append(kwargs)
        text, reason = next(stream)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason=reason)]
        )

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def test_truncated_prose_retried_with_larger_budget_not_salvaged(monkeypatch):
    """Verify truncated prose retried with larger budget not salvaged."""
    calls = []
    monkeypatch.setattr(llm, "get_client", lambda: fake)
    monkeypatch.setattr(llm.time, "sleep", lambda _: None)
    fake = fake_client([("Incomplete " * 100, "length"), ("A complete response.", "stop")], calls)
    assert llm.call_llm("s", "u") == "A complete response."
    assert calls[1]["max_tokens"] > calls[0]["max_tokens"]
    assert len(calls[1]["messages"]) == 2


def test_exhausted_truncation_never_publishes_partial(monkeypatch):
    """Verify exhausted truncation never publishes partial."""
    monkeypatch.setenv("LLM_MAX_RETRIES", "3")
    fake = fake_client([("Long partial " * 100, "length")] * 3, [])
    monkeypatch.setattr(llm, "get_client", lambda: fake)
    monkeypatch.setattr(llm.time, "sleep", lambda _: None)
    with pytest.raises(ValueError, match="length"):
        llm.call_llm("s", "u")


def test_termination_prevents_report_writes(isolated_case):
    """Verify termination prevents report writes."""
    isolated_case.cancelled.set()
    conn = case_db.get_connection()
    with pytest.raises(SimulationCancelled):
        case_db.save_report(conn, "judge", "should never publish")
    assert case_db.get_all_reports(conn) == {}
    conn.close()


def test_concurrent_case_workers_are_isolated(tmp_path):
    """Verify concurrent case workers are isolated."""

    def write(label):
        """Save a report using the active per-case database context."""
        case_db.init_schema()
        conn = case_db.get_connection()
        case_db.save_report(conn, "case_manager", label)
        conn.close()

    async def run(label):
        """Execute the fake stage and verify case-local prerequisites."""
        token = current_run.set(RunContext(tmp_path / label / "case.db"))
        try:
            await asyncio.to_thread(write, label)
            conn = case_db.get_connection()
            assert case_db.get_report(conn, "case_manager") == label
            conn.close()
        finally:
            current_run.reset(token)

    async def both():
        """Run both isolated case tasks on the same event loop."""
        await asyncio.gather(run("one"), run("two"))

    asyncio.run(both())


def test_failed_page_blocks_downstream_and_diagnostics_are_not_facts(isolated_case, monkeypatch):
    """Verify failed page blocks downstream and diagnostics are not facts."""

    def fail(*args, **kwargs):
        """Simulate a provider or validation failure."""
        raise ValueError("provider failure")

    monkeypatch.setattr(extraction, "call_structured", fail)
    with pytest.raises(ValueError, match="Extraction incomplete"):
        extraction.stage_clarify_extract()
    conn = case_db.get_connection()
    assert conn.execute("SELECT COUNT(*) FROM case_meta").fetchone()[0] == 0
    assert case_db.get_artifact(conn, "extraction_coverage")["failed_pages"]
    conn.close()


def test_unknown_page_or_authority_reference_rejected():
    """Verify unknown page or authority reference rejected."""
    submission = Submission(
        proposition="This is an alleged assault.",
        factual_basis="FIR",
        legal_analysis="Unverified",
        record_refs=["P99"],
        authority_refs=[],
        responds_to=[],
        concession_or_limit="No independent testimony",
    )
    report = ArgumentReport(submissions=[submission] * 3, relief_sought="Investigation")
    with pytest.raises(ValueError, match="Unknown record_refs"):
        validate_references(report, {"P1"}, set())


def test_missing_judgment_inputs_never_generate_fallback(isolated_case, monkeypatch):
    """Verify missing judgment inputs never generate fallback."""
    monkeypatch.setattr(
        judgment, "call_structured", lambda *a, **k: pytest.fail("Must not generate judgment")
    )
    with pytest.raises(ValueError, match="Missing judicial input"):
        judgment.stage_judge()
    conn = case_db.get_connection()
    assert case_db.get_report(conn, "judge") is None
    conn.close()


def test_rejected_judicial_review_never_publishes_decision(isolated_case, monkeypatch):
    """Verify rejected judicial review never publishes decision."""
    conn = case_db.get_connection()
    for key in (
        "case_manager",
        "prosecution_research",
        "defense_research",
        "pros_r1",
        "def_r1",
        "pros_r2",
        "def_r2",
    ):
        case_db.save_report(conn, key, "Allegation only; investigation stage.")
    conn.close()
    decision = JudicialDecision(
        decision_type="preliminary_assessment",
        procedural_basis="FIR only",
        verdict="Further investigation required.",
        reasoning="The record contains only allegations, with no trial evidence supplied. Individual roles and the alleged weapon require investigation.",
        findings=[
            {
                "act_section": "Not verified",
                "accused_or_group": "Bob",
                "prosecution_position": "Assault alleged",
                "defense_position": "Not tested",
                "finding": "Investigate",
                "record_refs": ["P1"],
                "authority_refs": [],
                "missing_evidence": ["Witness statements"],
            }
        ],
        unresolved_questions=["What did witnesses observe?"],
        sections_applied=[],
        probable_punishment="Not applicable to preliminary assessment",
        confidence=20,
    )
    rejected = JudicialReview(
        acceptable=False,
        material_errors=["Unsupported finding"],
        corrections_required=["Remove unsupported finding"],
        strengths=[],
    )

    def generate(system, user, schema, **kwargs):
        """Return a controlled structured response for the requested schema."""
        return rejected if schema is JudicialReview else decision

    monkeypatch.setattr(judgment, "call_structured", generate)
    with pytest.raises(ValueError, match="unresolved material errors"):
        judgment.stage_judge()
    conn = case_db.get_connection()
    assert case_db.get_report(conn, "judge") is None
    assert case_db.get_artifact(conn, "unapproved_judgment")
    conn.close()


def test_argument_handoff_preserves_report_tails(isolated_case):
    """Verify argument handoff preserves report tails."""
    conn = case_db.get_connection()
    for key in ("case_manager", "defense_consultant", "defense_research", "prosecution_research", "pros_r1"):
        case_db.save_report(conn, key, "x" * 9000 + f"IMPORTANT_END_{key}")
    conn.close()
    context = advocacy._context_builder("defense", 1)
    for key in ("case_manager", "defense_consultant", "defense_research", "prosecution_research", "pros_r1"):
        assert f"IMPORTANT_END_{key}" in context


def test_missing_rebuttal_never_generates_placeholder_submissions(isolated_case, monkeypatch):
    """Failed opposing-point coverage must stop publication rather than invent a reply."""
    conn = case_db.get_connection()
    case_db.save_artifact(conn, "pros_r1", {"submissions": [{"proposition": "Opposing point"}]})
    conn.close()
    result = ArgumentReport(
        submissions=[
            Submission(
                proposition="A record-supported defense point.",
                factual_basis="FIR allegation",
                legal_analysis="Unverified",
                record_refs=["P1"],
                authority_refs=[],
                responds_to=[],
                concession_or_limit="Investigation ongoing",
            )
            for _ in range(3)
        ],
        relief_sought="Further investigation",
    )
    monkeypatch.setattr(advocacy, "call_structured", lambda *args, **kwargs: result)
    with pytest.raises(ValueError, match="Rebuttal coverage"):
        advocacy._argument("def_r1", "system", "context", "pros_r1")
    conn = case_db.get_connection()
    assert case_db.get_report(conn, "def_r1") is None
    assert case_db.get_artifact(conn, "def_r1") is None
    conn.close()
