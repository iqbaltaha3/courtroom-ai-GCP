"""Shared application paths, environment settings, and model limits."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent  # backend/
DATA_DIR = ROOT / "data"
STORAGE_DIR = ROOT / "storage" / "cases"
JUDGES_DIR = ROOT / "judges"

DATA_DIR.mkdir(parents=True, exist_ok=True)
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

GCP_PROJECT_ID = (
    os.getenv("GCP_PROJECT_ID")
    or os.getenv("GOOGLE_CLOUD_PROJECT")
    or os.getenv("GOOGLE_PROJECT_ID", "courtroom-ai-509709")
)
GCP_LOCATION = os.getenv("GCP_LOCATION") or os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "CORS_ORIGINS",
        ",".join(
            [
                "https://courtroom-ai-frontend-uvydgiipxq-el.a.run.app",
                "https://courtroom-ai-frontend-558535784594.asia-south1.run.app",
                "http://localhost:5173",
                "http://127.0.0.1:5173",
                "http://localhost:3000",
            ]
        ),
    ).split(",")
    if o.strip()
]
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "200"))


def vertex_openai_model(model: str) -> str:
    """Return the publisher-qualified model name required by Vertex OpenAI."""
    model = (model or "").strip()
    if not model:
        return model
    if "/" in model:
        return model
    return f"google/{model}"


# Models and request limits shared by simulation, transcription, and research.
GEMINI_REASONING_MODEL = os.getenv("GEMINI_REASONING_MODEL", "gemini-2.5-pro")
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "300"))
VERTEX_OPENAI_BASE = os.getenv(
    "VERTEX_OPENAI_BASE",
    f"https://{GCP_LOCATION}-aiplatform.googleapis.com/v1/projects/{GCP_PROJECT_ID}/locations/{GCP_LOCATION}/endpoints/openapi",
)

# Research retrieval limits and source authority ceilings.
EXTRACTOR_MODEL = os.getenv("GEMINI_EXTRACTOR_MODEL", os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))
PLANNER_MODEL = os.getenv("GEMINI_PLANNER_MODEL", os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))
RANKER_MODEL = os.getenv("GEMINI_RANKER_MODEL", os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))
REASONING_MODEL = os.getenv("GEMINI_REASONING_MODEL", os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))


N_STATUTE_RESULTS = 8
N_SEARCH_QUERIES = 5
TAVILY_RESULTS_PER_QUERY = 3

MAX_RESULT_CONTENT_CHARS = 700
MAX_CANDIDATES_TO_RANK = 15
MIN_SELECTED_PRECEDENTS = 3
MAX_SELECTED_PRECEDENTS = 5

MAX_STATUTE_TEXT_CHARS = 1200
MAX_REASONER_CANDIDATE_CHARS = 500


SOURCE_TYPE_AUTHORITY_CAPS = {
    "supreme_court_judgment": 100,
    "high_court_judgment": 90,
    "tribunal_decision": 75,
    "judgment_repository": 65,
    "legal_commentary": 35,
    "legal_blog": 20,
    "news_report": 15,
    "generic_information": 10,
    "video": 0,
    "social_media": 0,
    "unknown": 25,
}


TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
