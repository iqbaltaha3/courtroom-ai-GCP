"""Run the production orchestration on a PDF in an isolated case directory."""

import argparse
import asyncio
import json
import logging
import time
import uuid
from pathlib import Path

from app.config import STORAGE_DIR
from app.database.cases import get_all_reports, get_artifact, get_connection
from app.services.runner import run_pipeline
from app.services.task_registry import registry


async def evaluate(pdf: Path, case_id: str):
    """Run the simulation on a PDF and save its coverage, reviews, and reports."""
    state = registry.create(case_id)
    queue = asyncio.Queue()
    state.subscribers.add(queue)
    started = time.monotonic()

    async def monitor():
        """Print progress events without duplicating full stage reports."""
        while True:
            event = await queue.get()
            if event.get("type") != "stage_report":
                print(json.dumps({"elapsed": round(time.monotonic() - started, 1), **event}), flush=True)

    monitor_task = asyncio.create_task(monitor())
    try:
        await run_pipeline(case_id, None, [(pdf.name, pdf.read_bytes())], [], [])
    finally:
        monitor_task.cancel()
        await asyncio.gather(monitor_task, return_exceptions=True)
    conn = get_connection(STORAGE_DIR / case_id / "case.db")
    result = {
        "case_id": case_id,
        "status": state.status,
        "error": state.error,
        "seconds": round(time.monotonic() - started, 2),
        "coverage": get_artifact(conn, "extraction_coverage", {}),
        "reports": get_all_reports(conn),
        "metrics": get_artifact(conn, "llm_metrics", []),
        "reviews": [get_artifact(conn, f"judicial_review_{i}", {}) for i in (1, 2)],
    }
    conn.close()
    target = STORAGE_DIR / case_id / "evaluation.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps({k: v for k, v in result.items() if k not in ("reports", "metrics", "reviews")}),
        flush=True,
    )
    print(f"Evaluation saved: {target}", flush=True)
    if state.status != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--case-id", default=None)
    args = parser.parse_args()
    asyncio.run(evaluate(args.pdf, args.case_id or str(uuid.uuid4())))
