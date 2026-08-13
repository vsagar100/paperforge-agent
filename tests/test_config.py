from pathlib import Path

from paperforge.config import load_config


def test_models_are_assigned_by_role() -> None:
    config = load_config(Path(__file__).parents[1] / "config" / "default.yaml")
    assert config.provider.active == "ollama"
    assert config.models["drafting"].model == "gpt-oss:20b"
    assert config.models["scientific_review"].model == "gpt-oss:20b"
    assert config.interaction.max_question_rounds == 1
    assert config.interaction.allow_later_stage_questions is False
