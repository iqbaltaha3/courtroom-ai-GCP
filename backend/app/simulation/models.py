"""Validated handoffs for advocacy and judicial evaluation."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class ResearchPlan(BaseModel):
    """Legal issues and queries used to guide the research stage."""

    issues: list[str] = Field(min_length=1)
    queries: list[str] = Field(min_length=2, max_length=5)


class Submission(BaseModel):
    """A legal proposition with its factual basis, citations, and opposing replies."""

    proposition: str = Field(min_length=15)
    factual_basis: str
    legal_analysis: str
    record_refs: list[str]
    authority_refs: list[str]
    responds_to: list[str]
    concession_or_limit: str


class ArgumentReport(BaseModel):
    """Validated advocacy submissions and the requested relief."""

    submissions: list[Submission] = Field(min_length=3, max_length=8)
    relief_sought: str


class ChargeFinding(BaseModel):
    """Both sides and the assessment for one accused and alleged offence."""

    act_section: str
    accused_or_group: str
    prosecution_position: str
    defense_position: str
    finding: str
    record_refs: list[str] = Field(min_length=1)
    authority_refs: list[str]
    missing_evidence: list[str]


class JudicialDecision(BaseModel):
    """A stage-appropriate decision with findings, unresolved issues, and record sufficiency."""

    decision_type: Literal["preliminary_assessment", "interlocutory_assessment", "trial_judgment"]
    procedural_basis: str
    verdict: str = Field(min_length=10)
    reasoning: str = Field(min_length=100)
    findings: list[ChargeFinding] = Field(min_length=1)
    unresolved_questions: list[str]
    sections_applied: list[str]
    probable_punishment: str
    confidence: int = Field(
        ge=0, le=100, description="Self-assessed record sufficiency, not probability of guilt"
    )


class JudicialReview(BaseModel):
    """Independent checks that must pass before publishing a decision."""

    acceptable: bool
    material_errors: list[str]
    corrections_required: list[str]
    strengths: list[str]


def validate_references(report: BaseModel, record_ids: set[str], authority_ids: set[str]) -> None:
    """Reject page or authority identifiers that are absent from the supplied record."""

    def walk(value):
        """Inspect nested report fields for page and authority reference lists."""
        if isinstance(value, dict):
            for key, item in value.items():
                allowed = (
                    record_ids if key == "record_refs" else authority_ids if key == "authority_refs" else None
                )
                if allowed is not None and not set(item).issubset(allowed):
                    raise ValueError(f"Unknown {key}: {set(item) - allowed}")
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(report.model_dump())


class ExtractedLaw(BaseModel):
    """An Act and section exactly as cited in the supplied pages."""

    act: str = Field(description="Act name exactly as found, e.g. IPC 1860, Cr.P.C., BNS, BNSS, BSA")
    section: str
    title: str = ""
    relevance: str = ""
    page_refs: str = ""


class ExtractedWitness(BaseModel):
    """A named witness, their role, and their page-cited account."""

    name: str
    role: str = ""
    statement_summary: str = ""
    page_refs: str = ""
    reliability_notes: str = ""


class ExtractedEvidence(BaseModel):
    """An alleged or supplied evidence item and its source pages."""

    description: str
    evidence_type: str = Field(description="documentary | physical | digital | oral")
    page_refs: str = ""
    relevance: str = ""


class ExtractedEntity(BaseModel):
    """A person, organization, location, or other named item in the record."""

    name: str
    entity_type: str = Field(description="person | organization | location | other")
    role: str = ""
    page_refs: str = ""
    notes: str = ""


class TimelineEvent(BaseModel):
    """A dated or ordered event extracted from the supplied pages."""

    event_date: str = ""
    event_description: str
    page_refs: str = ""
    sequence: int = 0


class ClarifyExtractResult(BaseModel):
    """The structured facts extracted from one batch of pages."""

    laws: list[ExtractedLaw]
    witnesses: list[ExtractedWitness]
    evidence: list[ExtractedEvidence]
    entities: list[ExtractedEntity]
    timeline: list[TimelineEvent]
    case_summary: str
    parties: dict[str, str]


class CaseManagerReport(BaseModel):
    """Neutral case narrative, stage, evidence inventory, and identified gaps."""

    case_caption: str
    parties: dict[str, str]
    court_and_stage: str
    neutral_factual_narrative: str = Field(min_length=50)
    procedural_stage: str = Field(
        description="investigation, bail, charge, trial, appeal, or unknown; infer only from supplied documents"
    )
    decision_scope: str = Field(description="What can legitimately be assessed on this record")
    alleged_offences: list[dict[str, Any]]
    evidence_matrix: list[str]
    witness_matrix: list[str]
    timeline_summary: list[str]
    key_gaps_and_inconsistencies: list[str]
    document_inventory: list[str]
