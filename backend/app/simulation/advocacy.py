"""Prepare both sides and run the argument rounds."""

from __future__ import annotations

import json
from contextlib import closing
from typing import Any

from app.ai.grounding import grounded_research
from app.ai.llm import call_llm, call_structured
from app.database.cases import (
    get_all_reports,
    get_artifact,
    get_connection,
    get_report,
    save_artifact,
    save_report,
)
from app.documents.context import record_context
from app.simulation.models import (
    ArgumentReport,
    ResearchPlan,
    validate_references,
)
from app.simulation.prompts import PIPELINE
from app.simulation.shared import _reference_ids


def _research_side(side: str) -> dict[str, Any]:
    """side = 'prosecution' | 'defense'"""
    with closing(get_connection()) as conn:
        cm = get_report(conn, "case_manager") or ""
        consultant = get_report(conn, f"{side}_consultant") or ""

        system = (
            PIPELINE
            + f"""
    You are the {side.title()} Legal Research Officer. Your task is limited to legal issues and search framing.
    From the consultant report and Case Manager Report only:
    1. Identify the key legal issues that require judicial interpretation or precedent support, limited to issues that actually appear in the supplied reports.
    2. Create exactly five precise precedent search queries that would be useful for the {side}.
    3. For each query, state in one sentence the principle sought.
    Do not invent case names, citations, or holdings. Do not claim any case exists. Focus only on the legal questions raised by the record. If the record is thin, say so.
    Write in numbered paragraphs. No markdown. Keep the planner complete and under 500 words.
    """
        )
        system += "\nReturn issues and queries in the schema. Queries must contain only generic legal issues and Act/section names, no case-party names, addresses or identifiers."
        user = f"CASE MANAGER:\n{cm}\nCONSULTANT:\n{consultant}"
        plan = call_structured(system, user, ResearchPlan, max_tokens=3000)
        try:
            research = grounded_research(plan.queries, side)
        except Exception as research_exc:
            research = {
                "queries": plan.queries,
                "sources": [],
                "passages": [],
                "search_entry_point": "",
                "limitations": f"Live precedent research unavailable: {research_exc}",
            }
        save_artifact(conn, f"{side}_sources", research)
        planner = "\n".join(f"{i}. {q}" for i, q in enumerate(plan.queries, 1))

        system2 = (
            PIPELINE
            + f"""
    You are the {side.title()} Precedent Reasoner.
    Given the retrieved source-supported passages and case context, produce a research report in numbered paragraphs covering:
    1. Key legal principles relevant to the {side} that can be derived from the statutory text and the issues in the record.
    2. How those principles apply to the facts that are actually present in the record.
    3. Strengths and limitations of the available legal position based on the supplied material.
    4. Notes on binding and persuasive authority under Indian law in general terms only. Do not invent specific case holdings.
    For each legal proposition cite its supplied RP/RD/ST source identifier. Describe the retrieved source's
    court, procedural stage, actual principle, factual analogy, adverse authority and limits where available.
    If those details are unavailable, say so. Source-supported passages are not independently verified
    judgments; do not imply that they are, or that current validity has been checked.
    Identify each alleged offence's ingredients only where supported by retrieved sources.
    State which consultant propositions are corrected or remain unverified. Do not treat a failed shot,
    absence of injury or a non-exploding device as automatically negating an attempt; research the actual test.
    Do not invent case names, citations or holdings. Keep the complete report under 1400 words.
    """
        )
        user2 = f"""PLANNER OUTPUT:
    {planner}

    CASE CONTEXT:
    {cm}

    CONSULTANT TO CHECK:
    {consultant}

    RETRIEVED SOURCE-SUPPORTED MATERIAL:
    {json.dumps(research, ensure_ascii=False)}

    LOCAL STATUTES:
    {get_artifact(conn, "statute_context_bns", "")}

    Write the {side} precedent research report."""

        report = call_llm(system2, user2, max_tokens=6000)
        sources = "\n".join(f"[{s['id']}] {s['title']}: {s['url']}" for s in research["sources"])
        full = f"SEARCH PLAN:\n{planner}\n\nRESEARCH REPORT:\n{report}\n\nSOURCES:\n{sources}\n\nLIMITATIONS:\n{research['limitations']}"
        save_report(conn, f"{side}_research", full, research)
        return {"ok": True, "report": full}


