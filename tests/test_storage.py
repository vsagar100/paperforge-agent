from pathlib import Path

from paperforge.domain import ResearchProfile
from paperforge.storage import ProjectStore


def test_v2_state_migration_preserves_legacy_manuscript_and_responses(
    tmp_path: Path,
    default_config_path: Path,
) -> None:
    store = ProjectStore(tmp_path / "paper")
    store.initialize(
        ResearchProfile(topic="Legacy UAV thermal fire detection workflow"),
        default_config_path,
    )
    store.write_text("manuscript/current.md", "# Legacy manuscript\n\nPreserve this text.")
    responses = "answers:\n  Q-001: Authentic system details\n"
    store.write_text("inputs/responses.yaml", responses)
    store.write_json(
        "evidence/registry.json",
        [
            {
                "id": "EV-FILE-LEGACY",
                "kind": "user_statement",
                "title": "Legacy user fact",
                "content": "A legacy evidence fact.",
                "source_path": "inputs/legacy.md",
                "verified": True,
                "metadata": {"auto_ingested": True},
            }
        ],
    )
    store.write_json(
        "audit/state.json",
        {
            "schema_version": 2,
            "project_id": "legacy-project",
            "profile": {
                "topic": "Legacy UAV thermal fire detection workflow",
                "domain": "engineering",
                "target_journal": "DJES",
                "objectives": ["Evaluate authentic UAV detection results."],
                "contribution": "An edge-cloud UAV prototype.",
            },
            "pending_questions": [{"id": "Q-001", "answer": "Authentic system details"}],
        },
    )
    state = store.load_state()
    assert state.schema_version == 5
    assert state.project_id == "legacy-project"
    assert state.stage_status == {}
    assert (store.root / "audit" / "state.v2.json").exists()
    assert (store.root / "manuscript" / "versions" / "legacy-v0.2.1.md").exists()
    assert (store.root / "inputs" / "legacy-profile.md").exists()
    assert store.read_manuscript().startswith("# Legacy manuscript")
    assert store.root.joinpath("inputs/responses.yaml").read_text(encoding="utf-8") == responses
    evidence = store.load_evidence()
    assert evidence[0].kind.value == "user_fact"
    assert (store.root / "evidence" / "registry.v2.json").exists()


def test_input_fingerprint_changes_only_for_user_controlled_inputs(
    project_store: ProjectStore,
) -> None:
    state = project_store.load_state()
    before = project_store.input_fingerprint(state)
    project_store.write_text("outputs/generated.md", "generated output")
    assert project_store.input_fingerprint(state) == before
    project_store.write_text("inputs/research_brief.md", "updated user evidence")
    assert project_store.input_fingerprint(state) != before


def test_v2_state_migrates_to_research_first_workflow(
    tmp_path: Path,
    default_config_path: Path,
) -> None:
    store = ProjectStore(tmp_path / "v2-paper")
    state = store.initialize(
        ResearchProfile(topic="Legacy PaperForge 2.0 thermal experiment"),
        default_config_path,
    )
    store.write_text("manuscript/current.md", "# PaperForge 2.0 manuscript\n\nPreserve me.\n")
    payload = state.model_dump(mode="json")
    payload["schema_version"] = 4
    payload["stage_status"] = {"prepare": "passed"}
    store.write_json("audit/state.json", payload)

    migrated = store.load_state()

    assert migrated.schema_version == 5
    assert migrated.stage_status == {}
    assert (store.root / "audit" / "state.v4.json").exists()
    assert (store.root / "manuscript" / "versions" / "legacy-v2.0.0.md").exists()
    assert any("research-first validation workflow" in note for note in migrated.migration_notes)
