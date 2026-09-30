from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import yaml
from pydantic import Field, field_validator, model_validator

from paperforge.schemas import StrictModel

ProviderName = Literal[
    "ollama",
    "gemini",
    "anthropic",
    "openai",
    "groq",
    "openrouter",
    "deepseek",
    "mistral",
    "compatible",
]

# Baseline defaults, not a claimed scientific benchmark ranking. Resolve once into project YAML.
PRESETS = {
    "ollama": ("http://localhost:11434", "gpt-oss:20b", "OLLAMA_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com", "gemini-2.5-flash", "GEMINI_API_KEY"),
    "anthropic": ("https://api.anthropic.com", None, "ANTHROPIC_API_KEY"),
    "openai": ("https://api.openai.com/v1", None, "OPENAI_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "openai/gpt-oss-120b", "GROQ_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", None, "OPENROUTER_API_KEY"),
    "deepseek": ("https://api.deepseek.com", "deepseek-flash", "DEEPSEEK_API_KEY"),
    "mistral": ("https://api.mistral.ai/v1", "mistral-small-latest", "MISTRAL_API_KEY"),
    "compatible": (None, None, "LLM_API_KEY"),
}


class Model(StrictModel):
    provider: ProviderName = "ollama"
    model: str | None = None
    version: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    billing: Literal["free", "paid", "unknown"] = "unknown"
    input_inr_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    output_inr_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    temperature: float | None = Field(default=0.1, ge=0, le=2)
    max_output_tokens: int = Field(default=4096, ge=128, le=32768)
    timeout_seconds: float = Field(default=120, gt=0, le=600)
    token_parameter: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"

    @model_validator(mode="after")
    def resolve(self) -> Model:
        url, model, env = PRESETS[self.provider]
        self.base_url = (self.base_url or url or "").rstrip("/")
        self.model = self.model or model
        self.api_key_env = self.api_key_env or env
        if not self.model or not self.model.strip():
            raise ValueError(f"An explicit model ID is required for {self.provider}")
        parsed = urlparse(self.base_url)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("base_url must be an HTTP(S) URL without embedded credentials")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Remote model endpoints must use HTTPS")
        # Only a local Ollama endpoint can be assumed to have no inference bill.
        if self.provider == "ollama" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
            self.billing = "free"
        if self.billing == "paid" and (
            self.input_inr_per_million is None or self.output_inr_per_million is None
        ):
            raise ValueError("Paid routes require explicit INR input/output price ceilings")
        return self

    @property
    def requested_id(self) -> str:
        # Version is the complete provider-native snapshot ID, never a guessed suffix.
        return self.version or self.model or ""


class Ingestion(StrictModel):
    max_file_bytes: int = Field(default=25_000_000, ge=1024, le=500_000_000)
    max_extracted_chars: int = Field(default=120_000, ge=1000, le=2_000_000)
    max_rows: int = Field(default=20000, ge=1, le=100000)
    max_cells: int = Field(default=200000, ge=1, le=1000000)
    max_archive_bytes: int = Field(default=100_000_000, ge=1024, le=500_000_000)
    ocr: bool = False


class Settings(StrictModel):
    schema_version: Literal[1] = 1
    policy: Literal["free_only", "free_first", "paid_only"] = "free_first"
    budget_inr: float = Field(default=500, ge=0, allow_inf_nan=False)
    models: dict[str, Model] = Field(default_factory=lambda: {"local": Model()})
    default_routes: list[str] = Field(default_factory=lambda: ["local"])
    stage_routes: dict[str, list[str]] = Field(default_factory=dict)
    role_routes: dict[str, list[str]] = Field(default_factory=dict)
    max_retries: int = Field(default=2, ge=0, le=5)
    max_schema_repairs: int = Field(default=1, ge=0, le=3)
    max_review_rounds: int = Field(default=2, ge=0, le=4)
    minimum_section_words: int = Field(default=100, ge=1, le=1000)
    abstract_min_words: int = Field(default=200, ge=1, le=500)
    abstract_max_words: int = Field(default=300, ge=1, le=1000)
    minimum_cited_sources: int = Field(default=5, ge=1, le=100)
    minimum_sources: int = Field(default=5, ge=1, le=100)
    target_sources: int = Field(default=35, ge=1, le=100)
    recent_years: int = Field(default=5, ge=1, le=20)
    max_context_chars: int = Field(default=80000, ge=1000, le=200000)
    literature_enabled: bool = True
    ingestion: Ingestion = Field(default_factory=Ingestion)

    @model_validator(mode="after")
    def validate_routes(self) -> Settings:
        if self.minimum_sources > self.target_sources:
            raise ValueError("minimum_sources must not exceed target_sources")
        if self.abstract_min_words > self.abstract_max_words:
            raise ValueError("abstract_min_words must not exceed abstract_max_words")
        if self.minimum_cited_sources > self.target_sources:
            raise ValueError("minimum_cited_sources must not exceed target_sources")
        for route in [self.default_routes, *self.stage_routes.values(), *self.role_routes.values()]:
            if not route or len(set(route)) != len(route):
                raise ValueError("Routes must contain distinct model names")
            if unknown := set(route) - self.models.keys():
                raise ValueError(f"Unknown model routes: {sorted(unknown)}")
        return self

    def routes(self, stage: str, role: str) -> list[str]:
        return self.stage_routes.get(stage, self.role_routes.get(role, self.default_routes))

    @field_validator("stage_routes")
    @classmethod
    def stage_names(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        allowed = {"intake", "literature", "plan", "analysis", "draft", "review", "export"}
        if unknown := value.keys() - allowed:
            raise ValueError(f"Unknown stages: {sorted(unknown)}")
        return value

    @field_validator("role_routes")
    @classmethod
    def role_names(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        allowed = {"extractor", "planner", "writer", "reviewer", "reviser"}
        if unknown := value.keys() - allowed:
            raise ValueError(f"Unknown roles: {sorted(unknown)}")
        return value


def load_settings(path: Path) -> Settings:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("Configuration must be a YAML mapping")
    return Settings.model_validate(raw)


def write_settings(path: Path, settings: Settings) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        yaml.safe_dump(settings.model_dump(mode="json"), sort_keys=False), encoding="utf-8"
    )
    temporary.replace(path)
