import pytest
import yaml

from paperforge.config import load_config
from paperforge.domain import EvidenceKind
from paperforge.ingestion import DocumentIngestor
from paperforge.storage import ProjectStore


@pytest.mark.parametrize("filename", ["responses.yaml", "responses.yml", "respones.yml"])
def test_legacy_responses_are_authoritative_evidence(
    project_store: ProjectStore,
    filename: str,
) -> None:
    responses = {
        "instructions": "legacy instruction",
        "answers": {
            "Q-001": "Pixhawk Cube and Raspberry Pi 4 were used.",
            "Q-002": "The labelled dataset contains authentic experimental observations.",
            "Q-003": "",
        },
    }
    path = project_store.root / "inputs" / filename
    path.write_text(yaml.safe_dump(responses, sort_keys=False), encoding="utf-8")
    config = load_config(project_store.config_path)

    first = DocumentIngestor(project_store, config.ingestion).refresh()
    evidence = project_store.load_evidence()
    response_item = next(item for item in evidence if item.source_path == f"inputs/{filename}")
    assert first.extracted >= 2
    assert response_item.kind == EvidenceKind.USER_FACT
    assert "Q-001: Pixhawk Cube" in response_item.content
    assert "Q-003" not in response_item.content
    assert response_item.verified is True

    second = DocumentIngestor(project_store, config.ingestion).refresh()
    assert second.unchanged == first.discovered


def test_ingestion_keeps_stable_ids_when_content_changes(
    project_store: ProjectStore,
) -> None:
    source = project_store.root / "data" / "results.csv"
    source.write_text("metric,value\naccuracy,0.968\n", encoding="utf-8")
    config = load_config(project_store.config_path)
    ingestor = DocumentIngestor(project_store, config.ingestion)
    ingestor.refresh()
    before = {item.source_path: item.id for item in project_store.load_evidence()}
    source.write_text("metric,value\naccuracy,0.969\n", encoding="utf-8")
    ingestor.refresh()
    after = {item.source_path: item.id for item in project_store.load_evidence()}
    assert after["data/results.csv"] == before["data/results.csv"]
