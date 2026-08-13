from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class ProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


@dataclass(slots=True)
class ModelRequest:
    role: str
    system: str
    prompt: str
    response_schema: dict[str, Any] | None = None
    temperature: float = 0.1
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModelResponse:
    content: str
    model: str
    provider: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class ModelProvider(ABC):
    @abstractmethod
    def generate(self, request: ModelRequest) -> ModelResponse:
        """Generate a response using a logical model role."""

    @abstractmethod
    def healthcheck(self) -> tuple[bool, str]:
        """Return provider availability and a human-readable status."""

    def close(self) -> None:
        """Release provider resources when applicable."""
        return None
