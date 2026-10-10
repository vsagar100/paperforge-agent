from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Project(StrictModel):
    topic: str = Field(min_length=8, max_length=10000)
    domain: str = "engineering"
    journal: str | None = None
    paper_type: Literal["auto", "original_research", "review"] = "auto"


class InputItem(StrictModel):
    id: str
    path: str
    sha256: str
    role: Literal["author_note", "dataset", "source", "figure", "code", "artifact"]
    status: Literal["extracted", "partial", "conversion_required", "unsupported", "failed"]
    chunks: list[dict[str, str]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Source(StrictModel):
    id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    doi: str | None = None
    url: str
    venue: str = ""
    abstract: str = ""
    metadata_verified: bool = False
    verification: str = ""
    access_level: Literal["metadata", "abstract", "full_text"] = "metadata"
    passages: list[dict[str, str]] = Field(default_factory=list)
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None


class ExtractedFact(StrictModel):
    summary: str
    quote: str = Field(min_length=1)


class SourceAssessment(StrictModel):
    source_id: str
    category: str
    method: ExtractedFact | None = None
    dataset_or_system: ExtractedFact | None = None
    finding: ExtractedFact | None = None
    reported_limitation: ExtractedFact | None = None
    missing_from_accessible_text: list[str] = Field(default_factory=list)


class FigureSpec(StrictModel):
    title: str
    purpose: str
    nodes: list[str] = Field(min_length=2, max_length=12)
    edges: list[tuple[int, int]] = Field(default_factory=list)
    status: Literal["proposed"] = "proposed"

    @model_validator(mode="after")
    def valid_edges(self):
        if any(
            a < 0 or b < 0 or a >= len(self.nodes) or b >= len(self.nodes) or a == b
            for a, b in self.edges
        ):
            raise ValueError("Figure edges must reference distinct existing nodes")
        return self


class StudyPlan(StrictModel):
    manuscript_title: str | None = Field(default=None, min_length=8, max_length=250)
    research_question: str
    proposed_gap: str
    gap_source_ids: list[str] = Field(min_length=1)
    objectives: list[str]
    method: str
    experiments: list[str]
    metrics: list[str]
    assumptions: list[str]
    missing_evidence: list[str]
    dataset_search_terms: list[str] = Field(default_factory=list)
    figures: list[FigureSpec] = Field(default_factory=list, max_length=4)


class Paragraph(StrictModel):
    text: str = Field(min_length=1)
    kind: Literal["literature", "study", "interpretation", "proposal", "disclosure"]
    source_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    # Exact supporting excerpts let reviewers inspect entailment separately from existence.
    supporting_quotes: dict[str, str] = Field(default_factory=dict)


class Section(StrictModel):
    title: str
    paragraphs: list[Paragraph] = Field(min_length=1)


class ReviewIssue(StrictModel):
    section: str
    description: str
    required_change: str
    category: Literal["writing", "citation", "methodology", "evidence", "statistics", "format"]
    blocking: bool = True
    needs_author: bool = False


class Review(StrictModel):
    issues: list[ReviewIssue]
    summary: str


class Reply(StrictModel):
    text: str
    provider: str
    requested_model: str
    returned_model: str
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class Decision(StrictModel):
    action: Literal["continue", "revise", "defer", "cancel"]
    note: str = Field(default="", max_length=20000)
    stage: str | None = None
