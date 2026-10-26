"""Run a small, inspectable evaluation without changing application code or user cases."""

import argparse
import csv
import hashlib
import importlib.metadata
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def error_info(exc):
    """Retain useful provider findings, but never save configured secret values."""
    message = str(exc)
    for key, value in os.environ.items():
        if value and len(value) > 8 and any(part in key.upper() for part in ("TOKEN", "PASSWORD", "API_KEY", "SECRET")):
            message = message.replace(value, "[REDACTED]")
    return {"type": type(exc).__name__, "status_code": getattr(exc, "status_code", None), "message": message[:5000]}


def recall_at_k(ids, expected, k):
    if not expected:
        raise ValueError("Recall requires a nonempty set of labels")
    return len(set(ids[:k]) & set(expected)) / len(set(expected))


def csv_rows(path, rows, fields):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def retrieval_evaluation(dataset, output):
    """Compare production-equivalent searches with equal query expansion and candidate limits."""
    from app.research.search import (
        CANDIDATES_PER_METHOD,
        expand_query,
        load_index,
        reciprocal_rank_fusion,
        tokenize,
    )

    rows = []
    result = {"status": "running", "items": rows, "timing_note": "One sequential measurement per query/method; warm local search, excluding index/model initialization. Hybrid recomputes both searches. No confidence intervals."}
    started = time.perf_counter()
    try:
        collection, bm25, corpus_ids = load_index()
        # Initialize the cached embedding runtime separately from per-question timing.
        warm_started = time.perf_counter()
        collection.query(query_texts=["statute retrieval warmup"], n_results=1)
        result["initialization_and_warmup_seconds"] = round(time.perf_counter() - started, 4)
        result["embedding_warmup_seconds"] = round(time.perf_counter() - warm_started, 4)

        def keyword(query):
            scores = bm25.get_scores(tokenize(query))
            ranked = sorted(zip(corpus_ids, scores), key=lambda value: value[1], reverse=True)
            return [sid for sid, score in ranked if score > 0][:CANDIDATES_PER_METHOD]

        def vector(query):
            return collection.query(query_texts=[query], n_results=CANDIDATES_PER_METHOD)["ids"][0]

        for example in dataset["examples"]:
            query = expand_query(example["question"])
            for method in dataset["methods"]:
                row = {"id": example["id"], "split": example["split"], "method": method,
                       "question": example["question"], "expected_ids": example["relevant_section_ids"]}
                before = time.perf_counter()
                try:
                    if method == "bm25":
                        ids = keyword(query)
                    elif method == "vector":
                        ids = vector(query)
                    else:
                        fused = reciprocal_rank_fusion([vector(query), keyword(query)])
                        ids = [sid for sid, _ in sorted(fused.items(), key=lambda item: item[1], reverse=True)]
                    row.update(status="completed", retrieved_ids=ids[:dataset["top_k"]],
                               recall_at_8=recall_at_k(ids, example["relevant_section_ids"], dataset["top_k"]),
                               first_relevant_rank=next((i for i, sid in enumerate(ids[:dataset["top_k"]], 1) if sid in example["relevant_section_ids"]), None))
                except Exception as exc:
                    row.update(status="failed", recall_at_8=None, error=error_info(exc))
                row["seconds"] = round(time.perf_counter() - before, 6)
                rows.append(row)
                write_json(output / "retrieval.json", result)
                print(f"retrieval {row['id']} {method}: {row['status']} recall={row.get('recall_at_8')}", flush=True)
        result["status"] = "completed" if all(row["status"] == "completed" for row in rows) else "partial"
    except Exception as exc:
        result.update(status="blocked", error=error_info(exc))
        print(f"retrieval blocked: {result['error']['message'][:300]}", flush=True)
    result["total_seconds"] = round(time.perf_counter() - started, 3)
    write_json(output / "retrieval.json", result)
    csv_rows(output / "retrieval.csv", rows,
             ["id", "split", "method", "status", "recall_at_8", "first_relevant_rank", "seconds", "expected_ids", "retrieved_ids"])
    return result


