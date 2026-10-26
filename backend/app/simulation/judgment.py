"""Produce and review a stage-appropriate judicial assessment."""

from __future__ import annotations

import json
from typing import Any

from app.ai.llm import call_structured
from app.config import GEMINI_REASONING_MODEL
from app.database.cases import get_all_reports, get_artifact, get_connection, save_artifact, save_report
from app.documents.context import record_context, review_original_batches
from app.services.run_context import current_run
from app.simulation.models import (
    JudicialDecision,
    JudicialReview,
    validate_references,
)
from app.simulation.prompts import PIPELINE
from app.simulation.shared import _reference_ids


def stage_judge() -> dict[str, Any]:
    """Review both sides and publish an assessment only after independent quality checks."""
    conn = get_connection()
    try:
        reports = get_all_reports(conn)
        for key in (
            "case_manager",
            "prosecution_research",
            "defense_research",
            "pros_r1",
            "def_r1",
            "pros_r2",
            "def_r2",
        ):
            if not reports.get(key):
                raise ValueError(f"Missing judicial input: {key}")
        context = current_run.get()
        judge_name = context.judge if context else None
        brief = get_artifact(conn, "compact_case_brief", {})
        record_ids, authority_ids = _reference_ids(conn)
        sources = {
            side: get_artifact(conn, f"{side}_sources", {}) for side in ("prosecution", "defense", "statutes")
        }
        user = (
            record_context(conn)
            + "\n\nCOMPLETE AGENT REPORTS:\n"
            + json.dumps(reports, ensure_ascii=False)
            + "\nSOURCE-SUPPORTED PASSAGES:\n"
            + json.dumps(sources, ensure_ascii=False)
        )
        system = (
            PIPELINE
            + """
You are the neutral judicial decision agent. First determine what proceeding the documents support.
For an FIR/investigation-only record issue a preliminary assessment, not a trial verdict or sentence.
Do not invent an application, hearing, charge sheet or committal order. Identify jurisdictional uncertainty.
Evaluate every alleged offence and distinguish accused with specific acts from those accused collectively.
For each finding address BOTH sides' strongest arguments, identify the actual applicable legal test,
cite P record references and RP/RD/ST sources, explain why you accept or reject the point,
and state what evidence would change the assessment. A lack of supplied corroboration is not proof of falsity.
Do not treat absence of injury as necessarily negating intent or an attempt.
List unresolved questions and concrete investigative priorities where justified by the record.
Confidence means record sufficiency, not probability of guilt. Use no judge personality or conviction bias.
Keep reasoning within 1500 words, in addition to concise per-offence findings.
"""
        )
        for attempt in range(2):
            result = call_structured(
                system, user, JudicialDecision, max_tokens=12000, model=GEMINI_REASONING_MODEL
            )
            validate_references(result, record_ids, authority_ids)
            if (
                brief.get("procedural_stage", "").lower() in ("investigation", "unknown")
                and result.decision_type != "preliminary_assessment"
            ):
                raise ValueError("Judicial decision exceeds the documented procedural stage")
            review = call_structured(
                PIPELINE + "\nYou are an independent judicial quality reviewer, not an advocate. "
                "Check the proposed decision against original pages and source-supported passages. "
                "Flag ONLY material errors: unsupported fact/authority, ignored decisive opposing argument, "
                "wrong act or legal test, failure to distinguish accused, FIR treated as trial proof, "
                "invented procedural power or disposition. Check all alleged offences are addressed. "
                "Search grounding is not independent verification; verify that attributed claims match "
                "supplied passages. Style preferences are not errors. Return acceptable only if no material errors remain.",
                user + "\nPROPOSED DECISION:\n" + result.model_dump_json(),
                JudicialReview,
                max_tokens=5000,
                model=GEMINI_REASONING_MODEL,
            )
            if review.acceptable and not review.material_errors and not review.corrections_required:
                corrections = review_original_batches(
                    conn, result.model_dump_json(), f"judicial_source_audit_{attempt + 1}"
                )
                if corrections:
                    review.acceptable = False
                    review.corrections_required.extend(corrections)
            save_artifact(conn, f"judicial_review_{attempt + 1}", review.model_dump())
            if review.acceptable and not review.material_errors and not review.corrections_required:
                break
            if attempt:
                save_artifact(conn, "unapproved_judgment", result.model_dump())
                raise ValueError(
                    "Judicial review found unresolved material errors; no final judgment published."
                )
            user += "\nRevise your draft to resolve these review findings:\n" + review.model_dump_json()
        findings = "\n\n".join(
            f"{i}. {f.act_section} - {f.accused_or_group}\nProsecution: {f.prosecution_position}\n"
            f"Defense: {f.defense_position}\nFinding: {f.finding}\n"
            f"Record: {', '.join(f.record_refs)}. Authorities: {', '.join(f.authority_refs) or 'Not verified'}.\n"
            f"Missing evidence: {'; '.join(f.missing_evidence) or 'None identified'}."
            for i, f in enumerate(result.findings, 1)
        )
        full = (
            f"{result.decision_type.replace('_', ' ').upper()}\n"
            f"Presiding: {(judge_name or 'Default').replace('_', ' ')}\n\n"
            f"Procedural basis: {result.procedural_basis}\n\n"
            f"Verdict: {result.verdict}\n\n"
            f"Reasoning:\n{result.reasoning}\n\n"
            f"Issue findings:\n{findings}\n\n"
            f"Unresolved questions:\n" + "\n".join(result.unresolved_questions) + "\n\n"
            f"Sections applied: {', '.join(result.sections_applied)}\n\n"
            f"Probable punishment: {result.probable_punishment}\n\n"
            f"Confidence: {result.confidence}/100 (self-assessed record sufficiency; not probability of guilt)"
        )
        structured = {**result.model_dump(), "judge": judge_name, "review": review.model_dump()}
        save_report(conn, "judge", full, structured)
        return {
            "ok": True,
            "report": full,
            "verdict": result.verdict,
            "confidence": result.confidence,
            "structured": structured,
        }
    finally:
        conn.close()
