"""HTTP endpoints for health."""

from fastapi import APIRouter

from app.config import GCP_LOCATION, GCP_PROJECT_ID, GEMINI_MODEL, JUDGES_DIR, vertex_openai_model
from app.schemas import HealthOut

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut)
def health():
    """Report local configuration and data availability without calling an AI provider."""
    judges_count = 0
    try:
        judges_count = len(list(JUDGES_DIR.glob("*.txt"))) if JUDGES_DIR.is_dir() else 0
    except Exception:
        judges_count = 0

    statute_ok = False
    try:
        from app.database.statutes import get_db_stats

        stats = get_db_stats()
        statute_ok = stats.get("ok", False)
    except Exception:
        statute_ok = False

    return HealthOut(
        status="ok",
        llm_configured=bool(GCP_PROJECT_ID),
        llm_provider=f"Gemini on Vertex AI ({GCP_LOCATION})",
        llm_model=vertex_openai_model(GEMINI_MODEL),
        judges=judges_count,
        statute_ok=statute_ok,
    )
