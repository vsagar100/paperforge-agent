from __future__ import annotations

import json
import os
import time
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from paperforge.config import AppConfig
from paperforge.providers.base import ModelProvider, ModelRequest, ModelResponse, ProviderError

RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


class OllamaProvider(ModelProvider):
    """Native Ollama REST adapter for direct Cloud and intentionally local hosts."""

    def __init__(self, config: AppConfig, transport: httpx.BaseTransport | None = None) -> None:
        self.config = config
        self.settings = config.provider.ollama
        headers: dict[str, str] = {"Content-Type": "application/json"}
        api_key = os.getenv(self.settings.api_key_env, "").strip()
        if self.is_cloud:
            if not api_key:
                raise ProviderError(
                    f"{self.settings.api_key_env} is required for Ollama Cloud. "
                    "Add it to .env in the PaperForge application directory."
                )
            headers["Authorization"] = f"Bearer {api_key}"
        self.client = httpx.Client(
            base_url=self.settings.host,
            headers=headers,
            timeout=config.provider.timeout_seconds,
            transport=transport,
        )

    @property
    def is_cloud(self) -> bool:
        return (urlparse(self.settings.host).hostname or "").casefold() == "ollama.com"

    def generate(self, request: ModelRequest) -> ModelResponse:
        role_config = self.config.models[request.role]
        prompt = request.prompt
        if request.response_schema:
            prompt += "\n\nReturn JSON only, matching this schema:\n" + json.dumps(
                request.response_schema, separators=(",", ":")
            )
        payload: dict[str, Any] = {
            "model": role_config.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "options": {"temperature": request.temperature},
        }
        if self.settings.structured_outputs and request.response_schema:
            payload["format"] = request.response_schema

        last_error: ProviderError | None = None
        for attempt in range(self.config.provider.max_retries + 1):
            response: httpx.Response | None = None
            try:
                response = self.client.post("/api/chat", json=payload)
            except httpx.RequestError as exc:
                last_error = ProviderError(f"Ollama connection failed: {exc}", retryable=True)
            else:
                if response.is_success:
                    return self._model_response(response, role_config.model)
                last_error = self._http_error(response, role_config.model)

            if not last_error.retryable or attempt >= self.config.provider.max_retries:
                break
            time.sleep(self._retry_delay(attempt, response))
        if last_error is None:
            last_error = ProviderError("Ollama request failed without a response")
        raise last_error

    def healthcheck(self) -> tuple[bool, str]:
        try:
            response = self.client.get("/api/tags")
        except httpx.RequestError as exc:
            return False, f"Ollama connection failed: {exc}"
        if not response.is_success:
            error = self._http_error(response, "model discovery")
            return False, str(error)
        location = "Ollama Cloud" if self.is_cloud else self.settings.host
        return True, f"{location} is reachable"

    def available_models(self) -> list[str]:
        response = self.client.get("/api/tags")
        if not response.is_success:
            raise self._http_error(response, "model discovery")
        try:
            return [str(item["name"]) for item in response.json().get("models", [])]
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError("Ollama returned an invalid model-list response") from exc

    def close(self) -> None:
        self.client.close()

    @staticmethod
    def _model_response(response: httpx.Response, fallback_model: str) -> ModelResponse:
        try:
            body = response.json()
            content = body["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError("Ollama returned a successful but invalid chat response") from exc
        return ModelResponse(
            content=content,
            model=body.get("model", fallback_model),
            provider="ollama",
            prompt_tokens=body.get("prompt_eval_count"),
            completion_tokens=body.get("eval_count"),
        )

    def _http_error(self, response: httpx.Response, model: str) -> ProviderError:
        detail = self._error_detail(response)
        status = response.status_code
        if status == 401:
            message = (
                "Ollama rejected the API key (401 Unauthorized). Create a new key and update .env."
            )
        elif status == 403:
            message = (
                f"Ollama denied access to '{model}' (403 Forbidden): {detail}. "
                "Choose a model included in the account plan or upgrade the plan."
            )
        elif status == 404:
            message = f"Ollama model or endpoint was not found for '{model}': {detail}"
        elif status == 429:
            message = f"Ollama rate or usage limit was reached: {detail}"
        else:
            message = f"Ollama request failed with HTTP {status}: {detail}"
        return ProviderError(
            message,
            status_code=status,
            retryable=status in RETRYABLE_STATUS_CODES,
        )

    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        try:
            body = response.json()
            detail = body.get("error") or body.get("message") or response.reason_phrase
        except ValueError:
            detail = response.text or response.reason_phrase
        return " ".join(str(detail).split())[:800]

    @staticmethod
    def _retry_delay(attempt: int, response: httpx.Response | None) -> float:
        if response is not None and (header := response.headers.get("Retry-After")):
            try:
                return min(float(header), 30.0)
            except ValueError:
                try:
                    delay = (
                        parsedate_to_datetime(header)
                        - parsedate_to_datetime(response.headers["Date"])
                    ).total_seconds()
                    return max(0.0, min(delay, 30.0))
                except (KeyError, TypeError, ValueError):
                    pass
        return min(2**attempt, 8)
