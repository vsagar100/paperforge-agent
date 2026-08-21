from pathlib import Path

import httpx
import pytest

from paperforge.config import load_config
from paperforge.providers.base import ModelRequest, ProviderError
from paperforge.providers.ollama import OllamaProvider


def test_403_subscription_error_is_clear_and_not_retried(
    monkeypatch: pytest.MonkeyPatch,
    default_config_path: Path,
) -> None:
    config = load_config(default_config_path, persist_migration=False)
    monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            403,
            json={"error": "this model requires a subscription"},
            request=request,
        )

    provider = OllamaProvider(config, transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError, match="requires a subscription") as error:
        provider.generate(ModelRequest(role="planner", system="system", prompt="prompt"))
    provider.close()
    assert error.value.status_code == 403
    assert error.value.retryable is False
    assert calls == 1


def test_cloud_request_does_not_send_unsupported_schema_format(
    monkeypatch: pytest.MonkeyPatch,
    default_config_path: Path,
) -> None:
    config = load_config(default_config_path, persist_migration=False)
    config.provider.ollama.structured_outputs = True
    monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(__import__("json").loads(request.content))
        return httpx.Response(
            200,
            json={"model": "gpt-oss:20b", "message": {"content": "{}"}},
            request=request,
        )

    provider = OllamaProvider(config, transport=httpx.MockTransport(handler))
    provider.generate(
        ModelRequest(
            role="planner",
            system="system",
            prompt="prompt",
            response_schema={"type": "object"},
        )
    )
    provider.close()
    assert "format" not in captured
