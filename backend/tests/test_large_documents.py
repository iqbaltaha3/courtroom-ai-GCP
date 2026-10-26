import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from pathlib import Path

import pytest

from app.database import cases as case_db
from app.documents import context as records
from app.documents import ingestion
from app.services.concurrency import bounded_results
from app.services.run_context import RunContext, current_run


@pytest.fixture
def case(tmp_path):
    """Provide an isolated database for large-document tests."""
    token = current_run.set(RunContext(tmp_path / "case.db"))
    case_db.init_schema()
    conn = case_db.get_connection()
    yield conn
    conn.close()
    current_run.reset(token)


def insert(conn, text, page=1):
    """Insert a source page into the test case."""
    case_db.bulk_insert_pages(
        conn,
        [
            {
                "doc_type": "allegation",
                "doc_name": "large.pdf",
                "page_number": page,
                "text": text,
                "source_hash": "test",
            }
        ],
    )


def test_dense_page_chunks_preserve_every_character_and_reference():
    """Verify dense page chunks preserve every character and reference."""
    text = "ABCD" * 10000 + "DECISIVE_FINAL_FACT"
    chunks = list(
        records.record_chunks(
            [
                {
                    "id": 7,
                    "doc_name": "dense.pdf",
                    "page_number": 1,
                    "doc_type": "evidence",
                    "text": text,
                }
            ],
            max_chars=4000,
        )
    )
    assert all(len(source) <= 4000 and refs == {"P7"} for source, refs in chunks)
    assert "".join(source.split("\n", 1)[1] for source, _ in chunks) == text


def test_small_record_uses_original_without_model(case, monkeypatch):
    """Verify small record uses original without model."""
    insert(case, "The original ending matters.")
    monkeypatch.setattr(records, "call_structured", lambda *a, **k: pytest.fail("No model needed"))
    assert records.record_context(case).endswith("The original ending matters.")


def test_large_record_has_full_coverage_cache_and_invalidation(case, monkeypatch):
    """Verify large record has full coverage cache and invalidation."""
    monkeypatch.setattr(records, "RECORD_CONTEXT_CHARS", 12000)
    calls = []

    def summarize(source, refs):
        """Record digest inputs and return a predictable page-cited summary."""
        calls.append((source, refs))
        return "Reviewed facts " + " ".join(sorted(refs)) + " END_FACT"

    monkeypatch.setattr(records, "_digest", summarize)
    for i in range(1, 126):
        insert(case, "x" * 4000 + f" END_FACT_{i}", i)
    context = records.record_context(case)
    assert "NOT original" in context
    assert {ref for _, refs in calls for ref in refs} == {f"P{i}" for i in range(1, 126)}
    assert "END_FACT_125" in calls[-1][0]
    assert len(context) < 13000
    before = len(calls)
    assert records.record_context(case) == context
    assert len(calls) == before
    insert(case, "A newly supplied decisive fact.", 126)
    records.record_context(case)
    assert len(calls) > before
    assert len(case_db.get_all_pages(case)) == 126


def test_digest_rejected_review_stops_analysis(monkeypatch):
    """Verify digest rejected review stops analysis."""

    def generate(system, user, schema, **kwargs):
        """Return a controlled structured response for the requested schema."""
        if schema is records.RecordDigest:
            return records.RecordDigest(text="Alleged conduct P1.", page_refs=["P1"])
        return records.DigestReview(acceptable=False, corrections=["Missing adverse fact"])

    monkeypatch.setattr(records, "call_structured", generate)
    with pytest.raises(ValueError, match="factual review"):
        records._digest("[P1] Original adverse fact.", {"P1"})


def test_digest_missing_page_cannot_pass_coverage(monkeypatch):
    """Verify digest missing page cannot pass coverage."""
    monkeypatch.setattr(
        records, "call_structured", lambda *a, **k: records.RecordDigest(text="P1 only.", page_refs=["P1"])
    )
    with pytest.raises(ValueError, match="coverage"):
        records._digest("[P1] [P2]", {"P1", "P2"})


def test_large_report_review_reads_original_tail_and_reports_corrections(case, monkeypatch):
    """Verify large report review reads original tail and reports corrections."""
    monkeypatch.setattr(records, "RECORD_CONTEXT_CHARS", 12000)
    insert(case, "x" * 14000 + " ORIGINAL_ADVERSE_TAIL")
    calls = []

    def review(system, user, schema, **kwargs):
        """Capture original-page review input and return a rejection."""
        calls.append(user)
        return records.DigestReview(
            acceptable=False, corrections=["P1: Original adverse evidence was omitted."]
        )

    monkeypatch.setattr(records, "call_structured", review)
    errors = records.review_original_batches(case, "Proposed report.", "test_audit")
    assert errors and "ORIGINAL_ADVERSE_TAIL" in calls[-1]
    assert case_db.get_artifact(case, "test_audit")[0]["page_refs"] == ["P1"]


