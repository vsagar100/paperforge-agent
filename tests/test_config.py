from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from paperforge.config import PIPELINE_STAGES, JournalConfig, load_config


def test_default_configuration_has_complete_v1_pipeline(default_config_path: Path) -> None:
    config = load_config(default_config_path, persist_migration=False)
    assert config.schema_version == 3
    assert config.workflow.stages == list(PIPELINE_STAGES)
    assert set(config.models) == {
        "planner",
        "drafter",
        "reviewer",
        "reviser",
        "final_auditor",
    }
    assert config.paper.topic_only_default.value == "review_article"


def test_v2_configuration_is_backed_up_and_migrated(tmp_path: Path) -> None:
    path = tmp_path / "paperforge.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "provider": {
                    "active": "ollama",
                    "ollama": {
                        "host": "https://ollama.com",
                        "api_key_env": "OLLAMA_API_KEY",
                        "structured_outputs": False,
                    },
                },
                "models": {
                    "drafting": {"model": "draft-model", "temperature": 0.2},
                    "enhancement": {"model": "revise-model", "temperature": 0.1},
                    "scientific_review": {"model": "review-model", "temperature": 0.0},
                    "final_audit": {"model": "audit-model", "temperature": 0.0},
                },
                "journal": {"abstract_max_words": 230, "citation_style": "ieee"},
            }
        ),
        encoding="utf-8",
    )
    config = load_config(path)
    assert (tmp_path / "paperforge.v2.yaml").exists()
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["schema_version"] == 3
    assert config.models["planner"].model == "draft-model"
    assert config.models["reviser"].model == "revise-model"
    assert config.models["reviewer"].model == "review-model"
    assert config.journal.abstract_max_words == 230


def test_unsupported_citation_style_is_rejected_explicitly() -> None:
    with pytest.raises(ValidationError, match="currently supports citation_style: ieee"):
        JournalConfig(citation_style="apa")
