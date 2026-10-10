from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest
from typer.testing import CliRunner

from paperforge.cli import app
from paperforge.config import Model, load_settings
from paperforge.llm import Gateway, GatewayFailure
from paperforge.providers import HTTPProvider, ProviderFailure
from paperforge.schemas import Reply
from paperforge.store import Store


def google_error(*details):
    return {"error": {"message": "response-secret-must-not-leak", "details": list(details)}}


@pytest.mark.parametrize("header,delay", [("45", 45), ("-1", 0), ("nan", 0), ("inf", 0)])
def test_retry_header_is_not_shortened_and_rejects_nonfinite_values(header, delay):
    response = httpx.Response(429, headers={"Retry-After": header})
    assert HTTPProvider.retry_details(response, Model())[0] == delay


def test_http_date_retry_header_is_supported():
    future = datetime.now(UTC) + timedelta(days=3)
    response = httpx.Response(429, headers={"Retry-After": format_datetime(future)})
    assert 259190 < HTTPProvider.retry_details(response, Model())[0] <= 259200


def test_gemini_retryinfo_and_header_use_larger_delay_without_leaking_body(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "key-secret")
    body = google_error(
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "47.25s"}
    )
    backend = HTTPProvider(
        httpx.MockTransport(lambda _: httpx.Response(429, headers={"Retry-After": "35"}, json=body))
    )
    try:
        with pytest.raises(ProviderFailure) as caught:
            backend.generate(Model(provider="gemini"), "system", "prompt")
    finally:
        backend.close()
    assert caught.value.retryable
    assert caught.value.retry_after == 47.25
    assert "47.25 seconds" in str(caught.value)
    assert "AI Studio" in str(caught.value)
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    "violation,hint",
    [
        ({"quotaId": "GenerateRequestsPerDayPerProjectPerModel"}, "daily quota"),
        ({"quotaMetric": "service/generate_content_requests_per_day"}, "daily quota"),
        ({"quotaValue": "0"}, "zero quota"),
    ],
)
def test_reported_daily_or_zero_quota_does_not_retry(violation, hint, monkeypatch, store, settings):
    monkeypatch.setenv("GEMINI_API_KEY", "key-secret")
    settings.models = {"google": Model(provider="gemini", billing="free")}
    settings.default_routes = ["google"]
    body = google_error(
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [violation]}
    )
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, json=body)

    gateway = Gateway(store, settings, HTTPProvider(httpx.MockTransport(handler)))
    try:
        with pytest.raises(GatewayFailure, match=hint) as caught:
            gateway.call("literature", "extractor", "system", "prompt")
    finally:
        gateway.close()
    assert len(calls) == 1
    assert "secret" not in str(caught.value)
    assert store.spent() == 0


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"error": "secret"},
        google_error(None),
        google_error(
            {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": "secret"}
        ),
    ],
)
def test_malformed_quota_metadata_does_not_hide_rate_limit(body):
    delay, permanent, hint = HTTPProvider.retry_details(
        httpx.Response(429, json=body), Model(provider="gemini")
    )
    assert (delay, permanent, hint) == (0, False, "")


class Clock:
    def __init__(self):
        self.time = 1000.0
        self.waits = []

    def now(self):
        return self.time

    def sleep(self, duration):
        self.waits.append(duration)
        self.time += duration


class Backend:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    def has_credentials(self, _):
        return True

    def generate(self, model, *_):
        self.calls += 1
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return Reply(
            text=response,
            provider=model.provider,
            requested_model=model.requested_id,
            returned_model=model.requested_id,
        )


def test_retryinfo_delay_is_honored_before_retry_and_cached_success_is_immediate(store, settings):
    clock = Clock()
    backend = Backend([ProviderFailure("rate limit", retryable=True, retry_after=45), "ok"])
    gateway = Gateway(store, settings, backend, sleep=clock.sleep, clock=clock.now)
    assert gateway.call("literature", "extractor", "s", "p").text == "ok"
    assert clock.waits == [45]
    assert backend.calls == 2
    assert gateway.call("literature", "extractor", "s", "p").text == "ok"
    assert clock.waits == [45]
    assert backend.calls == 2


def test_long_cooldown_survives_resume_and_sends_no_premature_requests(store, settings):
    clock = Clock()
    backend = Backend([ProviderFailure("rate limit", retryable=True, retry_after=120)])
    gateway = Gateway(store, settings, backend, sleep=clock.sleep, clock=clock.now)
    with pytest.raises(GatewayFailure, match="cooldown saved for 120"):
        gateway.call("literature", "extractor", "s", "p")
    assert backend.calls == 1 and clock.waits == []
    reopened = Store(store.root)
    replacement = Backend(["ok"])
    resumed = Gateway(reopened, settings, replacement, sleep=clock.sleep, clock=clock.now)
    with pytest.raises(GatewayFailure, match="cooldown"):
        resumed.call("literature", "extractor", "s", "p")
    assert replacement.calls == 0 and store.spent() == 0
    clock.time += 120
    assert resumed.call("literature", "extractor", "s", "p").text == "ok"
    assert replacement.calls == 1


def test_pacing_applies_across_roles_and_processes_but_not_cached_replies(store, settings):
    clock = Clock()
    settings.models["local"].min_interval_seconds = 20
    backend = Backend(["one", "two", "three"])
    gateway = Gateway(store, settings, backend, sleep=clock.sleep, clock=clock.now)
    gateway.call("literature", "extractor", "s", "one")
    assert clock.waits == []
    gateway.call("plan", "planner", "s", "two")
    assert clock.waits == [20]
    gateway.call("plan", "planner", "s", "two")
    assert clock.waits == [20]
    resumed = Gateway(Store(store.root), settings, backend, sleep=clock.sleep, clock=clock.now)
    resumed.call("draft", "writer", "s", "three")
    assert clock.waits == [20, 20]
    assert backend.calls == 3


def test_long_cooldown_can_use_only_explicit_permitted_fallback(store, settings):
    clock = Clock()
    settings.models["other"] = Model(model="other-local-model")
    settings.default_routes = ["local", "other"]
    backend = Backend([ProviderFailure("rate limit", retryable=True, retry_after=120), "ok"])
    gateway = Gateway(store, settings, backend, sleep=clock.sleep, clock=clock.now)
    assert gateway.call("literature", "extractor", "s", "p").text == "ok"
    assert backend.calls == 2 and clock.waits == []


def test_cli_pacing_is_saved_and_does_not_reset_stages(store):
    runner = CliRunner()
    before = store.snapshot()["stages"]
    result = runner.invoke(
        app,
        [
            "model",
            str(store.root),
            "google",
            "gemini",
            "--billing",
            "free",
            "--min-interval-seconds",
            "20",
            "--select",
        ],
    )
    assert result.exit_code == 0, result.output
    assert load_settings(store.config_path).models["google"].min_interval_seconds == 20
    assert store.snapshot()["stages"] == before