def test_hierarchical_reduction_is_bounded_and_covers_tail(case, monkeypatch):
    """Verify hierarchical reduction is bounded and covers tail."""
    monkeypatch.setattr(records, "RECORD_CONTEXT_CHARS", 12000)
    calls = []

    def summarize(source, refs):
        """Record digest inputs and return a predictable page-cited summary."""
        calls.append((len(source), refs))
        return "Facts " + " ".join(sorted(refs)) + " " + "z" * 5000

    monkeypatch.setattr(records, "_digest", summarize)
    for i in range(1, 41):
        insert(case, "x" * 6000, i)
    result = records.record_context(case)
    assert len(result) < 13000
    assert all(size <= records.RECORD_BATCH_CHARS for size, _ in calls)
    assert "P40" in result
    assert case_db.get_artifact(case, "large_record_context")["consolidation_levels"] > 0


def test_bounded_pool_does_not_consume_all_items_and_copies_context():
    """Verify bounded pool does not consume all items and copies context."""
    consumed = []
    marker = ContextVar("marker", default="missing")
    token = marker.set("case-context")

    def items():
        """Track which inputs the bounded worker pool consumes."""
        for i in range(100):
            consumed.append(i)
            yield i

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = bounded_results(pool, lambda i: (i, marker.get()), items(), 4)
            assert next(results) == (0, "case-context")
            assert len(consumed) == 4
            assert list(results)[-1] == (99, "case-context")
    finally:
        marker.reset(token)


def test_121_page_pdf_ingests_all_pages(case, monkeypatch):
    """Verify 121 page pdf ingests all pages."""
    import pymupdf

    document = pymupdf.open()
    for _ in range(121):
        document.new_page(width=10, height=10)
    data = document.tobytes()
    document.close()
    monkeypatch.setattr(ingestion, "transcribe_page_bytes", lambda *a, **k: "Complete page transcription.")
    result = ingestion.ingest_documents(current_run.get().db_path, [("large.pdf", data)], [], [])
    assert result["ok"] and result["total_pages"] == 121
    assert len(case_db.get_all_pages(case)) == 121
    # Only text and source metadata persist; page images remain transient.
    case_dir = Path(case.execute("PRAGMA database_list").fetchone()[2]).parent
    assert not (case_dir / "page_images").exists()
    assert not list(case_dir.rglob("*.png"))
    assert case_db.get_artifact(case, "page_images") is None
    assert not (case_dir / "case.txt").exists()
    assert case_db.get_artifact(case, "case_text_path") is None
    assert all(page["text"] == "Complete page transcription." for page in case_db.get_all_pages(case))


