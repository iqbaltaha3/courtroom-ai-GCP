"""Verify factual-review repairs receive the rejected draft and retain useful failure details."""

import pytest

from app.database import cases
from app.services.run_context import RunContext, current_run
from app.simulation import case_manager
from app.simulation.models import CaseManagerReport, JudicialReview


@pytest.fixture
def case_record(tmp_path, monkeypatch):
    """Provide a small original record without calling live research or models."""
    token = current_run.set(RunContext(tmp_path / "case.db"))
    cases.init_schema()
    conn = cases.get_connection()
    cases.bulk_insert_pages(
        conn,
        [
            {
                "doc_type": "allegation",
                "doc_name": "fir.pdf",
                "page_number": 1,
                "text": "Alice alleges an assault by Bob. The occurrence time is not stated.",
                "source_hash": "test",
            }
        ],
    )
    monkeypatch.setattr(case_manager, "review_original_batches", lambda *args: [])
    yield conn
    conn.close()
    current_run.reset(token)


def report(narrative):
    """Build a typed case report suitable for testing the review handoff."""
    return CaseManagerReport(
        case_caption="Alice and Bob",
        parties={"complainant": "Alice"},
        court_and_stage="Investigation",
        neutral_factual_narrative=narrative,
        procedural_stage="investigation",
        decision_scope="Preliminary assessment",
        alleged_offences=[],
        evidence_matrix=[],
        witness_matrix=[],
        timeline_summary=[],
        key_gaps_and_inconsistencies=[],
        document_inventory=["fir.pdf"],
    )


def test_revision_receives_previous_draft_and_original_source(case_record, monkeypatch):
    """The correction request must contain the original source, draft, and factual review."""
    rejected = report(
        "Alice alleges an assault by Bob at 10 pm. This is an alleged occurrence described in the FIR."
    )
    corrected = report(
        "Alice alleges an assault by Bob. The occurrence time is not stated in the supplied original record."
    )
    calls = []

    def generate(system, user, schema, **kwargs):
        """Reject the invented time, then approve the corrected draft."""
        if schema is CaseManagerReport:
            assert "model" not in kwargs  # Drafting uses the routine Flash model.
            calls.append(user)
            return rejected if len(calls) == 1 else corrected
        assert kwargs["model"] == case_manager.GEMINI_REASONING_MODEL
        return JudicialReview(
            acceptable=len(calls) > 1,
            material_errors=["The occurrence time is unsupported."] if len(calls) == 1 else [],
            corrections_required=["Remove the invented 10 pm time."] if len(calls) == 1 else [],
            strengths=[],
        )

    monkeypatch.setattr(case_manager, "call_structured", generate)
    assert case_manager.stage_case_manager()["ok"]
    assert "PREVIOUS DRAFT TO CORRECT" in calls[1]
    assert "10 pm" in calls[1] and "Remove the invented 10 pm time" in calls[1]
    assert "The occurrence time is not stated" in calls[1]
    assert "10 pm" not in cases.get_report(case_record, "case_manager")


def test_exhausted_reviews_explain_failure_without_publishing(case_record, monkeypatch):
    """After two revisions, save diagnostics and report the exact unresolved factual problem."""
    draft = report(
        "Alice alleges an assault by Bob at 10 pm. The report claims that this occurrence time is established."
    )

    def generate(system, user, schema, **kwargs):
        """Always reject the unsupported occurrence time."""
        if schema is CaseManagerReport:
            return draft
        return JudicialReview(
            acceptable=False,
            material_errors=["Unsupported occurrence time"],
            corrections_required=["Do not infer time from registration time"],
            strengths=[],
        )

    monkeypatch.setattr(case_manager, "call_structured", generate)
    with pytest.raises(ValueError, match="Review findings: Unsupported occurrence time"):
        case_manager.stage_case_manager()
    assert cases.get_report(case_record, "case_manager") is None
    for attempt in (1, 2, 3):
        assert cases.get_artifact(case_record, f"case_manager_audit_{attempt}")["acceptable"] is False
        assert cases.get_artifact(case_record, f"case_manager_draft_{attempt}")