def case_evaluation(dataset, output):
    """Execute actual stages using source-only typed input; retain stage-dependent outputs."""
    from app.config import GEMINI_MODEL, GEMINI_REASONING_MODEL, GCP_LOCATION, GCP_PROJECT_ID
    from app.database.cases import get_all_reports, get_connection, init_schema, save_artifact
    from app.documents.ingestion import ingest_documents
    from app.services.run_context import RunContext, current_run, current_stage
    from app.simulation.pipeline import PIPELINE_STEPS

    summary = {"status": "running", "mode": "typed_narrative_actual_upstream_handoffs",
               "models": {"routine": GEMINI_MODEL, "reasoning": GEMINI_REASONING_MODEL},
               "project": GCP_PROJECT_ID, "region": GCP_LOCATION, "cases": []}
    provider_blocker = None
    for case in dataset["cases"]:
        case_dir = output / "cases" / case["id"]
        case_dir.mkdir(parents=True, exist_ok=True)
        context = RunContext(case_dir / "case.db")
        state = {"id": case["id"], "split": case["split"], "title": case["title"], "status": "running", "stages": []}
        summary["cases"].append(state)
        if provider_blocker:
            state.update(status="blocked", error=provider_blocker, reason="Same provider access blocker; redundant paid requests not attempted.")
            state["stages"] = [{"key": key, "status": "not_run", "reason": "provider_access_blocked"} for _, key, _ in PIPELINE_STEPS]
            write_json(case_dir / "outputs.json", state)
            continue
        token = current_run.set(context)
        started = time.perf_counter()
        try:
            ingest_documents(context.db_path, [], [], [], narrative=case["simulation_input"]["narrative"])
            for title, key, function in PIPELINE_STEPS:
                stage_token = current_stage.set(key)
                stage = {"key": key, "title": title, "status": "running"}
                state["stages"].append(stage)
                before = time.perf_counter()
                try:
                    result = function()
                    if not isinstance(result, dict) or result.get("ok") is not True:
                        raise ValueError(f"Unsuccessful stage: {result}")
                    stage.update(status="completed", result=result)
                    print(f"case {case['id']} {key}: completed", flush=True)
                except Exception as exc:
                    stage.update(status="failed", error=error_info(exc))
                    state.update(status="blocked" if stage["error"]["status_code"] in (401, 403) else "failed", error=stage["error"])
                    if stage["error"]["status_code"] in (401, 403):
                        provider_blocker = stage["error"]
                    print(f"case {case['id']} {key}: {stage['error']['message'][:500]}", flush=True)
                    break
                finally:
                    stage["seconds"] = round(time.perf_counter() - before, 3)
                    current_stage.reset(stage_token)
                    write_json(case_dir / "outputs.json", {**state, "metrics": context.metrics})
            else:
                state["status"] = "completed"
        except Exception as exc:
            state.update(status="failed", error=error_info(exc))
        finally:
            existing = {stage["key"] for stage in state["stages"]}
            state["stages"].extend({"key": key, "status": "not_run", "reason": "upstream_failure"} for _, key, _ in PIPELINE_STEPS if key not in existing)
            state["seconds"] = round(time.perf_counter() - started, 3)
            state["metrics"] = context.metrics
            conn = get_connection(context.db_path)
            try:
                init_schema(conn)
                state["reports"] = get_all_reports(conn)
                save_artifact(conn, "llm_metrics", context.metrics)
                save_artifact(conn, "evaluation_state", state)
            finally:
                conn.close()
                current_run.reset(token)
            write_json(case_dir / "outputs.json", state)
            write_json(output / "cases.json", summary)
    summary["status"] = "completed" if all(case["status"] == "completed" for case in summary["cases"]) else "partial"
    write_json(output / "cases.json", summary)
    # Semantic requirements are deliberately not auto-passed based on substring matches.
    review = []
    for case, state in zip(dataset["cases"], summary["cases"]):
        for stage in state["stages"]:
            for criterion in dataset["default_agent_checks"][stage["key"]] + case["additional_agent_checks"].get(stage["key"], []):
                review.append({"case_id": case["id"], "stage": stage["key"], "execution_status": stage["status"], "criterion": criterion,
                               "assessment": "pending_review" if stage["status"] == "completed" else "not_evaluated", "evidence": ""})
    csv_rows(output / "agent_review.csv", review, ["case_id", "stage", "execution_status", "criterion", "assessment", "evidence"])
    return summary


