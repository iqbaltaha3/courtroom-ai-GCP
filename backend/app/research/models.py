"""Validated facts, plans, source assessments, and final research output."""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class CaseFactExtraction(BaseModel):
    """
    Structured output from the case fact extractor.
    """

    summary: str = Field(description="A concise 2-3 sentence summary of the legal problem.")
    parties_involved: list[str] = Field(
        description="List of parties (e.g. complainant, accused, police officer, magistrate)."
    )
    factual_allegations: str = Field(
        description="The factual narrative of what allegedly happened, in plain language."
    )
    alleged_offences: str = Field(
        description="The offences or legal wrongs that appear to be involved, based on the facts."
    )
    legal_issues: list[str] = Field(description="Key legal questions or issues raised by the facts.")
    disputed_facts: list[str] = Field(description="Facts that are contested or denied by any party.")
    procedural_context: Optional[str] = Field(
        default=None,
        description="Any procedural information (court, stage of case, bail, etc.) if mentioned.",
    )


class HistoricalEquivalent(BaseModel):
    """
    Historical provision relevant to the current legal concept.
    """

    current_concept: str
    historical_provision: str
    statute: str
    explanation: str


class ResearchPlan(BaseModel):
    """Legal issues and queries used to guide the research stage."""

    actual_alleged_offences: list[str]
    relevant_retrieved_sections: list[str]
    irrelevant_retrieved_sections: list[str]
    historical_equivalents: list[HistoricalEquivalent]
    legal_concepts: list[str]
    research_questions: list[str]
    search_queries: list[str]


# Categorical source classification. This is the key addition:
# instead of trusting only a numerical judicial_authority_score,
# the ranker must bucket every candidate into one of these types,
# and Python enforces a hard authority cap per bucket.
SourceType = Literal[
    "supreme_court_judgment",
    "high_court_judgment",
    "tribunal_decision",
    "judgment_repository",
    "legal_commentary",
    "legal_blog",
    "news_report",
    "video",
    "social_media",
    "generic_information",
    "unknown",
]


class CandidateAssessment(BaseModel):
    """
    AI assessment of one search candidate.
    """

    candidate_id: int
    source_type: SourceType
    relevance_score: int = Field(ge=0, le=100)
    source_quality_score: int = Field(ge=0, le=100)
    judicial_authority_score: int = Field(ge=0, le=100)
    factual_similarity_score: int = Field(ge=0, le=100)
    keep_for_reasoner: bool
    redundant_with: Optional[int] = None
    reason: str


class PrecedentRanking(BaseModel):
    """
    AI returns one assessment for every candidate.
    """

    assessments: list[CandidateAssessment]


class PrecedentAnalysisItem(BaseModel):
    """
    One precedent-related claim, with its own confidence and
    evidentiary basis.
    """

    claim: str
    support: Literal[
        "statutory_text",
        "judicial_authority",
        "secondary_source",
        "unverified",
    ]
    confidence: Literal[
        "high",
        "moderate",
        "low",
    ]


class FinalLegalResearch(BaseModel):
    """
    Structured legal research section.
    """

    issues: list[str]
    applicable_law: list[str]
    precedent_analysis: list[PrecedentAnalysisItem]
    application: list[str]
    uncertainties: list[str]
    research_queries: list[str]
    evidence_completeness: Literal[
        "exhaustive_search_no_authority_found",
        "limited_search_may_have_missed_authority",
    ]


class LegalResearchOutput(BaseModel):
    """
    Final structured legal research response.
    """

    laws: str
    sections_applied: str
    precedents: str
    legal_research: FinalLegalResearch
