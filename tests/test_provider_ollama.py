from pathlib import Path

import httpx
import pytest

from paperforge.config import load_config
from paperforge.providers.base import ModelRequest, ProviderError
from paperforge.providers.ollama import OllamaProvider


def test_403_subscription_error_is_clear_and_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_config(Path(__file__).parents[1] / "config" / "default.yaml")
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
        provider.generate(ModelRequest(role="drafting", system="system", prompt="prompt"))
    provider.close()
    assert error.value.status_code == 403
    assert error.value.retryable is False
    assert calls == 1