def report(output, retrieval, cases, metadata):
    lines = ["# Courtroom AI evaluation report", "", f"Run: `{metadata['run_id']}`", f"Started (UTC): {metadata['started_at']}", "",
             "This is a small source-grounded synthetic evaluation, not a legal-accuracy benchmark.", "", "## Retrieval", "",
             f"Execution status: **{retrieval['status']}**.", ""]
    if retrieval.get("error"):
        lines += ["Retrieval could not complete:", "", "```text", retrieval["error"]["message"], "```", ""]
    rows = retrieval.get("items", [])
    if rows:
        lines += ["| Split | Method | Scored / planned | Recall@8 | Top-1 hit rate | MRR@8 | Mean search ms |", "| --- | --- | --- | --- | --- | --- | --- |"]
        aggregates = []
        for split in ("all", "development", "held_out"):
            for method in ("bm25", "vector", "hybrid"):
                planned = [row for row in rows if row["method"] == method and (split == "all" or row["split"] == split)]
                completed = [row for row in planned if row["status"] == "completed"]
                recall = f"{sum(row['recall_at_8'] for row in completed) / len(completed):.1%}" if completed else "N/A"
                elapsed = f"{1000 * sum(row['seconds'] for row in completed) / len(completed):.2f}" if completed else "N/A"
                top1 = sum(row["first_relevant_rank"] == 1 for row in completed) / len(completed) if completed else None
                mrr = sum(1 / row["first_relevant_rank"] if row["first_relevant_rank"] else 0 for row in completed) / len(completed) if completed else None
                top1_display = f"{top1:.1%}" if top1 is not None else "N/A"
                mrr_display = f"{mrr:.4f}" if mrr is not None else "N/A"
                lines.append(f"| {split} | {method} | {len(completed)} / {len(planned)} | {recall} | {top1_display} | {mrr_display} | {elapsed} |")
                aggregates.append({"split": split, "method": method, "scored": len(completed), "planned": len(planned),
                                   "recall_at_8": recall, "top1_hit_rate": top1, "mrr_at_8": mrr, "mean_ms": elapsed})
        csv_rows(output / "summary.csv", aggregates, ["split", "method", "scored", "planned", "recall_at_8", "top1_hit_rate", "mrr_at_8", "mean_ms"])
        lines += ["", retrieval["timing_note"], "", "Each query labels one primary provision. Here Recall@8 is a target-hit measure, not exhaustive real-world legal recall. Held-out examples are locally reserved, not a blind external benchmark.", ""]
        lines += ["Top-1 and reciprocal-rank metrics are supplementary diagnostics calculated from the saved rankings; no prompts or retrieval settings were tuned using these results.", ""]
        for row in rows:
            if row.get("status") == "completed" and row["recall_at_8"] < 1:
                lines += [f"- Miss: `{row['id']}` / `{row['method']}` expected `{row['expected_ids']}`, returned `{row['retrieved_ids']}`."]
        lines += [""]
    lines += ["## Case workflows and agents", "", "| Case | Split | Status | Completed stages | Seconds |", "| --- | --- | --- | --- | --- |"]
    for case in cases.get("cases", []):
        count = sum(stage["status"] == "completed" for stage in case["stages"])
        lines.append(f"| {case['id']} | {case['split']} | {case['status']} | {count} / 11 | {case.get('seconds', 'not run')} |")
    errors = {case.get("error", {}).get("message") for case in cases.get("cases", [])} - {None}
    for error in sorted(errors):
        lines += ["", "Provider/workflow finding:", "", "```text", error, "```"]
    calls = [metric for case in cases.get("cases", []) for metric in case.get("metrics", [])]
    lines += ["", f"Recorded model request attempts: **{len(calls)}**; request outcomes: `{dict(Counter(m['status'] for m in calls))}`.", "",
              "Agent checklist rows are saved in `agent_review.csv`. Completed outputs require semantic review; unexecuted agents are not scored as passes or factual failures. Stage outputs here use actual upstream outputs, not independent golden upstream fixtures.", "",
              "## Scope and reproducibility", "",
              "- No model or prompt changes were made for this run.",
              "- All evaluation databases are isolated under this results directory; user case databases and the case library are not modified.",
              "- Cases use the source-only typed-narrative path; PDF transcription and large records are not evaluated.",
              "- The execution harness calls the production stage functions sequentially. It does not test HTTP, WebSockets, the background scheduler, or deployment.",
              "- No unsupported-claim or legal-correctness score is inferred from successful execution or internal reviewer approval.",
              "- Dataset copies and fingerprints, runtime versions, outputs, rankings, and provider failures are saved alongside this report.", ""]
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["all", "retrieval", "cases"], default="all")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    run_id = now.strftime("%Y%m%dT%H%M%SZ")
    output = args.output or HERE / "results" / run_id
    output.mkdir(parents=True, exist_ok=False)
    from validate_datasets import validate
    validate()
    metadata = {"run_id": run_id, "started_at": now.isoformat(), "python": sys.version,
                "mode": args.mode, "dataset_sha256": {}}
    metadata["package_versions"] = {name: importlib.metadata.version(name) for name in ("chromadb", "rank-bm25", "openai", "google-genai", "pydantic")}
    metadata["application_source_sha256"] = {
        str(path.relative_to(HERE.parent)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((HERE.parent / "app").rglob("*.py"))
    }
    data = {}
    for name in ("retrieval", "cases"):
        raw = (HERE / "datasets" / f"{name}.json").read_bytes()
        metadata["dataset_sha256"][name] = hashlib.sha256(raw).hexdigest()
        (output / f"{name}.dataset.json").write_bytes(raw)
        data[name] = json.loads(raw)
    write_json(output / "metadata.json", metadata)
    print(f"RESULTS_DIR={output}", flush=True)
    retrieval = {"status": "not_run", "items": []}
    cases = {"status": "not_run", "cases": []}
    if args.mode in ("all", "retrieval"):
        retrieval = retrieval_evaluation(data["retrieval"], output)
    report(output, retrieval, cases, metadata)
    if args.mode in ("all", "cases"):
        cases = case_evaluation(data["cases"], output)
    metadata["finished_at"] = datetime.now(timezone.utc).isoformat()
    write_json(output / "metadata.json", metadata)
    report(output, retrieval, cases, metadata)
    print(f"REPORT={output / 'report.md'}", flush=True)


if __name__ == "__main__":
    main()
