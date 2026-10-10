import json

import httpx
import pytest

from paperforge.config import Model, Settings
from paperforge.llm import Gateway, GatewayFailure
from paperforge.providers import HTTPProvider
from paperforge.schemas import Reply, Review


class Backend:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    def has_credentials(self, model):
        return True

    def close(self):
        pass

    def generate(self, model, system, prompt):
        self.calls.append(model.provider)
        response = next(self.replies)
        if isinstance(response, Exception):
            raise response
        return Reply(
            text=response,
            provider=model.provider,
            requested_model=model.requested_id,
            returned_model="resolved-version",
            input_tokens=100,
            output_tokens=20,
        )


def paid_config(**kwargs):
    return Settings(
        models={
            "paid": Model(
                provider="deepseek",
                billing="paid",
                input_inr_per_million=30,
                output_inr_per_million=120,
            )
        },
        default_routes=["paid"],
        **kwargs,
    )


def test_free_only_never_calls_paid_even_as_fallback(store):
    backend = Backend(["ok"])
    gateway = Gateway(store, paid_config(policy="free_only"), backend)
    with pytest.raises(GatewayFailure, match="billing policy"):
        gateway.call("draft", "writer", "system", "prompt")
    assert backend.calls == []
    assert store.spent() == 0


def test_cap_prevents_request_and_success_is_cached(store):
    backend = Backend(["ok"])
    gateway = Gateway(store, paid_config(budget_inr=0), backend)
    with pytest.raises(GatewayFailure, match="Budget"):
        gateway.call("draft", "writer", "system", "prompt")
    assert not backend.calls
    config = paid_config()
    gateway = Gateway(store, config, backend)
    reply = gateway.call("draft", "writer", "system", "prompt")
    assert reply.returned_model == "resolved-version"
    assert gateway.call("draft", "writer", "system", "prompt") == reply
    assert len(backend.calls) == 1
    assert store.spent() == pytest.approx(0.0054)


def test_timeout_retains_ceiling_then_uses_explicit_free_fallback(store):
    from paperforge.providers import ProviderFailure

    config = paid_config(max_retries=0)
    config.models["local"] = Model()
    config.default_routes = ["paid", "local"]
    config.policy = "paid_only"
    backend = Backend([ProviderFailure("timeout", uncertain=True)])
    gateway = Gateway(store, config, backend)
    with pytest.raises(GatewayFailure):
        gateway.call("draft", "writer", "system", "prompt")
    assert store.spent() > 0
    config.policy = "free_first"
    backend = Backend(["free response"])
    gateway = Gateway(store, config, backend)
    assert gateway.call("draft", "writer", "system", "prompt").provider == "ollama"
    assert backend.calls == ["ollama"]


def test_switch_to_free_route_after_paid_spend_with_zero_new_budget(store):
    paid = Gateway(store, paid_config(), Backend(["paid response"]))
    paid.call("draft", "writer", "s", "p")
    old_spend = store.spent()
    free_backend = Backend(["free response"])
    free = Gateway(store, Settings(policy="free_only", budget_inr=0), free_backend)
    assert free.call("review", "reviewer", "s", "new prompt").text == "free response"
    assert store.spent() == old_spend
    assert free_backend.calls == ["ollama"]


def test_schema_repair_preserves_original_context_and_is_bounded(store, settings):
    settings.max_schema_repairs = 1
    backend = Backend(["not json", json.dumps({"issues": [], "summary": "Reviewed"})])
    gateway = Gateway(store, settings, backend)
    assert (
        gateway.structured(
            Review, stage="review", role="reviewer", system="system", context={"evidence": "fact"}
        ).issues
        == []
    )
    assert len(backend.calls) == 2
    settings.max_context_chars = 1000
    with pytest.raises(GatewayFailure, match="Context exceeds"):
        gateway.structured(
            Review, stage="review", role="reviewer", system="s", context={"text": "a" * 1001}
        )


@pytest.mark.parametrize(
    "provider",
    ["ollama", "gemini", "anthropic", "openai", "groq", "deepseek", "mistral", "openrouter"],
)
def test_wire_adapters_preserve_returned_model_and_usage(provider, monkeypatch):
    model = Model(provider=provider, model="model-snapshot", billing="free")
    monkeypatch.setenv(model.api_key_env, "test-key-do-not-log")
    observed = []

    def handler(request):
        payload = json.loads(request.content)
        observed.append((request, payload))
        if provider == "ollama":
            body = {
                "model": "actual",
                "message": {"content": "ok"},
                "prompt_eval_count": 10,
                "eval_count": 5,
            }
        elif provider == "gemini":
            body = {
                "modelVersion": "actual",
                "candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}],
                "usageMetadata": {
                    "promptTokenCount": 10,
                    "candidatesTokenCount": 3,
                    "thoughtsTokenCount": 2,
                },
            }
        elif provider == "anthropic":
            body = {
                "model": "actual",
                "content": [{"type": "text", "text": "ok"}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }
        else:
            body = {
                "model": "actual",
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
        return httpx.Response(200, json=body)

    backend = HTTPProvider(httpx.MockTransport(handler))
    try:
        response = backend.generate(model, "system", "prompt")
    finally:
        backend.close()
    assert response.returned_model == "actual"
    assert response.input_tokens == 10 and response.output_tokens == 5
    assert "test-key-do-not-log" not in str(observed[0][0].url)
    if provider in {"groq", "openai"}:
        assert "max_completion_tokens" in observed[0][1]


def test_auth_error_does_not_retry_or_leak_body(monkeypatch, store):
    model = Model(provider="gemini", billing="free")
    monkeypatch.setenv("GEMINI_API_KEY", "secret")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(401, json={"error": "secret appeared in response"})

    settings = Settings(models={"remote": model}, default_routes=["remote"])
    gateway = Gateway(
        store, settings, HTTPProvider(httpx.MockTransport(handler)), sleep=lambda _: None
    )
    with pytest.raises(GatewayFailure) as caught:
        gateway.call("draft", "writer", "s", "p")
    assert "secret" not in str(caught.value)
    assert len(calls) == 1
    assert store.spent() == 0
    gateway.close()
