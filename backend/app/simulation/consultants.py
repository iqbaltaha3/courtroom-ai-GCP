"""Write internal strategies for prosecution and defense."""

from app.ai.llm import call_llm
from app.database.cases import get_artifact, get_connection, get_report, save_report
from app.documents.context import record_context
from app.simulation.prompts import PIPELINE

# Each side has a different task but uses the same case and statute context.
CONSULTANT_PROMPTS = {
    "prosecution": """
Write an internal prosecution strategy, not court submissions. Map the supplied facts,
evidence and witnesses to offence ingredients under the Act actually in the record.
Keep IPC/Cr.P.C. framing unless supplied law supports an equivalent. Discuss strengths,
weaknesses, genuine contradictions, evidentiary gaps and likely defense arguments.
Give a grounded strategy in numbered paragraphs, under 900 words. Do not address the Court.
""",
    "defense": """
Write an internal defense strategy, not court submissions. Explain the prosecution theory,
gaps in ingredients, genuine contradictions and supported challenges to evidence and witnesses.
Discuss procedural or evidentiary issues only under supplied law, likely prosecution responses
and a grounded defense strategy. Presumption of innocence applies. Use numbered paragraphs,
under 900 words, and do not address the Court.
""",
}


def consultant(side):
    """Load the record, generate the selected strategy, and save its report."""
    conn = get_connection()
    try:
        user = (
            f"CASE MANAGER REPORT:\n{get_report(conn, 'case_manager') or ''}"
            f"\n\nORIGINAL RECORD:\n{record_context(conn)}"
            f"\n\nSTATUTORY MATERIAL:\n{get_artifact(conn, 'statute_context_bns', '')}"
        )
        content = call_llm(PIPELINE + CONSULTANT_PROMPTS[side], user, max_tokens=2500)
        save_report(conn, f"{side}_consultant", content)
        return {"ok": True, "report": content}
    finally:
        conn.close()


def stage_prosecution_consultant():
    """Prepare the prosecution strategy."""
    return consultant("prosecution")


def stage_defense_consultant():
    """Prepare the defense strategy."""
    return consultant("defense")
