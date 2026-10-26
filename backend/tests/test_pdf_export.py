"""Verify polished simulation-only reports and case-scoped download behavior."""

import unicodedata
import uuid

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.database import cases, library
from app.main import app
from app.routes import cases as routes
from app.services.pdf_export import STAGES, build_case_pdf, read_simulation
from app.services.task_registry import registry


@pytest.fixture
def completed_case(tmp_path, monkeypatch):
    """Provide an archived case with all tables populated and private test storage."""
    monkeypatch.setattr(routes, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(library, "DATA_DIR", tmp_path)
    monkeypatch.setattr(library, "LIBRARY_DB", tmp_path / "library.db")
    monkeypatch.setattr(library, "STORAGE_DIR", tmp_path)
    case_id = str(uuid.uuid4())
    path = tmp_path / case_id / "case.db"
    conn = cases.get_connection(path)
    cases.init_schema(conn)
    cases.bulk_insert_pages(
        conn,
        [
            {
                "doc_type": "evidence",
                "doc_name": "source.pdf",
                "page_number": 1,
                "text": "PRIVATE_TRANSCRIPTION_SENTINEL - नमूना बयान",
                "source_hash": "f" * 64,
            }
        ],
    )
    for table, columns in cases.EXTRACTED_COLUMNS.items():
        cases.save_structured(
            conn, table, [{column: f"{table}_{column}_VALUE" for column in columns.split(", ")}]
        )
    cases.save_case_meta(conn, {"parties": {"complainant": "Alice"}, "summary": "Summary <not markup>"})
    for key, title, _ in STAGES:
        cases.save_report(conn, key, f"{title}: STAGE_{key}_SENTINEL")
    cases.save_report(
        conn,
        "prosecution_research",
        "Research findings.\nSOURCE: https://example.org/" + "a" * 150 + "URL_END_SENTINEL",
    )
    cases.save_report(
        conn,
        "judge",
        "Readable assessment fallback",
        {
            "verdict": "Further investigation required.",
            "procedural_basis": "Investigation record only.",
            "reasoning": "Complete legal reasoning. " * 800 + "FINAL_REPORT_SENTINEL",
            "findings": [
                {
                    "act_section": "Supplied provision",
                    "finding": "FINAL_STRUCTURED_SENTINEL",
                    "record_refs": ["P1"],
                    "authority_refs": ["RP1"],
                }
            ],
            "unresolved_questions": ["What did the witness observe?"],
            "confidence": 0,
            "review": {"debug": "PRIVATE_JUDICIAL_REVIEW_SENTINEL"},
        },
    )
    cases.save_artifact(
        conn,
        "review",
        {
            "acceptable": False,
            "score": 0,
            "corrections": [],
            "url": "https://example.org/" + "a" * 150 + "URL_END_SENTINEL",
        },
    )
    conn.execute('CREATE TABLE "legacy notes" ("unexpected field" TEXT, missing TEXT)')
    conn.execute('INSERT INTO "legacy notes" VALUES (?, NULL)', ("LEGACY_FIELD_SENTINEL",))
    conn.commit()
    conn.close()
    library.upsert_case(case_id, title="Export test", status="completed", notes="LIBRARY_NOTE_SENTINEL")
    registry.cases.clear()
    yield case_id, path
    registry.cases.clear()


def test_pdf_contains_simulation_outputs_without_debugging_data(completed_case):
    """Preserve final agent reports and citations while excluding storage and audit internals."""
    case_id, path = completed_case
    extraction, summary, reports = read_simulation(path)
    assert set(reports) == {key for key, _, _ in STAGES}
    result = build_case_pdf(path, library.get_case_meta(case_id))
    with pymupdf.open(stream=result, filetype="pdf") as pdf:
        text = unicodedata.normalize("NFKC", "\n".join(page.get_text() for page in pdf))
        for _, title, _ in STAGES:
            assert title in text
        for marker in ("FINAL_REPORT_SENTINEL", "FINAL_STRUCTURED_SENTINEL", "Source pages", "P1", "RP1"):
            assert marker in text
        for private in (
            "PRIVATE_TRANSCRIPTION_SENTINEL",
            "LEGACY_FIELD_SENTINEL",
            "LIBRARY_NOTE_SENTINEL",
            "PRIVATE_JUDICIAL_REVIEW_SENTINEL",
            "source_hash",
            "structured_json",
            "value_json",
            "created_at",
            "retried_pages",
            case_id,
            "f" * 64,
        ):
            assert private not in text
        assert "a" * 150 + "URL_END_SENTINEL" in text.replace("\n", "")
        assert len(pdf) >= 12
        assert sum(len(page.get_links()) for page in pdf) >= 11
        for index, page in enumerate(pdf, 1):
            assert f"{index:02} / {len(pdf):02}" in page.get_text()
            assert all(block[2] <= page.rect.width - 30 for block in page.get_text("blocks"))
    assert sorted(item.name for item in path.parent.iterdir()) == ["case.db"]


def test_download_completed_case_without_live_registry(completed_case):
    """Completed saved cases remain exportable after a server restart."""
    case_id, _ = completed_case
    response = TestClient(app).get(f"/api/v1/cases/{case_id}/export.pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert f"simulation-{case_id}.pdf" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    assert response.content.startswith(b"%PDF")


@pytest.mark.parametrize("status", ["created", "ingesting", "running", "failed", "terminated"])
def test_incomplete_cases_cannot_be_exported(completed_case, status):
    """The download endpoint only exports finished simulations."""
    case_id, _ = completed_case
    library.upsert_case(case_id, status=status)
    assert TestClient(app).get(f"/api/v1/cases/{case_id}/export.pdf").status_code == 409


def test_final_save_must_finish_before_export(completed_case):
    """A completed event must not expose a PDF before the runner's final save."""
    from app.services.run_context import RunContext

    case_id, path = completed_case
    state = registry.create(case_id)
    state.status = "completed"
    state.runtime = RunContext(path)
    assert TestClient(app).get(f"/api/v1/cases/{case_id}/export.pdf").status_code == 409


def test_missing_case_database_is_not_created(completed_case):
    """An export request never creates a replacement empty database."""
    case_id, path = completed_case
    path.unlink()
    assert TestClient(app).get(f"/api/v1/cases/{case_id}/export.pdf").status_code == 404
    assert not path.exists()


def test_unknown_case_cannot_export_another_case(completed_case):
    """The download is scoped to a known case ID."""
    assert TestClient(app).get(f"/api/v1/cases/{uuid.uuid4()}/export.pdf").status_code == 404


def test_empty_extraction_and_legacy_report_fallback(tmp_path):
    """Missing facts get a plain explanation; old reports remain readable without exposing raw JSON."""
    path = tmp_path / "case.db"
    conn = cases.get_connection(path)
    cases.init_schema(conn)
    conn.execute(
        "INSERT INTO reports (report_type, content, structured_json) VALUES (?, ?, ?)",
        ("case_manager", "LEGACY_APPROVED_REPORT_SENTINEL", "invalid-raw-json"),
    )
    conn.commit()
    conn.close()
    with pymupdf.open(stream=build_case_pdf(path, {}), filetype="pdf") as pdf:
        text = unicodedata.normalize("NFKC", "\n".join(page.get_text() for page in pdf))
    assert "No information identified" in text
    assert "LEGACY_APPROVED_REPORT_SENTINEL" in text
    assert "invalid-raw-json" not in text


def test_argument_ids_are_presented_as_readable_replies(completed_case):
    """Reply labels name the opposing stage rather than exposing internal stage keys."""
    case_id, path = completed_case
    conn = cases.get_connection(path)
    cases.save_report(
        conn,
        "def_r1",
        "Unused raw argument",
        {
            "submissions": [
                {
                    "proposition": "The allegation requires further examination.",
                    "factual_basis": "P1 contains an allegation.",
                    "legal_analysis": "The evidence remains untested.",
                    "record_refs": ["P1"],
                    "authority_refs": ["RP1"],
                    "responds_to": ["pros_r1.1"],
                    "concession_or_limit": "Witness account not supplied.",
                }
            ],
            "relief_sought": "Further investigation",
        },
    )
    conn.close()
    with pymupdf.open(stream=build_case_pdf(path, library.get_case_meta(case_id)), filetype="pdf") as pdf:
        text = unicodedata.normalize("NFKC", "\n".join(page.get_text() for page in pdf))
    assert "Prosecution Opening - point 1" in text
    assert "pros_r1.1" not in text and "responds_to" not in text
