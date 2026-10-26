"""Check the ingestion data flow without credentials or live model calls."""

import sqlite3

import pymupdf
import pytest

from app.database import cases
from app.documents import ingestion
from app.services.run_context import RunContext, current_run


def make_pdf():
    """Return one tiny valid source document for transcription tests."""
    with pymupdf.open() as pdf:
        pdf.new_page(width=10, height=10)
        return pdf.tobytes()


def test_explicit_destination_overrides_another_active_case(tmp_path, monkeypatch):
    """Ingestion writes to its explicit destination, even if another run is active."""
    token = current_run.set(RunContext(tmp_path / "other" / "case.db"))
    monkeypatch.setattr(ingestion, "transcribe_page_bytes", lambda *args, **kwargs: "Complete source text.")
    target = tmp_path / "selected" / "case.db"
    progress = []
    try:
        result = ingestion.ingest_documents(
            target,
            [("source.pdf", make_pdf())],
            [],
            [],
            narrative="Typed facts",
            on_progress=lambda *values: progress.append(values),
        )
    finally:
        current_run.reset(token)
    assert result["ok"] and result["total_pages"] == 2
    assert not (tmp_path / "other").exists()
    with sqlite3.connect(target) as conn:
        rows = conn.execute("SELECT doc_name, page_number, text FROM pages ORDER BY id").fetchall()
        assert rows == [("source.pdf", 1, "Complete source text."), ("typed_narrative.txt", 1, "Typed facts")]
        assert conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
    assert sorted(path.name for path in target.parent.iterdir()) == ["case.db"]
    assert progress[-1] == (2, 2, "typed_narrative.txt")


def test_typed_facts_need_no_pdf_or_model(tmp_path, monkeypatch):
    """Typed input follows the same storage path with no transcription request."""
    monkeypatch.setattr(
        ingestion, "transcribe_page_bytes", lambda *args, **kwargs: pytest.fail("No model needed")
    )
    target = tmp_path / "case.db"
    result = ingestion.ingest_documents(target, [], [], [], narrative="  Supplied facts.  ")
    assert result["ok"] and result["total_pages"] == 1
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT text FROM pages").fetchone()[0] == "Supplied facts."


def test_invalid_pdf_preserves_previous_case_records(tmp_path, monkeypatch):
    """Reject invalid inputs before clearing an existing case or making model requests."""
    target = tmp_path / "case.db"
    ingestion.ingest_documents(target, [], [], [], narrative="Previous record")
    monkeypatch.setattr(
        ingestion, "transcribe_page_bytes", lambda *args, **kwargs: pytest.fail("No model needed")
    )
    result = ingestion.ingest_documents(target, [("broken.pdf", b"invalid")], [], [])
    assert not result["ok"] and result["stats"]["errors"]
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT text FROM pages").fetchone()[0] == "Previous record"


def test_missing_case_destination_is_an_error():
    """Database calls must not silently create a shared default case database."""
    token = current_run.set(None)
    try:
        with pytest.raises(ValueError, match="case database path"):
            cases.get_connection()
    finally:
        current_run.reset(token)


def test_transcription_failure_stops_ingestion(tmp_path, monkeypatch):
    """Exhausted transcription errors propagate instead of producing successful empty records."""
    monkeypatch.setattr(ingestion, "PAGE_TRANSCRIBE_RETRIES", 1)

    def fail(*args, **kwargs):
        """Represent a failed page transcription request."""
        raise ValueError("Transcription failed")

    monkeypatch.setattr(ingestion, "transcribe_page_bytes", fail)
    target = tmp_path / "case.db"
    with pytest.raises(ValueError, match="Transcription failed"):
        ingestion.ingest_documents(target, [("source.pdf", make_pdf())], [], [])
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0] == 0