def stage_prosecution_research() -> dict[str, Any]:
    """Research the legal questions raised by the prosecution strategy."""
    return _research_side("prosecution")


def stage_defense_research() -> dict[str, Any]:
    """Research the legal questions raised by the defense strategy."""
    return _research_side("defense")


def _context_builder(side: str, round_num: int) -> str:
    """Combine complete prior reports and page-cited record context for an argument."""
    with closing(get_connection()) as conn:
        reports = get_all_reports(conn)

        cm = reports.get("case_manager", "")
        own_consultant = reports.get(f"{side}_consultant", "")
        own_research = reports.get(f"{side}_research", "")
        opp = "defense" if side == "prosecution" else "prosecution"

        history = []
        if round_num >= 1:
            history.append(("Prosecution R1", reports.get("pros_r1", "")))
            history.append(("Defense R1", reports.get("def_r1", "")))
        if round_num >= 2:
            history.append(("Prosecution R2", reports.get("pros_r2", "")))
            history.append(("Defense R2", reports.get("def_r2", "")))

        hist_text = "\n\n".join(f"{k}:\n{v}" for k, v in history if v)
        record = record_context(conn)

        return f"""CASE MANAGER:
    {cm}

    OWN CONSULTANT STRATEGY:
    {own_consultant}

    OWN RESEARCH:
    {own_research}

    OPPOSING RESEARCH (address adverse authority):
    {reports.get(f"{opp}_research", "")}

    {record}

    DEBATE SO FAR:
    {hist_text or "None yet."}
    """


def stage_prosecutor_r1() -> dict[str, Any]:
    """Prepare the prosecution opening from supplied evidence and law."""
    ctx = _context_builder("prosecution", 0)
    system = (
        PIPELINE
        + """
You are the Public Prosecutor in a simulated hearing at the stage identified in the case report. Address the Court as Your Honour.
This is Round 1 opening submissions. Using only facts, evidence, witnesses and sections present in the supplied context:
1. State the charge under the statutory sections that appear in the Case Manager Report or statute material.
2. Apply only the facts that are in the record to the ingredients of each offence.
3. Refer only to key evidence and witnesses that appear in the record.
Write four to six concise numbered submissions in courtroom voice. No preamble outside the submissions.
If a fact or section is missing, omit it or state that it is not in the record. Do not invent material.
"""
    )
    return _argument("pros_r1", system, ctx)


def stage_defense_r1() -> dict[str, Any]:
    """Reply to the prosecution opening with record-supported defense points."""
    ctx = _context_builder("defense", 1)
    system = (
        PIPELINE
        + """
You are Defence Counsel in a simulated hearing at the stage identified in the case report. Address the Court as Your Honour.
This is Round 1 reply submissions. Using only material present in the supplied context:
1. Answer the prosecution opening with points that can be grounded in the record.
2. Raise reasonable doubt only where the record itself supports a gap or inconsistency.
3. Challenge ingredients, evidence, or witness reliability only with support from the record.
Write four to six concise numbered submissions in courtroom voice. Presumption of innocence applies.
Do not invent facts, evidence, or authorities. If something is not in the record, say so.
"""
    )
    return _argument("def_r1", system, ctx, "pros_r1")


def stage_prosecutor_r2() -> dict[str, Any]:
    """Answer the defense and request relief appropriate to the documented stage."""
    ctx = _context_builder("prosecution", 1)
    system = (
        PIPELINE
        + """
You are the Public Prosecutor. Address the Court as Your Honour.
This is Round 2 closing submissions. Using only material in the supplied context:
1. Answer the defence submissions with reference to the record.
2. Reaffirm the charge and the strength of the evidence that actually appears in the record.
3. Seek relief justified by the actual procedural stage. Never seek conviction on an FIR-only record.
Write four to six concise numbered submissions in courtroom voice. Do not invent facts or authorities.
"""
    )
    return _argument("pros_r2", system, ctx, "def_r1")


