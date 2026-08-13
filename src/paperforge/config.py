from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from paperforge.domain import PaperType

PIPELINE_STAGES = (
    "prepare",
    "plan",
    "literature",
    "synthesis",
    "outline",
    "draft",
    "evidence_review",
    "methodology_review",
    "results_review",
    "writing_review",
    "journal_review",
    "final_review",
    "export",
)

MODEL_ROLES = {"planner", "drafter", "reviewer", "reviser", "final_auditor"}


class ModelConfig(BaseModel):
    model: str = Field(min_length=2)
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)


class OllamaConfig(BaseModel):
    host: str = "https://ollama.com"
    api_key_env: str = "OLLAMA_API_KEY"
    structured_outputs: bool = False
    think: bool | str | None = False
    keep_alive: str | int | None = None

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        clean = value.strip().rstrip("/")
        parsed = urlparse(clean)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Ollama host must be an absolute HTTP(S) URL")
        return clean


class ProviderConfig(BaseModel):
    active: str = "ollama"
    timeout_seconds: float = Field(default=300.0, gt=0, le=1800)
    max_retries: int = Field(default=3, ge=0, le=8)
    max_schema_retries: int = Field(default=2, ge=0, le=4)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)

    @field_validator("active")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        if value not in {"ollama", "mock"}:
            raise ValueError("provider.active must be 'ollama' or 'mock'")
        return value


class WorkflowConfig(BaseModel):
    stages: list[str] = Field(default_factory=lambda: list(PIPELINE_STAGES))
    max_review_cycles: int = Field(default=2, ge=1, le=4)
    sections_per_draft_call: int = Field(default=3, ge=1, le=6)
    resume: bool = True
    invalidate_on_input_change: bool = True
    preserve_legacy_manuscript: bool = True

    @field_validator("stages")
    @classmethod
    def validate_stages(cls, value: list[str]) -> list[str]:
        if value != list(PIPELINE_STAGES):
            raise ValueError(
                "workflow.stages must use the complete ordered PaperForge 1.0 pipeline"
            )
        return value


class PaperConfig(BaseModel):
    requested_type: PaperType = PaperType.AUTO
    topic_only_default: PaperType = PaperType.REVIEW_ARTICLE
    fallback_to_review_without_results: bool = True
    target_word_count: int = Field(default=4500, ge=1200, le=15000)

    @model_validator(mode="after")
    def validate_defaults(self) -> PaperConfig:
        if self.topic_only_default == PaperType.AUTO:
            raise ValueError("paper.topic_only_default cannot be auto")
        return self


class LiteratureConfig(BaseModel):
    enabled: bool = True
    endpoint: str = "https://api.openalex.org"
    crossref_endpoint: str = "https://api.crossref.org"
    api_key_env: str = "OPENALEX_API_KEY"
    contact_email_env: str = "PAPERFORGE_CONTACT_EMAIL"
    min_sources: int = Field(default=12, ge=3, le=100)
    target_sources: int = Field(default=24, ge=3, le=100)
    max_sources: int = Field(default=30, ge=3, le=150)
    results_per_query: int = Field(default=25, ge=5, le=100)
    verify_dois_with_crossref: bool = True
    require_abstract_fraction: float = Field(default=0.35, ge=0.0, le=1.0)
    include_types: list[str] = Field(default_factory=lambda: ["article", "review"])

    @model_validator(mode="after")
    def validate_counts(self) -> LiteratureConfig:
        if not self.min_sources <= self.target_sources <= self.max_sources:
            raise ValueError("literature source counts must satisfy min <= target <= max")
        return self


class IngestionConfig(BaseModel):
    folders: list[str] = Field(default_factory=lambda: ["inputs", "sources", "data", "figures"])
    max_file_bytes: int = Field(default=25_000_000, ge=1024)
    max_chars_per_document: int = Field(default=60_000, ge=1000, le=1_000_000)
    max_total_chars: int = Field(default=240_000, ge=10_000, le=2_000_000)


class QualityConfig(BaseModel):
    minimum_review_score: float = Field(default=0.80, ge=0.0, le=1.0)
    minimum_verified_sources: int = Field(default=12, ge=0, le=100)
    minimum_cited_sources: int = Field(default=10, ge=0, le=100)
    minimum_manuscript_words: int = Field(default=2500, ge=300, le=20000)
    maximum_unresolved_integrity_blockers: int = Field(default=0, ge=0)
    require_verified_citations: bool = True
    require_supported_numeric_claims: bool = True


