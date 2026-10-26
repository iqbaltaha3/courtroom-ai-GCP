"""HTTP endpoints for statutes."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.database.extra_statutes import (
    init_extra_statutes,
    is_core_act,
    list_extra_acts,
    list_extra_sections,
    search_extra_statutes,
)
from app.database.statutes import get_all_sections, search_statutes
from app.schemas import ActsOut, StatuteBrowseItem, StatuteBrowseOut, StatuteHit, StatuteSearchOut

router = APIRouter(prefix="/api/v1", tags=["statutes"])

CORE_ACTS = ["BNS", "BNSS", "BSA"]


@router.get("/statutes/acts", response_model=ActsOut)
def list_acts():
    """Acts available in the Statute Library (core + extra labour/company laws)."""
    init_extra_statutes()
    extra = list_extra_acts()
    return ActsOut(acts=CORE_ACTS + extra, core=CORE_ACTS, extra=extra)


@router.get("/statutes/browse", response_model=StatuteBrowseOut)
def browse_statute(
    act: str = Query(..., min_length=1),
    limit: int = Query(250, ge=1, le=1000),
):
    """Browse sections/headings for a selected act so the UI can offer clickable navigation."""
    act_raw = (act or "").strip()
    sections = []

    if is_core_act(act_raw):
        core = get_all_sections()
        for core_act in core.get("acts", []):
            if core_act.get("code") == act_raw.upper():
                for s in core_act.get("sections", [])[:limit]:
                    sections.append(
                        StatuteBrowseItem(
                            act=act_raw.upper(),
                            section=str(s.get("n", "")),
                            chapter_title=s.get("ct", "") or "",
                            text=s.get("t", "") or "",
                        )
                    )
                break
    else:
        for s in list_extra_sections(act_raw, limit=limit):
            sections.append(
                StatuteBrowseItem(
                    act=s.get("act", act_raw),
                    section=str(s.get("section", "")),
                    chapter_title=s.get("chapter_title", "") or "",
                    text=s.get("text", "") or "",
                )
            )

    return StatuteBrowseOut(act=act_raw, sections=sections)


@router.get("/statutes/search", response_model=StatuteSearchOut)
def search(
    q: str = Query(..., min_length=1),
    act: str = Query("All"),
    limit: int = Query(12, ge=1, le=50),
):
    """Statute Library search only."""
    act_raw = (act or "All").strip()
    results = []

    if act_raw in ("All", "all", "") or is_core_act(act_raw):
        act_arg = None if act_raw in ("All", "all", "") else act_raw.upper()
        try:
            for h in search_statutes(q, act=act_arg, limit=limit):
                results.append(
                    StatuteHit(
                        act=h.get("act", ""),
                        section=str(h.get("section") or h.get("section_number", "")),
                        chapter_title=h.get("chapter_title", "") or h.get("title", ""),
                        text=h.get("text", "") or h.get("t", ""),
                        score=float(h.get("score", 0) or 0),
                    )
                )
        except Exception:
            pass

    if act_raw in ("All", "all", "") or not is_core_act(act_raw):
        extra_act = None if act_raw in ("All", "all", "") else act_raw
        try:
            for h in search_extra_statutes(q, act=extra_act, limit=limit):
                results.append(
                    StatuteHit(
                        act=h.get("act", ""),
                        section=str(h.get("section", "")),
                        chapter_title=h.get("chapter_title", "") or h.get("title", ""),
                        text=h.get("text", "") or "",
                        score=float(h.get("score", 0) or 0),
                    )
                )
        except Exception:
            pass

    return StatuteSearchOut(query=q, results=results[:limit])
