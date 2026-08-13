from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

CURRENT_STATE_SCHEMA = 2


def utc_now() -> datetime:
    return datetime.now(UTC)


def normalize_question_key(value: str) -> str:
    """Create a provider-independent semantic key for question reconciliation."""
    key = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    return key[:80] or "research_detail"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    BLOCKING = "blocking"


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    PASSED_WITH_WARNINGS = "passed_with_warnings"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"

    @property
    def advances_workflow(self) -> bool:
        return self in {self.PASSED, self.PASSED_WITH_WARNINGS}


class QuestionStatus(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    ASSUMED = "assumed"
    DISMISSED = "dismissed"


class EvidenceKind(StrEnum):
    USER_STATEMENT = "user_statement"
    EXPERIMENTAL = "experimental"
    SOURCE = "source"
    STANDARD = "standard"
    COMPUTED = "computed"
    FIGURE = "figure"


class EvidenceItem(BaseModel):
    id: str = Field(pattern=r"^EV-[A-Z0-9_-]+$")
    kind: EvidenceKind
    title: str
    source_path: str | None = None
    locator: str | None = None
    content: str
    verified: bool = False
    checksum: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Claim(BaseModel):
    id: str = Field(pattern=r"^CL-[A-Z0-9_-]+$")
    text: str
    section: str
    evidence_ids: list[str] = Field(default_factory=list)
    citation_ids: list[str] = Field(default_factory=list)
    numeric: bool = False
    verified: bool = False


class Finding(BaseModel):
    id: str
    stage: str
    severity: Severity
    section: str | None = None
    problem: str
    required_action: str
    evidence_ids: list[str] = Field(default_factory=list)
    auto_fixable: bool = False
    resolved: bool = False


class QuestionProposal(BaseModel):
    """A model suggestion. IDs are deliberately assigned by PaperForge, not the model."""

    key: str | None = None
    text: str
    reason: str
    blocking: bool = True

    @model_validator(mode="after")
    def create_key(self) -> QuestionProposal:
        self.key = normalize_question_key(self.key or self.text)
        return self


class Question(BaseModel):
    id: str = Field(pattern=r"^Q-\d{3,}$")
    key: str
    text: str
    reason: str
    stage: str = "intake"
    blocking: bool = True
    status: QuestionStatus = QuestionStatus.OPEN
    answer: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    answered_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_question(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        raw_id = str(migrated.get("id", ""))
        digits = "".join(re.findall(r"\d+", raw_id))
        if not re.fullmatch(r"Q-\d{3,}", raw_id):
            migrated["id"] = f"Q-{int(digits or '0'):03d}"
        migrated.setdefault("key", normalize_question_key(str(migrated.get("text", ""))))
        migrated.setdefault("stage", "intake")
        if migrated.get("answer"):
            migrated["status"] = QuestionStatus.ANSWERED
        else:
            migrated.setdefault("status", QuestionStatus.OPEN)
        return migrated

    @model_validator(mode="after")
    def synchronize_status(self) -> Question:
        self.key = normalize_question_key(self.key)
        if self.answer and self.status == QuestionStatus.OPEN:
            self.status = QuestionStatus.ANSWERED
        if self.status == QuestionStatus.ANSWERED and not self.answer:
            raise ValueError("an answered question must contain an answer")
        return self

    @property
    def is_open(self) -> bool:
        return self.status == QuestionStatus.OPEN and not self.answer


class QuestionRound(BaseModel):
    stage: str
    number: int = Field(ge=1)
    question_ids: list[str]
    context_fingerprint: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    closed_at: datetime | None = None


class ManuscriptPatch(BaseModel):
    section: str
    before: str
    after: str
    rationale: str
    evidence_ids: list[str] = Field(default_factory=list)
    scientific_change: bool = False


class ResearchProfileUpdate(BaseModel):
    objectives: list[str] = Field(default_factory=list)
    contribution: str | None = None
    target_journal: str | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)


class StageResult(BaseModel):
    stage: str
    status: StageStatus
    score: float = Field(ge=0, le=1)
    findings: list[Finding] = Field(default_factory=list)
    questions: list[QuestionProposal] = Field(default_factory=list)
    question_ids: list[str] = Field(default_factory=list)
    patches: list[ManuscriptPatch] = Field(default_factory=list)
    replacement_document: str | None = None
    profile_update: ResearchProfileUpdate | None = None
    claim_updates: list[Claim] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    model_role: str | None = None
    model_name: str | None = None
    attempt: int = Field(default=1, ge=1)
    input_fingerprint: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


class ResearchProfile(BaseModel):
    topic: str = Field(min_length=8)
    domain: str = "engineering"
    objectives: list[str] = Field(default_factory=list)
    contribution: str | None = None
    target_journal: str | None = None
    manuscript_type: str = "research_article"
    constraints: dict[str, Any] = Field(default_factory=dict)


class WorkflowState(BaseModel):
    schema_version: int = CURRENT_STATE_SCHEMA
    project_id: str
    profile: ResearchProfile
    current_stage: str | None = None
    stage_status: dict[str, StageStatus] = Field(default_factory=dict)
    stage_runs: list[StageResult] = Field(default_factory=list)
    pending_questions: list[Question] = Field(default_factory=list)
    question_rounds: list[QuestionRound] = Field(default_factory=list)
    closed_question_keys: set[str] = Field(default_factory=set)
    next_question_number: int = Field(default=1, ge=1)
    intake_closed: bool = False
    source_fingerprint: str | None = None
    manuscript_version: int = 0
    workflow_completed: bool = False
    submission_ready: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_question_ledger(self) -> WorkflowState:
        ids = [question.id for question in self.pending_questions]
        if len(ids) != len(set(ids)):
            raise ValueError("question IDs must be unique")
        open_keys = [question.key for question in self.pending_questions if question.is_open]
        if len(open_keys) != len(set(open_keys)):
            raise ValueError("open question keys must be unique")
        return self

    def open_questions(self, stage: str | None = None) -> list[Question]:
        return [
            question
            for question in self.pending_questions
            if question.is_open and (stage is None or question.stage == stage)
        ]
