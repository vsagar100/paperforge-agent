import json

import httpx
import pytest
from typer.testing import CliRunner

from paperforge.cli import app
from paperforge.config import Model, load_settings, write_settings
from paperforge.providers import HTTPProvider, ProviderFailure


def test_gemini_model_listing_paginates_and_keeps_credentials_in_headers(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "metadata-test-secret")
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["x-goog-api-key"] == "metadata-test-secret"
        assert "metadata-test-secret" not in str(request.url)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "models/gemini-3.5-flash-lite",
                            "supportedGenerationMethods": ["generateContent"],
                        }
                    ],
                    "nextPageToken": "page-two",
                },
            )
        assert request.url.params["pageToken"] == "page-two"
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-3.8-flash",
                        "supportedGenerationMethods": ["generateContent"],
                    }
                ]
            },
        )

    backend = HTTPProvider(httpx.MockTransport(handler))
    try:
        models = backend.list_models(Model(provider="gemini"))
    finally:
        backend.close()
    assert [m["id"] for m in models] == ["gemini-3.5-flash-lite", "gemini-3.8-flash"]
    assert len(requests) == 2


@pytest.mark.parametrize(
    "provider,body",
    [
        ("ollama", {"models": [{"name": "gpt-oss:20b"}]}),
        ("anthropic", {"data": [{"id": "a-model"}], "has_more": False}),
        ("openai", {"data": [{"id": "a-model"}]}),
    ],
)
def test_other_model_metadata_protocols(provider, body, monkeypatch):
    model = Model(provider=provider, model="a-model")
    monkeypatch.setenv(model.api_key_env, "test-key")
    backend = HTTPProvider(httpx.MockTransport(lambda _: httpx.Response(200, json=body)))
    try:
        assert backend.list_models(model)[0]["id"]
    finally:
        backend.close()


def test_gemini_404_has_actionable_guidance_without_response_secret(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret")
    backend = HTTPProvider(
        httpx.MockTransport(
            lambda _: httpx.Response(404, json={"error": {"message": "test-secret"}})
        )
    )
    try:
        with pytest.raises(ProviderFailure) as caught:
            backend.generate(Model(provider="gemini", model="gemini-2.5-flash"), "system", "prompt")
    finally:
        backend.close()
    assert "paperforge models" in str(caught.value)
    assert "restricted" in str(caught.value)
    assert "test-secret" not in str(caught.value)


def test_model_resource_prefix_and_snapshot_are_normalized():
    model = Model(
        provider="gemini", model="models/gemini-3.5-flash-lite", version="models/complete-snapshot"
    )
    assert model.model == "gemini-3.5-flash-lite"
    assert model.requested_id == "complete-snapshot"


def test_cli_models_is_read_only_and_probe_tests_selected_route_once(store, monkeypatch):
    config = load_settings(store.config_path)
    config.models["google"] = Model(provider="gemini", billing="free")
    config.default_routes = ["local"]
    config.stage_routes = {"review": ["local"]}
    write_settings(store.config_path, config)
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-test-secret")
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"models": [{"name": "models/gemini-3.5-flash-lite"}]})
        assert "gemini-3.5-flash-lite:generateContent" in str(request.url)
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": "OK"}]}, "finishReason": "STOP"}],
                "modelVersion": "returned-snapshot",
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 1},
            },
        )

    original = HTTPProvider.__init__
    monkeypatch.setattr(
        HTTPProvider,
        "__init__",
        lambda self, *_, **__: original(self, httpx.MockTransport(handler)),
    )
    runner = CliRunner()
    before_stages = store.snapshot()["stages"]
    assert runner.invoke(app, ["models", str(store.root), "google"]).exit_code == 0
    for _ in range(2):
        result = runner.invoke(app, ["probe", str(store.root), "google"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["returned_model"] == "returned-snapshot"
        assert "synthetic-test-secret" not in result.output
    assert [r.method for r in requests] == ["GET", "POST", "POST"]
    assert store.snapshot()["stages"] == before_stages
    assert load_settings(store.config_path) == config


def test_probe_paid_route_is_blocked_in_free_only_without_dispatch(store, monkeypatch):
    config = load_settings(store.config_path)
    config.policy = "free_only"
    config.models["paid"] = Model(
        provider="gemini", billing="paid", input_inr_per_million=30, output_inr_per_million=120
    )
    write_settings(store.config_path, config)
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")

    def must_not_dispatch(*_):
        raise AssertionError("Paid call must not be dispatched")

    monkeypatch.setattr(HTTPProvider, "generate", must_not_dispatch)
    assert CliRunner().invoke(app, ["probe", str(store.root), "paid"]).exit_code == 1
    assert store.spent() == 0
