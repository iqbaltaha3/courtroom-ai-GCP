"""A small data pipeline: validate PDFs, transcribe pages, and store text rows."""

import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pymupdf

from app.database.cases import bulk_insert_pages, clear_case_data, get_connection, init_schema
from app.documents.transcription import RENDER_DPI, transcribe_page_bytes
from app.services.concurrency import bounded_results
from app.services.run_context import SimulationCancelled, check_cancelled

# Limit simultaneous requests and retry transient transcription failures.
PDF_WORKERS = max(2, min(4, int(os.getenv("PDF_EXTRACT_WORKERS", "4"))))
PAGE_TRANSCRIBE_RETRIES = max(1, int(os.getenv("PAGE_TRANSCRIBE_RETRIES", "3")))
PAGE_TRANSCRIBE_RETRY_BACKOFF = float(os.getenv("PAGE_TRANSCRIBE_RETRY_BACKOFF", "1.5"))


def validate_pdfs(file_groups):
    """Read document metadata and reject unreadable, protected, or empty PDFs."""
    documents, errors = [], []
    for doc_type, files in file_groups.items():
        for name, data in files:
            try:
                with pymupdf.open(stream=data, filetype="pdf") as pdf:
                    if pdf.needs_pass:
                        raise ValueError("PDF is password-protected")
                    if pdf.page_count == 0:
                        raise ValueError("PDF has no pages")
                    documents.append(
                        {
                            "doc_type": doc_type,
                            "name": name,
                            "data": data,
                            "page_count": pdf.page_count,
                            "source_hash": hashlib.sha256(data).hexdigest(),
                        }
                    )
            except Exception as exc:
                errors.append(f"{name}: {exc}")
    return documents, errors


def transcribe_page(document, page_index):
    """Render one page in memory; return text and source columns ready for SQLite."""
    check_cancelled()
    with pymupdf.open(stream=document["data"], filetype="pdf") as pdf:
        image = pdf.load_page(page_index).get_pixmap(dpi=RENDER_DPI).tobytes("png")
    label = f"{document['name']} p{page_index + 1}"
    for attempt in range(PAGE_TRANSCRIBE_RETRIES):
        check_cancelled()
        try:
            text = transcribe_page_bytes(image, page_label=label)
            return {
                "doc_type": document["doc_type"],
                "doc_name": document["name"],
                "page_number": page_index + 1,
                "text": text,
                "source_hash": document["source_hash"],
            }, attempt > 0
        except SimulationCancelled:
            raise
        except Exception:
            if attempt == PAGE_TRANSCRIBE_RETRIES - 1:
                raise
            time.sleep(PAGE_TRANSCRIBE_RETRY_BACKOFF * (attempt + 1))


def ingest_documents(
    db_path, allegation_files, petition_files, evidence_files, narrative="", on_progress=None
):
    """Save PDFs and optional typed facts into the explicitly selected case database.

    Input: PDF bytes grouped by role, plus optional typed facts.
    Output: one pages-table row per PDF page and one optional narrative row.
    """
    check_cancelled()
    groups = {"allegation": allegation_files, "petition": petition_files, "evidence": evidence_files}
    documents, errors = validate_pdfs(groups)
    total_pages = sum(doc["page_count"] for doc in documents) + bool(narrative.strip())
    stats = {
        "allegation": 0,
        "petition": 0,
        "evidence": 0,
        "transcribed_pages": 0,
        "retried_pages": 0,
        "errors": errors,
    }
    # Do not spend model calls or replace existing records when any input is invalid.
    if errors or not total_pages:
        return {"ok": False, "stats": stats, "total_pages": 0}

    completed = 0

    def report_progress(label):
        """Count saved rows; this runs on the coordinator thread, not page workers."""
        nonlocal completed
        completed += 1
        if on_progress:
            on_progress(completed, total_pages, label)

    with closing(get_connection(db_path)) as conn:
        init_schema(conn)
        check_cancelled()
        clear_case_data(conn)
        # Workers transcribe independently; the coordinator saves results in source order.
        for document in documents:
            with ThreadPoolExecutor(max_workers=PDF_WORKERS) as pool:
                results = bounded_results(
                    pool,
                    lambda index: transcribe_page(document, index),
                    range(document["page_count"]),
                    PDF_WORKERS * 2,
                )
                for row, retried in results:
                    check_cancelled()
                    bulk_insert_pages(conn, [row])
                    stats[row["doc_type"]] += 1
                    stats["transcribed_pages"] += 1
                    stats["retried_pages"] += int(retried)
                    report_progress(f"{row['doc_name']} p{row['page_number']}")
        # Typed facts use the same page table, but need no rendering or model call.
        if narrative.strip():
            check_cancelled()
            bulk_insert_pages(
                conn,
                [
                    {
                        "doc_type": "allegation",
                        "doc_name": "typed_narrative.txt",
                        "page_number": 1,
                        "text": narrative.strip(),
                        "source_hash": "",
                    }
                ],
            )
            report_progress("typed_narrative.txt")
    return {"ok": True, "stats": stats, "total_pages": completed}
