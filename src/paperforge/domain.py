from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

CURRENT_STATE_SCHEMA = 3


def utc_now() -> datetime:
    return datetime.now(UTC)


class PaperType(StrEnum):
    AUTO = "auto"
    ORIGINAL_RESEARCH = "original_research"
    REVIEW_ARTICLE = "review_article"


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    PASSED_WITH_ACTIONS = "passed_with_actions"
    BLOCKED = "blocked"
    FAILED = "failed"

    @property
    def complete(self) -> bool:
        return self in {self.PASSED, self.PASSED_WITH_ACTIONS}


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    BLOCKING = "blocking"


class IssueDisposition(StrEnum):
    AUTO_FIX = "auto_fix"
    AUTHOR_ACTION = "author_action"
    RECOMMENDATION = "recommendation"
    INTEGRITY_BLOCKER = "integrity_blocker"


class EvidenceKind(StrEnum):
    USER_FACT = "user_fact"
    EXPERIMENTAL_DATA = "experimental_data"
    SOURCE_DOCUMENT = "source_document"
    FIGURE = "figure"
    COMPUTED = "computed"


class EvidenceItem(BaseModel):
    id: str = Field(pattern=r"^EV-[A-Z0-9_-]+$")
    kind: EvidenceKind
    title: str
    content: str
    source_path: str | None = None
    locator: str | None = None
    checksum: str | None = None
    verified: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReferenceRecord(BaseModel):
    id: str = Field(pattern=r"^REF\d{3,}$")
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = Field(default=None, ge=1500, le=2200)
    venue: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    publisher: str | None = None
    doi: str | None = None
    url: str | None = None
    openalex_id: str | None = None
    abstract: str | None = None
    cited_by_count: int = Field(default=0, ge=0)
    publication_type: str | None = None
    open_access_url: str | None = None
    verified: bool = False
    verification_sources: list[str] = Field(default_factory=list)
    retracted: bool = False
    relevance_score: float = Field(default=0.0, ge=0.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("doi")
    @classmethod
    def normalize_doi(cls, value: str | None) -> str | None:
        if not value:
            return None
        clean = value.strip().lower()
        clean = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", clean)
        return clean.rstrip(".,; ") or None


class ResearchProfile(BaseModel):
    topic: str = Field(min_length=8)
    synopsis: str | None = None
    domain: str = "engineering"
    requested_paper_type: PaperType = PaperType.AUTO
    resolved_paper_type: PaperType | None = None
    target_journal: str | None = None
    language: str = "English"
    constraints: dict[str, Any] = Field(default_factory=dict)

    @property
    def complete_input(self) -> str:
        if self.synopsis and self.synopsis.strip():
            return f"Topic: {self.topic}\n\nSynopsis:\n{self.synopsis.strip()}"
        return self.topic


class ResearchPlan(BaseModel):
    working_title: str
    paper_type: PaperType
    research_question: str
    objectives: list[str] = Field(min_length=1)
    contribution: str
    scope: str
    keywords: list[str] = Field(min_length=3, max_length=10)
    search_queries: list[str] = Field(min_length=2, max_length=8)
    required_sections: list[str] = Field(min_length=5)
    rationale: str

    @model_validator(mode="after")
    def normalize_lists(self) -> ResearchPlan:
        self.keywords = list(dict.fromkeys(x.strip() for x in self.keywords if x.strip()))[:10]
        self.search_queries = list(
            dict.fromkeys(x.strip() for x in self.search_queries if x.strip())
        )[:8]
        self.required_sections = list(
            dict.fromkeys(x.strip() for x in self.required_sections if x.strip())
        )
        return self


class LiteratureNote(BaseModel):
    reference_id: str = Field(pattern=r"^REF\d{3,}$")
    method: str = "Not reported in the available metadata."
    dataset_or_material: str = "Not reported in the available metadata."
    key_finding: str = "Not reported in the available metadata."
    limitation: str = "Not reported in the available metadata."
    relevance: str


class LiteratureSynthesis(BaseModel):
    themes: list[str] = Field(min_length=1)
    research_gap: str
    novelty_position: str
    source_notes: list[LiteratureNote] = Field(default_factory=list)
    synthesis_summary: str


class OutlineSection(BaseModel):
    heading: str
    purpose: str
    evidence_ids: list[str] = Field(default_factory=list)
    reference_ids: list[str] = Field(default_factory=list)
    target_words: int = Field(default=350, ge=80, le=2500)


class ManuscriptOutline(BaseModel):
    title: str
    sections: list[OutlineSection] = Field(min_length=5)


class ReviewIssue(BaseModel):
    id: str
    code: str
    severity: Severity
    section: str | None = None
    description: str
    required_change: str
    evidence_ids: list[str] = Field(default_factory=list)
    reference_ids: list[str] = Field(default_factory=list)
    requires_new_evidence: bool = False
    disposition: IssueDisposition | None = None
    resolved: bool = False
    resolution: str | None = None

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_") or "review_issue"


class ReviewReport(BaseModel):
    review_type: str
    summary: str
    dimension_scores: dict[str, float] = Field(default_factory=dict)
    issues: list[ReviewIssue] = Field(default_factory=list)
    recommendation: str = "revise"

    @field_validator("dimension_scores")
    @classmethod
    def validate_scores(cls, value: dict[str, float]) -> dict[str, float]:
        return {key: min(1.0, max(0.0, float(score))) for key, score in value.items()}

    @property
    def score(self) -> float:
        if not self.dimension_scores:
            return 0.0
        return sum(self.dimension_scores.values()) / len(self.dimension_scores)


class StageRecord(BaseModel):
    stage: str
    status: StageStatus
    attempt: int = Field(default=1, ge=1)
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    input_fingerprint: str
    output_fingerprint: str | None = None
    model: str | None = None
    issues: list[ReviewIssue] = Field(default_factory=list)
    changes: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


class AuthorAction(BaseModel):
    id: str
    section: str | None = None
    action: str
    reason: str
    blocking: bool = False
    resolved: bool = False


class WorkflowState(BaseModel):
    schema_version: int = CURRENT_STATE_SCHEMA
    project_id: str
    profile: ResearchProfile
    stage_status: dict[str, StageStatus] = Field(default_factory=dict)
    stage_records: dict[str, StageRecord] = Field(default_factory=dict)
    run_history: list[StageRecord] = Field(default_factory=list)
    current_stage: str | None = None
    input_fingerprint: str | None = None
    config_fingerprint: str | None = None
    manuscript_version: int = 0
    workflow_completed: bool = False
    submission_ready: bool = False
    author_actions: list[AuthorAction] = Field(default_factory=list)
    migration_notes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None

    def status_for(self, stage: str) -> StageStatus:
        return self.stage_status.get(stage, StageStatus.PENDING)
