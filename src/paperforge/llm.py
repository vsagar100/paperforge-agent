from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from paperforge.config import AppConfig
from paperforge.providers.base import ModelProvider, ModelRequest, ModelResponse, ProviderError

T = TypeVar("T", bound=BaseModel)
BEGIN_MARKER = "<!-- PAPERFORGE:BEGIN -->"
END_MARKER = "<!-- PAPERFORGE:END -->"


class ModelOutputError(ProviderError):
    pass


class LLMClient:
    """Schema-validation and long-form text boundary around a model provider."""

    def __init__(self, config: AppConfig, provider: ModelProvider) -> None:
        self.config = config
        self.provider = provider

    def structured(
        self,
        model_type: type[T],
        *,
        role: str,
        system: str,
        prompt: str,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[T, ModelResponse]:
        schema = model_type.model_json_schema()
        response: ModelResponse | None = None
        errors: list[str] = []
        current_prompt = prompt
        for attempt in range(self.config.provider.max_schema_retries + 1):
            response = self.provider.generate(
                ModelRequest(
                    role=role,
                    system=system,
                    prompt=current_prompt,
                    response_schema=schema,
                    temperature=0 if attempt else None,
                    metadata={**(metadata or {}), "schema_attempt": attempt + 1},
                )
            )
            try:
                return model_type.model_validate(_extract_json(response.content)), response
            except (json.JSONDecodeError, ValidationError, TypeError) as exc:
                errors.append(str(exc))
                if attempt >= self.config.provider.max_schema_retries:
                    break
                current_prompt = (
                    "Repair the following invalid response. Preserve its factual content and return "
                    "one JSON object only. Do not explain the repair.\n\n"
                    f"Validation error:\n{str(exc)[:1800]}\n\n"
                    f"Invalid response:\n{response.content[:16000]}"
                )
        model = response.model if response else role
        raise ModelOutputError(
            f"Model '{model}' did not return valid {model_type.__name__} JSON after "
            f"{len(errors)} attempt(s): {errors[-1] if errors else 'unknown error'}"
        )

    def text(
        self,
        *,
        role: str,
        system: str,
        prompt: str,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[str, ModelResponse]:
        bounded_prompt = (
            prompt
            + "\n\nReturn only the requested Markdown between these exact boundary markers:\n"
            + BEGIN_MARKER
            + "\n<requested Markdown>\n"
            + END_MARKER
        )
        response = self.provider.generate(
            ModelRequest(
                role=role,
                system=system,
                prompt=bounded_prompt,
                metadata=metadata or {},
            )
        )
        content = _extract_marked_text(response.content)
        if len(content.split()) < 20:
            raise ModelOutputError(
                f"Model '{response.model}' returned an incomplete long-form response."
            )
        return content.rstrip() + "\n", response


def _extract_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        closing = stripped.rfind("```")
        if first_newline >= 0 and closing > first_newline:
            stripped = stripped[first_newline + 1 : closing].strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        if start < 0:
            raise
        value, _ = json.JSONDecoder().raw_decode(stripped[start:])
    if not isinstance(value, dict):
        raise TypeError("the top-level model response must be a JSON object")
    return value


def _extract_marked_text(text: str) -> str:
    if BEGIN_MARKER in text and END_MARKER in text:
        return text.split(BEGIN_MARKER, 1)[1].split(END_MARKER, 1)[0].strip()
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        first_newline = stripped.find("\n")
        return stripped[first_newline + 1 : -3].strip()
    return stripped
