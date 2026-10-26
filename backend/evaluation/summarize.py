"""Combine measured results and explicit assistant review annotations into a report."""

import argparse
import csv
import importlib.metadata
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from check_outputs import inspect_run
from run import report, write_json


def fraction(facts):
    good = len(facts.get("covered_fact_ids", []))
    total = good + len(facts.get("incorrect_or_overstated_fact_ids", [])) + len(facts.get("missing_fact_ids", []))
    return good, total


def summarize(root):
    inspect_run(root)
    retrieval = json.loads((root / "retrieval.json").read_text())
    cases = json.loads((root / "cases.json").read_text())
    metadata = json.loads((root / "metadata.json").read_text())
    review = json.loads((root / "semantic_review.json").read_text())
    checks = json.loads((root / "automatic_checks.json").read_text())
    dataset = json.loads((root / "cases.dataset.json").read_text())
    by_id = {case["id"]: case for case in review["cases"]}
    if len(by_id) != len(cases["cases"]):
        raise ValueError("Review every executed case before finalizing")
    metadata["report_generated_at"] = datetime.now(timezone.utc).isoformat()
    metadata.setdefault("package_versions_observed_at_report_time", {
        name: importlib.metadata.version(name)
        for name in ("chromadb", "rank-bm25", "openai", "google-genai", "pydantic")
    })
    write_json(root / "metadata.json", metadata)
    report(root, retrieval, cases, metadata)
    lines = ["", "## Source-based factual review", "",
             "Reviewer: the assistant, reading saved outputs and their supplied sources. These are manual source-fidelity assessments, not legal-expert judgments or an automated legal-accuracy score.", "",
             review["fact_scoring_rule"], "",
             "| Case | Extraction facts | Case Manager facts | Judicial facts | Preliminary scope |",
             "| --- | --- | --- | --- | --- |"]
    fact_rows, agent_rows = [], []
    totals = {key: [0, 0] for key in ("clarify_extract", "case_manager", "judge")}
    for case in cases["cases"]:
        annotation = by_id[case["id"]]
        ratios = []
        for stage in totals:
            facts = annotation["facts"][stage]
            good, total = fraction(facts)
            totals[stage][0] += good
            totals[stage][1] += total
            ratios.append(f"{good}/{total}")
            for field, status in (("covered_fact_ids", "pass"), ("incorrect_or_overstated_fact_ids", "fail"),
                                  ("missing_fact_ids", "fail"), ("not_applicable_fact_ids", "not_applicable")):
                for fact_id in facts.get(field, []):
                    fact_rows.append({"case_id": case["id"], "stage": stage, "fact_id": fact_id,
                                      "assessment": status, "reason_category": field,
                                      "evidence": annotation["fact_evidence"][stage], "reviewer": "assistant"})
        lines.append(f"| {case['id']} | {' | '.join(ratios)} | {'pass' if annotation['expected_decision_scope_met'] else 'fail'} |")
        for stage in dataset["default_agent_checks"]:
            agent_rows.append({"case_id": case["id"], "stage": stage,
                               "assessment": annotation["stage_assessment"][stage],
                               "notes": annotation["stage_review"][stage], "reviewer": "assistant"})
    lines += ["", "Applicable fact totals:", ""]
    for stage, (good, total) in totals.items():
        lines.append(f"- `{stage}`: **{good}/{total} ({good / total:.1%})** source-fidelity checks passed.")
    lines += ["", "The unresolved-assessment interpretation (case_003 F4) is excluded from extraction's denominator. Explicit source distinctions are still scored. These percentages apply only to this tiny labeled set and this single run; do not describe them as overall legal accuracy.", "",
              "## Individual agent results", "",
              "These are stage-by-stage audits with actual upstream handoffs. They are not isolated component experiments with independent golden upstream inputs. `needs_review` indicates unresolved source support, applicability, or domain questions rather than a verified pass.", "",
              "| Agent | Structural pass / checked | Source-review pass | Source-review fail | Needs review |",
              "| --- | --- | --- | --- | --- |"]
    for stage in dataset["default_agent_checks"]:
        structural = [item for item in checks if item["stage"] == stage]
        states = Counter(row["assessment"] for row in agent_rows if row["stage"] == stage)
        structural_display = f"{sum(item['assessment'] == 'pass' for item in structural)}/{len(structural)}" if structural else "not a structural check"
        lines.append(f"| {stage} | {structural_display} | {states['pass']} | {states['fail']} | {states['needs_review']} |")
    lines += ["", "Structural checks cover exact original-name presence, extracted Act/section pairs, absence of invented extraction sections, existing reference IDs, declared rebuttal coverage, and decision-type enum. They do not establish semantic support. Per-case judgments and evidence are in `agent_assessments.csv` and `semantic_review.json`; no combined legal-quality percentage is calculated.", "",
              "## Concrete findings", ""]
    for annotation in review["cases"]:
        lines.append(f"### {annotation['id']}")
        lines.append("")
        for item in annotation["findings"]:
            lines.append(f"- **{item['stage']} / {item['severity']}**: {item['finding']} Evidence: “{item['evidence']}”.")
        lines.append("")
    usage = list(csv.DictReader((root / "usage.csv").open()))
    totals_reported = [int(row["total_tokens"]) for row in usage if row["total_tokens"]]
    stage_seconds = sum(case.get("seconds", 0) for case in cases["cases"])
    completed_stages = sum(stage["status"] == "completed" for case in cases["cases"] for stage in case["stages"])
    lines += ["## Execution and usage", "",
              f"- Full cases completed: {sum(case['status'] == 'completed' for case in cases['cases'])}/{len(cases['cases'])}.",
              f"- Simulation stages completed: {completed_stages}/{11 * len(cases['cases'])}.",
              f"- Sum of case execution times: {stage_seconds:.1f} seconds ({stage_seconds / 60:.1f} minutes).",
              f"- Recorded model requests: {len(usage)}; failed request attempts: {sum(row['status'] == 'failed' for row in usage)}.",
              f"- Sum of provider-reported total tokens: {sum(totals_reported):,}; requests missing total-token reporting: {len(usage) - len(totals_reported)}.",
              "- Reasoning tokens are not added again to provider totals. No currency-cost estimate is made.",
              "- Existing backend software suite: 73 tests passed, with dependency deprecation warnings. These mocked-provider tests are separate from the live results above.", "",
              "## Interpretation and next fixes", "",
              "1. BM25 and hybrid both retrieve the target in 15/15 questions. This set does not establish hybrid superiority. Vector misses the theft target once. The questions are deliberately targeted and the sample is small.",
              "2. Normalize supported Act names before simulation exact lookup. The saved case_004 lookup probe shows available BNS 303 text being missed for the full extracted Act name.",
              "3. Validate section references in research plans against the actual Act, and prevent specific unsupported procedural sections from entering consultant strategies.",
              "4. Preserve epistemic distinctions: no document supplied does not mean no action occurred; not seeing a weapon does not mean denying its presence; no act attributed does not mean factual exoneration.",
              "5. Source-ID validation and internal model review are useful but insufficient. Require claim-to-passage support checks, particularly at the final judicial review.",
              "6. Confirm these findings with independently reviewed examples and repeated runs before making broader portfolio claims. No corrective prompt/model changes or before/after improvement experiment were performed during this baseline run.", "",
              "## Saved evidence", "",
              "- `retrieval.csv`, `retrieval.json`, `summary.csv`: measured rankings, hits, and timings.",
              "- `cases.json`, `cases/*/outputs.json`, `cases/*/case.db`: actual stage outputs, reviews, source metadata, and isolated databases.",
              "- `automatic_checks.csv`: structural assessments.",
              "- `fact_review.csv`, `agent_assessments.csv`, `semantic_review.json`: assistant review with evidence and scope.",
              "- `agent_review.csv`: original granular checklist worksheet; unfilled cells remain pending and are not silently counted as passes.",
              "- `usage.csv`: provider-reported request/token details.",
              "- `simulation_statute_lookup_probe.json`: reproduced Act-name lookup failure.",
              "- Dataset snapshots and `metadata.json`: inputs and runtime metadata.", "",
              "Not evaluated: PDF transcription, long records, later procedural stages, independent legal correctness, full-judgment verification, standalone Tavily memo generation, case chat, HTTP/WebSocket behavior, production deployment, and isolated downstream-agent inputs.", ""]
    for name, rows in (("fact_review", fact_rows), ("agent_assessments", agent_rows)):
        with (root / f"{name}.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    with (root / "report.md").open("a", encoding="utf-8") as stream:
        stream.write("\n".join(lines))
    print(f"Final report: {root / 'report.md'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    summarize(parser.parse_args().results)