class JournalConfig(BaseModel):
    name: str | None = None
    manuscript_type: str = "research_article"
    abstract_max_words: int = Field(default=250, ge=50, le=1000)
    citation_style: str = "ieee"
    required_sections: list[str] = Field(default_factory=list)
    required_declarations: list[str] = Field(
        default_factory=lambda: [
            "Data Availability",
            "Conflict of Interest",
            "Funding",
            "Author Contributions",
        ]
    )

    @field_validator("citation_style")
    @classmethod
    def validate_citation_style(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized != "ieee":
            raise ValueError("PaperForge 1.0 currently supports citation_style: ieee")
        return normalized


class ContextConfig(BaseModel):
    max_evidence_chars: int = Field(default=120_000, ge=5_000, le=1_000_000)
    max_reference_abstract_chars: int = Field(default=45_000, ge=5_000, le=300_000)
    max_manuscript_chars: int = Field(default=160_000, ge=10_000, le=1_000_000)


class AppConfig(BaseModel):
    schema_version: int = 3
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    models: dict[str, ModelConfig]
    workflow: WorkflowConfig = Field(default_factory=WorkflowConfig)
    paper: PaperConfig = Field(default_factory=PaperConfig)
    literature: LiteratureConfig = Field(default_factory=LiteratureConfig)
    ingestion: IngestionConfig = Field(default_factory=IngestionConfig)
    quality: QualityConfig = Field(default_factory=QualityConfig)
    journal: JournalConfig = Field(default_factory=JournalConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)

    @model_validator(mode="after")
    def validate_roles(self) -> AppConfig:
        missing = MODEL_ROLES - set(self.models)
        if missing:
            raise ValueError(f"missing model roles: {', '.join(sorted(missing))}")
        return self


def default_config_data() -> dict[str, Any]:
    model = {"model": "gpt-oss:20b", "temperature": 0.1}
    return {
        "schema_version": 3,
        "provider": ProviderConfig().model_dump(mode="json"),
        "models": {
            "planner": {**model, "temperature": 0.1},
            "drafter": {**model, "temperature": 0.2},
            "reviewer": {**model, "temperature": 0.0},
            "reviser": {**model, "temperature": 0.1},
            "final_auditor": {**model, "temperature": 0.0},
        },
        "workflow": WorkflowConfig().model_dump(mode="json"),
        "paper": PaperConfig().model_dump(mode="json"),
        "literature": LiteratureConfig().model_dump(mode="json"),
        "ingestion": IngestionConfig().model_dump(mode="json"),
        "quality": QualityConfig().model_dump(mode="json"),
        "journal": JournalConfig().model_dump(mode="json"),
        "context": ContextConfig().model_dump(mode="json"),
    }


def _migrate_v2(data: dict[str, Any]) -> dict[str, Any]:
    defaults = default_config_data()
    migrated = {**defaults}
    migrated["provider"] = {**defaults["provider"], **data.get("provider", {})}
    if "ollama" in data.get("provider", {}):
        migrated["provider"]["ollama"] = {
            **defaults["provider"]["ollama"],
            **data["provider"]["ollama"],
        }
    legacy_models = data.get("models", {})
    role_sources = {
        "planner": "drafting",
        "drafter": "drafting",
        "reviewer": "scientific_review",
        "reviser": "enhancement",
        "final_auditor": "final_audit",
    }
    for role, source in role_sources.items():
        if source in legacy_models:
            migrated["models"][role] = legacy_models[source]
    legacy_journal = data.get("journal", {})
    migrated["journal"] = {**defaults["journal"], **legacy_journal}
    migrated["journal"]["name"] = migrated["journal"].get("name") or legacy_journal.get(
        "target_journal"
    )
    return migrated


def load_config(path: Path, *, persist_migration: bool = True) -> AppConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Configuration must be a YAML mapping: {path}")
    migrated = int(raw.get("schema_version", 1)) < 3
    data = _migrate_v2(raw) if migrated else raw
    if host := os.getenv("OLLAMA_HOST"):
        data.setdefault("provider", {}).setdefault("ollama", {})["host"] = host
    config = AppConfig.model_validate(data)
    if migrated and persist_migration:
        backup = path.with_name("paperforge.v2.yaml")
        if not backup.exists():
            shutil.copy2(path, backup)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        temporary.replace(path)
    return config
