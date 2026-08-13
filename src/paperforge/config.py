from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

KNOWN_STAGES = {
    "intake",
    "evidence_preparation",
    "outline",
    "initial_draft",
    "methodology_review",
    "engineering_integrity",
    "citation_audit",
    "section_enhancement",
    "abstract_review",
    "originality_review",
    "manuscript_consistency",
    "journal_compliance",
    "final_audit",
}
REQUIRED_MODEL_ROLES = {"drafting", "enhancement", "scientific_review", "final_audit"}


class ModelRoleConfig(BaseModel):
    model: str = Field(min_length=2)
    temperature: float = Field(default=0.1, ge=0, le=2)


class OllamaConfig(BaseModel):
    host: str = "https://ollama.com"
    api_key_env: str = "OLLAMA_API_KEY"
    structured_outputs: bool = False

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Ollama host must be an absolute http(s) URL")
        return normalized


class ProviderConfig(BaseModel):
    active: str = "ollama"
    timeout_seconds: float = Field(default=180, gt=0, le=1800)
    max_retries: int = Field(default=3, ge=0, le=10)
    max_schema_retries: int = Field(default=1, ge=0, le=3)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)

    @field_validator("active")
    @classmethod
    def validate_active_provider(cls, value: str) -> str:
        if value not in {"ollama", "mock"}:
            raise ValueError("provider.active must be 'ollama' or 'mock'")
        return value


class WorkflowConfig(BaseModel):
    max_enhancement_cycles: int = Field(default=3, ge=1, le=10)
    auto_apply_severity: str = "low"
    require_human_for_scientific_change: bool = True
    continue_on_quality_failure: bool = True
    stages: list[str]

    @field_validator("stages")
    @classmethod
    def validate_stages(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("workflow.stages cannot be empty")
        unknown = set(value) - KNOWN_STAGES
        if unknown:
            raise ValueError(f"unknown workflow stages: {', '.join(sorted(unknown))}")
        if len(value) != len(set(value)):
            raise ValueError("workflow.stages cannot contain duplicates")
        if "intake" not in value or value[0] != "intake":
            raise ValueError("intake must be the first workflow stage")
        return value


class QualityConfig(BaseModel):
    minimum_stage_score: float = Field(default=0.85, ge=0, le=1)
    maximum_unresolved_high: int = Field(default=0, ge=0)
    require_evidence_for_numeric_claims: bool = True
    require_verified_citations: bool = True


class InteractionConfig(BaseModel):
    mode: str = "minimal"
    question_stage: str = "intake"
    max_question_rounds: int = Field(default=1, ge=0, le=3)
    max_questions_per_round: int = Field(default=5, ge=1, le=10)
    max_total_questions: int = Field(default=5, ge=1, le=25)
    allow_later_stage_questions: bool = False
    reopen_answered_questions: bool = False
    group_related_questions: bool = True

    @model_validator(mode="after")
    def enforce_minimal_interaction_contract(self) -> InteractionConfig:
        if self.mode != "minimal":
            raise ValueError("only interaction.mode='minimal' is supported in this release")
        if self.question_stage != "intake":
            raise ValueError("the single consolidated question gate must be the intake stage")
        if self.max_questions_per_round > self.max_total_questions:
            raise ValueError("max_questions_per_round cannot exceed max_total_questions")
        return self


class IngestionConfig(BaseModel):
    enabled: bool = True
    folders: list[str] = Field(default_factory=lambda: ["inputs", "sources", "data", "figures"])
    max_file_bytes: int = Field(default=25_000_000, ge=1_024)
    max_chars_per_document: int = Field(default=30_000, ge=1_000, le=500_000)


class ContextConfig(BaseModel):
    max_total_evidence_chars: int = Field(default=100_000, ge=5_000, le=1_000_000)
    max_chars_per_evidence: int = Field(default=12_000, ge=500, le=100_000)
    max_manuscript_chars: int = Field(default=120_000, ge=10_000, le=1_000_000)


class AppConfig(BaseModel):
    schema_version: int = 2
    provider: ProviderConfig
    models: dict[str, ModelRoleConfig]
    workflow: WorkflowConfig
    quality: QualityConfig
    interaction: InteractionConfig = Field(default_factory=InteractionConfig)
    ingestion: IngestionConfig = Field(default_factory=IngestionConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    journal: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_model_roles(self) -> AppConfig:
        missing = REQUIRED_MODEL_ROLES - set(self.models)
        if missing:
            raise ValueError(f"missing model roles: {', '.join(sorted(missing))}")
        return self


def load_config(path: Path) -> AppConfig:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if host := os.getenv("OLLAMA_HOST"):
        data.setdefault("provider", {}).setdefault("ollama", {})["host"] = host
    return AppConfig.model_validate(data)