def test_oversized_upload_preserves_existing_file_and_removes_temporary(tmp_path, monkeypatch):
    """Verify oversized upload preserves existing file and removes temporary."""
    from io import BytesIO
    from types import SimpleNamespace

    from fastapi import HTTPException, UploadFile

    from app.routes import cases

    monkeypatch.setattr(cases, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(cases, "MAX_BYTES", 10)
    monkeypatch.setattr(cases.registry, "get", lambda _: SimpleNamespace(status="created", uploads={}))
    dest = tmp_path / "test" / "uploads" / "allegation"
    dest.mkdir(parents=True)
    path = dest / "same.pdf"
    path.write_bytes(b"original")
    upload = UploadFile(filename="same.pdf", file=BytesIO(b"x" * 11))
    with pytest.raises(HTTPException) as error:
        asyncio.run(cases.upload_documents("test", allegation=[upload], petition=[], evidence=[]))
    assert error.value.status_code == 400
    assert path.read_bytes() == b"original"
    assert list(dest.iterdir()) == [path]


def test_upload_larger_than_old_cap_streams_in_megabyte_chunks(tmp_path, monkeypatch):
    """Verify upload larger than old cap streams in megabyte chunks."""
    from types import SimpleNamespace

    from app.routes import cases

    monkeypatch.setattr(cases, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(cases, "MAX_BYTES", 200 * 1024 * 1024)
    state = SimpleNamespace(status="created", uploads={})
    monkeypatch.setattr(cases.registry, "get", lambda _: state)

    class Upload:
        """Test data or fake provider object for upload."""

        filename = "large.pdf"
        remaining = 26

        async def read(self, size):
            """Return a bounded synthetic upload chunk."""
            assert size == 1024 * 1024
            if not self.remaining:
                return b""
            self.remaining -= 1
            return b"x" * size

    result = asyncio.run(cases.upload_documents("test", allegation=[Upload()], petition=[], evidence=[]))
    assert result["ok"]
    assert (tmp_path / "test/uploads/allegation/large.pdf").stat().st_size == 26 * 1024 * 1024


@pytest.fixture
def chunk_upload(tmp_path, monkeypatch):
    """Provide a chunk-upload sender and temporary case storage."""
    import uuid
    from io import BytesIO
    from types import SimpleNamespace

    from fastapi import UploadFile

    from app.routes import cases

    state = SimpleNamespace(status="created", uploads={"allegation": [], "petition": [], "evidence": []})
    monkeypatch.setattr(cases, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(cases.registry, "get", lambda _: state)
    upload_id = str(uuid.uuid4())

    def send(data, offset, total_size, **changes):
        """Submit one upload chunk with optional metadata changes."""
        arguments = dict(
            case_id="test",
            chunk=UploadFile(filename="part", file=BytesIO(data)),
            upload_id=upload_id,
            kind="evidence",
            filename="large.pdf",
            offset=offset,
            total_size=total_size,
        )
        arguments.update(changes)
        return asyncio.run(cases.upload_document_chunk(**arguments))

    return cases, state, send, tmp_path


def test_chunk_upload_assembles_before_registering_and_preserves_original(chunk_upload):
    """Verify chunk upload assembles before registering and preserves original."""
    cases, state, send, directory = chunk_upload
    dest = directory / "test/uploads/evidence/large.pdf"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"old")
    assert send(b"abc", 0, 6) == {"ok": True, "received": 3, "complete": False}
    assert state.uploads["evidence"] == []
    assert dest.read_bytes() == b"old"
    assert send(b"def", 3, 6)["complete"]
    assert dest.read_bytes() == b"abcdef"
    assert state.uploads["evidence"] == ["large.pdf"]
    assert list((directory / "test/upload_chunks").iterdir()) == []


def test_chunk_upload_rejects_bad_offsets_and_changed_metadata(chunk_upload):
    """Verify chunk upload rejects bad offsets and changed metadata."""
    from fastapi import HTTPException

    _, state, send, _ = chunk_upload
    send(b"abc", 0, 6)
    for offset, changes in [(0, {}), (2, {}), (3, {"filename": "different.pdf"})]:
        with pytest.raises(HTTPException) as error:
            send(b"def", offset, 6, **changes)
        assert error.value.status_code == 409
    assert state.uploads["evidence"] == []


def test_chunk_upload_rejects_oversized_file_and_chunk(chunk_upload, monkeypatch):
    """Verify chunk upload rejects oversized file and chunk."""
    from fastapi import HTTPException

    cases, _, send, _ = chunk_upload
    monkeypatch.setattr(cases, "MAX_BYTES", 20)
    monkeypatch.setattr(cases, "UPLOAD_CHUNK_BYTES", 4)
    with pytest.raises(HTTPException, match="exceeds"):
        send(b"abcd", 0, 21)
    with pytest.raises(HTTPException, match="Chunk exceeds"):
        send(b"abcde", 0, 10)


def test_chunk_upload_above_cloud_run_single_request_limit(chunk_upload):
    """Verify chunk upload above cloud run single request limit."""
    cases, state, send, directory = chunk_upload
    total = 33 * 1024 * 1024
    offset = 0
    while offset < total:
        size = min(cases.UPLOAD_CHUNK_BYTES, total - offset)
        result = send(b"x" * size, offset, total)
        assert result["received"] == offset + size
        offset += size
    assert result["complete"] and state.uploads["evidence"] == ["large.pdf"]
    assert (directory / "test/uploads/evidence/large.pdf").stat().st_size == total


def test_new_upload_selection_cleans_abandoned_chunks(chunk_upload):
    """Verify new upload selection cleans abandoned chunks."""
    cases, state, send, directory = chunk_upload
    send(b"abc", 0, 6)
    assert len(list((directory / "test/upload_chunks").iterdir())) == 2
    asyncio.run(cases.upload_documents("test", allegation=[], petition=[], evidence=[]))
    assert list((directory / "test/upload_chunks").iterdir()) == []
    assert state.uploads == {"allegation": [], "petition": [], "evidence": []}
