"""Explain recorded stage actions without generating another AI answer."""

from app.database.cases import get_artifact
from app.services.usage import summarize_usage
from app.simulation.pipeline import PIPELINE_STEPS


def agent_details(conn, metrics, statuses):
    """Combine measured requests with a short description of each stage's workflow."""
    details = {}
    for key in ["ingestion"] + [key for _, key, _ in PIPELINE_STEPS]:
        status = statuses.get(key, "pending")
        usage = summarize_usage(metrics, key)
        steps = []
        if status == "done":
            if key == "ingestion":
                steps = ["Transcribed supplied PDF pages and stored their text. Page images were temporary."]
            elif key == "clarify_extract":
                coverage = get_artifact(conn, "extraction_coverage", {})
                steps = [
                    f"Processed {coverage.get('pages', 'the supplied')} pages in batches.",
                    "Extracted laws, witnesses, evidence, people and events into structured tables.",
                ]
            elif key == "case_manager":
                steps = ["Read extracted facts and the page-cited record; prepared a neutral case summary."]
            elif key.endswith("consultant"):
                steps = [
                    "Read the neutral summary, document record and statute material.",
                    "Prepared this side's strategy, strengths and limitations.",
                ]
            elif key.endswith("research"):
                side = key.removesuffix("_research")
                sources = get_artifact(conn, f"{side}_sources", {})
                steps = [
                    "Used the case summary and strategy to prepare legal research questions.",
                    f"Retained {len(sources.get('sources', []))} research sources and wrote a report with limitations.",
                ]
                if not sources.get("sources"):
                    steps.append(
                        "No live sources were retained; the report must be read with that limitation."
                    )
            elif key == "judge":
                steps = [
                    "Read the record, prior reports and research passages; assessed both sides.",
                    "Checked citation IDs and whether the decision fits the documented procedural stage.",
                ]
            else:
                steps = [
                    "Read the case record, strategies, research and available earlier arguments.",
                    "Checked page and authority IDs, factual page references and coverage of opposing points.",
                ]
        elif status == "running":
            steps = ["This stage is still running. Its workflow summary will appear when it finishes."]
        elif status in ("error", "failed"):
            steps = ["This stage stopped before completing. Recorded requests include unsuccessful attempts."]
        else:
            steps = ["This stage has not run yet."]
        if key in ("case_manager", "judge"):
            prefix = "case_manager_audit" if key == "case_manager" else "judicial_review"
            reviews = [get_artifact(conn, f"{prefix}_{n}", {}) for n in (1, 2, 3)]
            reviews = [r for r in reviews if r]
            if reviews:
                rejected = sum(
                    not r.get("acceptable") or bool(r.get("material_errors") or r.get("corrections_required"))
                    for r in reviews
                )
                steps.append(f"Recorded {len(reviews)} factual review(s); {rejected} requested corrections.")
        details[key] = {**usage, "available": bool(usage["calls"]), "steps": steps}
    return details
