import pytest

from paperforge.config import Settings
from paperforge.schemas import Project
from paperforge.store import Store


@pytest.fixture
def settings():
    return Settings(
        minimum_sources=1,
        target_sources=3,
        minimum_cited_sources=1,
        minimum_section_words=5,
        abstract_min_words=5,
        max_schema_repairs=0,
    )


@pytest.fixture
def store(tmp_path, settings):
    return Store.create(
        tmp_path / "paper", Project(topic="Thermal monitoring of engineering systems"), settings
    )