def stage_defense_r2() -> dict[str, Any]:
    """Give the final defense reply using supplied evidence and legal sources."""
    ctx = _context_builder("defense", 2)
    system = (
        PIPELINE
        + """
You are Defence Counsel. Address the Court as Your Honour.
This is Round 2 closing submissions. Using only material in the supplied context:
1. Answer the prosecution closing with reference to the record.
2. Reiterate reasonable doubt and any procedural or evidentiary issues that are supported by the record.
3. Seek relief justified by the actual procedural stage. Never seek acquittal on an FIR-only record.
Write four to six concise numbered submissions in courtroom voice. Do not invent facts or authorities.
"""
    )
    return _argument("def_r2", system, ctx, "pros_r2")


def _argument(key: str, system: str, context: str, opponent: str = "") -> dict[str, Any]:
    """Validate citations and rebuttal coverage before saving a structured submission."""
    conn = get_connection()
    try:
        record_ids, authority_ids = _reference_ids(conn)
        opposing = get_artifact(conn, opponent, {}) if opponent else {}
        required = {f"{opponent}.{i}" for i in range(1, len(opposing.get("submissions", [])) + 1)}
        instruction = (
            "\nReturn the structured submission fields. Give each proposition its factual basis, "
            "legal analysis and honest limitation. Cite P page IDs and RP/RD/ST authority IDs. "
            "Identify the specific accused or group and why liability differs. "
            "Every opposing submission must be answered or expressly conceded by listing its ID "
            f"in responds_to. Required opposing IDs (use each at least once): {sorted(required)}. "
            "You may answer several opposing points in one submission by listing multiple IDs. "
            "Opening submissions have no responds_to IDs. Do not invent unknown opposing IDs. "
            "Do not repeat another side's claim as fact."
        )
        last_exc: Exception | None = None
        result = None
        for attempt in range(3):
            result = call_structured(system + instruction, context, ArgumentReport, max_tokens=7000)
            try:
                validate_references(result, record_ids, authority_ids)
                answered = {ref for sub in result.submissions for ref in sub.responds_to}
                missing = required - answered
                # A rebuttal must cover every opposing ID and cite no unknown ID.
                if missing or answered - required:
                    raise ValueError(
                        f"Rebuttal coverage mismatch: missing={missing}, unknown={answered - required}"
                    )
                if any(not sub.record_refs for sub in result.submissions):
                    raise ValueError("Each submission must identify its factual page basis")
                last_exc = None
                break
            except ValueError as exc:
                last_exc = exc
                context += (
                    f"\nRepair these handoff errors (attempt {attempt + 1}): {exc}. "
                    f"You MUST include every ID in responds_to at least once: {sorted(required)}."
                )

        if last_exc is not None:
            raise last_exc
        if result is None:
            raise ValueError("Argument stage produced no structured result")

        lines = []
        for i, sub in enumerate(result.submissions, 1):
            lines.append(
                f"{key}.{i}. {sub.proposition}\n{sub.factual_basis}\n{sub.legal_analysis}\n"
                f"Record: {', '.join(sub.record_refs)}. Authorities: {', '.join(sub.authority_refs) or 'Unverified; no source relied on'}.\n"
                f"Responds to: {', '.join(sub.responds_to) or 'Opening'}.\nLimit: {sub.concession_or_limit}"
            )
        content = "\n\n".join(lines) + f"\n\nRelief sought: {result.relief_sought}"
        save_artifact(conn, key, result.model_dump())
        save_report(conn, key, content, result.model_dump())
        return {"ok": True, "report": content, "structured": result.model_dump()}
    finally:
        conn.close()
