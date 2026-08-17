import pytest
import yaml

from paperforge.config import load_config
from paperforge.domain import (
    AuthorValidationDecision,
    AuthorValidationItem,
    AuthorValidationPackage,
    EvidenceCoverageStatus,
    EvidenceKind,
    PaperType,
    RequirementLevel,
)
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


def test_only_resolved_author_validation_becomes_evidence_and_changes_fingerprint(
    project_store: ProjectStore,
) -> None:
    state = project_store.load_state()
    before = project_store.input_fingerprint(state)
    item = AuthorValidationItem(
        id="VAL-ACQUISITION-PROTOCOL",
        requirement_code="acquisition_protocol",
        label="Experimental acquisition protocol",
        level=RequirementLevel.DRAFT_BLOCKING,
        evidence_status=EvidenceCoverageStatus.PARTIAL,
        why_relevant="Sampling conditions define the experimental boundary.",
        question="How many flights were conducted?",
        literature_context=["REF001 — Reporting context only."],
        reference_ids=["REF001"],
        manuscript_treatment="Disclose the missing detail.",
    )
    package = AuthorValidationPackage(
        topic="UAV thermal fire detection",
        paper_type=PaperType.ORIGINAL_RESEARCH,
        instructions="Change only decision and answer.",
        items=[item],
    )
    project_store.save_author_validation(package)
    assert project_store.input_fingerprint(state) == before

    config = load_config(project_store.config_path)
    DocumentIngestor(project_store, config.ingestion).refresh()
    assert not any(
        evidence.id == "EV-AUTHOR-VALIDATION" for evidence in project_store.load_evidence()
    )

    item.decision = AuthorValidationDecision.PROVIDED
    item.answer = "The reported dataset was acquired during six flights grouped by flight."
    project_store.save_author_validation(package)
    assert project_store.input_fingerprint(state) != before

    DocumentIngestor(project_store, config.ingestion).refresh()
    evidence = next(
        evidence
        for evidence in project_store.load_evidence()
        if evidence.id == "EV-AUTHOR-VALIDATION"
    )
    assert "six flights grouped by flight" in evidence.content
    assert "REF001" not in evidence.content
    assert "Reporting context only" not in evidence.content
