from pathlib import Path

from paperforge.config import load_config
from paperforge.domain import EvidenceKind, ResearchProfile
from paperforge.ingestion import DocumentIngestor
from paperforge.storage import ProjectStore


def test_ingestion_is_incremental_and_uses_stable_evidence_ids(tmp_path: Path) -> None:
    default = Path(__file__).parents[1] / "config" / "default.yaml"
    store = ProjectStore(tmp_path / "paper")
    store.initialize(ResearchProfile(topic="Thermal sensing engineering experiment"), default)
    source = store.root / "sources" / "paper.txt"
    source.write_text("Published method and operating conditions.", encoding="utf-8")
    data = store.root / "data" / "results.csv"
    data.write_text("trial,accuracy\n1,96.8\n", encoding="utf-8")
    config = load_config(store.config_path)
    ingestor = DocumentIngestor(store, config.ingestion)

    first = ingestor.refresh()
    evidence = store.load_evidence()
    ids = {item.source_path: item.id for item in evidence}
    assert first.extracted == 3  # research brief + source + data
    assert {item.kind for item in evidence} >= {
        EvidenceKind.USER_STATEMENT,
        EvidenceKind.SOURCE,
        EvidenceKind.EXPERIMENTAL,
    }

    second = ingestor.refresh()
    assert second.extracted == 0
    assert second.unchanged == 3

    source.write_text("Updated published method and conditions.", encoding="utf-8")
    third = ingestor.refresh()
    updated = {item.source_path: item.id for item in store.load_evidence()}
    assert third.extracted == 1
    assert updated["sources/paper.txt"] == ids["sources/paper.txt"]
