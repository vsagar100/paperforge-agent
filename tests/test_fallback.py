import json

import pytest
from typer.testing import CliRunner

from paperforge.cli import app
from paperforge.config import Model, load_settings, write_settings
from paperforge.llm import Gateway, GatewayFailure
from paperforge.providers import ProviderFailure
from paperforge.schemas import Reply, Review, Source, SourceAssessment
from paperforge.store import Store
from paperforge.workflow import Workflow


class Backend:
    def __init__(self, responses):
        self.responses = {name: iter(values) for name, values in responses.items()}
        self.calls = []

    def has_credentials(self, _):
        return True

    def generate(self, model, system, prompt):
        self.calls.append((model.requested_id, prompt))
        value = next(self.responses[model.requested_id])
        if isinstance(value, Exception):
            raise value
        return Reply(
            text=value,
            provider=model.provider,
            requested_model=model.requested_id,
            returned_model=model.requested_id,
            input_tokens=100,
            output_tokens=20,
        )


def chain(settings):
    settings.models = {"first": Model(model="first"), "second": Model(model="second")}
    settings.default_routes = ["first", "second"]
    settings.policy = "free_only"
    settings.max_schema_repairs = 1
    return settings


def test_invalid_schema_falls_back_with_original_task_and_reuses_valid_cache(store, settings):
    settings = chain(settings)
    good = Review(issues=[], summary="Synthetic review").model_dump_json()
    backend = Backend({"first": ["invented invalid output", "still invalid"], "second": [good]})
    gateway = Gateway(store, settings, backend)
    args = dict(stage="review", role="reviewer", system="system", context={"evidence": "fact"})
    assert gateway.structured(Review, **args).summary == "Synthetic review"
    assert [name for name, _ in backend.calls] == ["first", "first", "second"]
    assert "invented invalid output" in backend.calls[1][1]
    assert "invented invalid output" not in backend.calls[2][1]
    assert json.dumps(args["context"]) in backend.calls[2][1]
    assert gateway.structured(Review, **args).summary == "Synthetic review"
    assert len(backend.calls) == 3


def test_appraisal_quote_failure_uses_free_fallback_and_preserves_checkpoints(store, settings):
    settings = chain(settings)
    text = "A thermal method was evaluated."
    sources = [
        Source(id=name, title="Synthetic source", url="https://example.org/test", abstract=text)
        for name in ["SRC-one", "SRC-two"]
    ]

    def response(name, quote):
        return SourceAssessment(
            source_id=name,
            category="thermal",
            method={"summary": "An approach was tested.", "quote": quote},
        ).model_dump_json()

    class Literature:
        def search(self, *_):
            return sources

    backend = Backend(
        {
            "first": [
                response("SRC-one", text),
                response("SRC-two", "Invented quote"),
                response("SRC-two", "Still invented"),
            ],
            "second": [response("SRC-two", text)],
        }
    )
    workflow = Workflow(store, settings, Gateway(store, settings, backend), Literature())
    assert workflow.run(until="literature")["status"] == "pending"
    assert [name for name, _ in backend.calls] == ["first", "first", "first", "second"]
    assert len(store.stage("literature")["output"]["assessments"]) == 2
    resumed = Workflow(Store(store.root), settings, Gateway(store, settings, backend), Literature())
    resumed.run(until="literature")
    assert len(backend.calls) == 4 and store.stage("intake")["attempt"] == 1
    assert store.spent() == 0


@pytest.mark.parametrize("structured", [False, True])
def test_429_switches_to_next_free_route_without_retry_sleep(store, settings, structured):
    settings = chain(settings)
    good = Review(issues=[], summary="Reviewed").model_dump_json()
    backend = Backend(
        {
            "first": [ProviderFailure("HTTP 429", retryable=True, retry_after=45, status_code=429)],
            "second": [good],
        }
    )
    waits = []
    gateway = Gateway(store, settings, backend, sleep=waits.append)
    if structured:
        assert (
            gateway.structured(
                Review, stage="review", role="reviewer", system="s", context={}
            ).summary
            == "Reviewed"
        )
    else:
        assert gateway.call("review", "reviewer", "s", "p").requested_model == "second"
    assert [name for name, _ in backend.calls] == ["first", "second"]
    assert waits == []
    assert store.get(gateway._limit_key(settings.models["first"]))["retry_at"] > gateway.clock()


def test_all_invalid_routes_stop_at_bounded_limit_without_paid_or_unlisted_calls(store, settings):
    settings = chain(settings)
    settings.models["paid"] = Model(
        provider="deepseek",
        model="paid",
        billing="paid",
        input_inr_per_million=30,
        output_inr_per_million=120,
    )
    settings.models["unlisted"] = Model(model="unlisted")
    settings.default_routes.append("paid")
    backend = Backend({"first": ["bad one", "bad repair"], "second": ["bad two", "bad repair"]})
    with pytest.raises(GatewayFailure, match="all configured routes") as caught:
        Gateway(store, settings, backend).structured(
            Review, stage="review", role="reviewer", system="s", context={}
        )
    assert [name for name, _ in backend.calls] == ["first", "first", "second", "second"]
    assert "paid: credentials or billing policy" in str(caught.value)
    assert store.spent() == 0


def test_provider_failure_on_repair_moves_to_next_route_with_original_context(store, settings):
    settings = chain(settings)
    good = Review(issues=[], summary="Reviewed").model_dump_json()
    backend = Backend(
        {"first": ["invalid", ProviderFailure("HTTP 401", status_code=401)], "second": [good]}
    )
    result = Gateway(store, settings, backend).structured(
        Review, stage="review", role="reviewer", system="s", context={"source": "real"}
    )
    assert result.summary == "Reviewed"
    assert [name for name, _ in backend.calls] == ["first", "first", "second"]
    assert "Invalid output" not in backend.calls[-1][1]


def test_free_route_command_uses_configured_credentials_and_excludes_paid_or_unknown(
    store, monkeypatch
):
    settings = load_settings(store.config_path)
    settings.models.update(
        google=Model(provider="gemini", billing="free"),
        backup=Model(provider="gemini", model="gemini-3.8-flash", billing="free"),
        no_key=Model(provider="groq", billing="free", api_key_env="UNSET_TEST_FALLBACK_KEY"),
        paid=Model(
            provider="openai",
            model="paid",
            billing="paid",
            input_inr_per_million=10,
            output_inr_per_million=20,
        ),
        unknown=Model(provider="compatible", model="unknown", base_url="https://example.org/v1"),
    )
    settings.default_routes = ["google"]
    settings.policy = "paid_only"
    write_settings(store.config_path, settings)
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-secret")
    monkeypatch.delenv("UNSET_TEST_FALLBACK_KEY", raising=False)
    runner = CliRunner()
    before = store.snapshot()["stages"]
    result = runner.invoke(app, ["route", str(store.root), "--free"])
    assert result.exit_code == 0, result.output
    saved = load_settings(store.config_path)
    assert saved.default_routes == ["google", "backup", "local"]
    assert saved.policy == "free_only"
    assert "synthetic-secret" not in result.output
    assert store.snapshot()["stages"] == before
    assert (
        runner.invoke(
            app, ["route", str(store.root), "backup", "google", "--free", "--stage", "literature"]
        ).exit_code
        == 0
    )
    assert load_settings(store.config_path).stage_routes["literature"] == ["backup", "google"]
    assert runner.invoke(app, ["route", str(store.root), "paid", "--free"]).exit_code != 0
    assert runner.invoke(app, ["route", str(store.root), "missing", "--free"]).exit_code != 0
