import pytest
from pydantic import ValidationError

from paperforge.config import Model, Settings, load_settings, write_settings


def test_defaults_are_resolved_and_roundtrip(tmp_path):
    config = Settings()
    assert config.models["local"].billing == "free"
    assert config.models["local"].requested_id == "gpt-oss:20b"
    assert config.budget_inr == 500
    path = tmp_path / "config.yaml"
    write_settings(path, config)
    assert load_settings(path) == config


@pytest.mark.parametrize(
    "kwargs",
    [
        {"provider": "bad"},
        {"provider": "openai"},
        {"provider": "compatible", "model": "model-v1", "base_url": "http://example.com"},
        {"provider": "ollama", "base_url": "https://key:secret@example.com"},
        {"provider": "gemini", "billing": "paid"},
        {
            "provider": "gemini",
            "billing": "paid",
            "input_inr_per_million": float("nan"),
            "output_inr_per_million": 1,
        },
    ],
)
def test_invalid_or_unpriced_routes_are_rejected(kwargs):
    with pytest.raises(ValidationError):
        Model(**kwargs)


def test_cloud_is_not_assumed_free_and_versions_are_exact():
    assert Model(provider="ollama", base_url="https://ollama.com").billing == "unknown"
    model = Model(provider="gemini", version="exact-provider-snapshot")
    assert model.requested_id == "exact-provider-snapshot"


def test_stage_override_precedes_role_and_default():
    settings = Settings(stage_routes={"draft": ["local"]}, role_routes={"writer": ["local"]})
    assert settings.routes("draft", "writer") == ["local"]
    with pytest.raises(ValidationError):
        Settings(default_routes=["typo"])
    with pytest.raises(ValidationError):
        Settings(stage_routes={"invented": ["local"]})
    with pytest.raises(ValidationError):
        Settings(abstract_min_words=300, abstract_max_words=200)
