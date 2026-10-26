"""HTTP endpoints for judges."""

from fastapi import APIRouter

from app.config import JUDGES_DIR
from app.schemas import JudgeOut

router = APIRouter(prefix="/api/v1", tags=["judges"])


@router.get("/judges", response_model=list[JudgeOut])
def list_judges():
    """List judge profile filenames as selectable display names."""
    if not JUDGES_DIR.is_dir():
        return []
    out = []
    for p in sorted(JUDGES_DIR.glob("*.txt")):
        out.append(JudgeOut(id=p.stem, display_name=p.stem.replace("_", " ")))
    return out
