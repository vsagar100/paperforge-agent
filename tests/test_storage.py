from pathlib import Path

import pytest
import yaml

from paperforge.domain import Question, QuestionRound, ResearchProfile, StageStatus
from paperforge.storage import ProjectStore


def test_project_initialization_and_versioning(tmp_path: Path) -> None:
    default = Path(__file__).parents[1] / "config" / "default.yaml"
    store = ProjectStore(tmp_path / "paper")
    state = store.initialize(
        ResearchProfile(topic="A sufficiently detailed engineering topic"), default
    )
    assert store.state_path.exists()
    assert store.load_state().project_id == state.project_id
    version = store.save_manuscript_version("# Abstract\nDraft", "initial_draft", state)
    assert version.name == "v001-initial_draft.md"
    assert store.read_manuscript().endswith("Draft")


def test_legacy_answered_intake_deadlock_is_migrated(tmp_path: Path) -> None:
    default = Path(__file__).parents[1] / "config" / "default.yaml"
    store = ProjectStore(tmp_path / "paper")
    state = store.initialize(
        ResearchProfile(topic="A legacy engineering research workflow"), default
    )
    payload = state.model_dump(mode="json")
    payload["schema_version"] = 1
    payload["stage_status"] = {"intake": "needs_input"}
    payload["pending_questions"] = [
        {
            "id": "Q6",
            "text": "What communication method was used?",
            "reason": "Required for the implementation description.",
            "blocking": True,
            "answer": "Wi-Fi with local buffering.",
        }
    ]
    for key in (
        "question_rounds",
        "closed_question_keys",
        "next_question_number",
        "intake_closed",
        "source_fingerprint",
        "workflow_completed",
        "submission_ready",
        "completed_at",
    ):
        payload.pop(key, None)
    store.write_json("audit/state.json", payload)

    migrated = store.load_state()
    assert migrated.schema_version == 2
    assert migrated.pending_questions[0].id == "Q-006"
    assert migrated.intake_closed is True
    assert migrated.stage_status["intake"] == StageStatus.PENDING


def test_needs_input_without_an_open_question_is_repaired_in_schema_two(tmp_path: Path) -> None:
    default = Path(__file__).parents[1] / "config" / "default.yaml"
    store = ProjectStore(tmp_path / "paper")
    state = store.initialize(
        ResearchProfile(topic="A resilient engineering workflow project"), default
    )
    state.stage_status["intake"] = StageStatus.NEEDS_INPUT
    store.save_state(state)
    assert store.load_state().stage_status["intake"] == StageStatus.PENDING


def test_response_template_never_erases_an_uncommitted_user_answer(tmp_path: Path) -> None:
    default = Path(__file__).parents[1] / "config" / "default.yaml"
    store = ProjectStore(tmp_path / "paper")
    state = store.initialize(
        ResearchProfile(topic="A robust engineering response persistence study"), default
    )
    question = Question(
        id="Q-001",
        key="study_facts",
        text="Provide the consolidated study facts.",
        reason="Required for evidence-grounded drafting.",
    )
    state.pending_questions.append(question)
    state.question_rounds.append(
        QuestionRound(stage="intake", number=1, question_ids=[question.id])
    )
    state.stage_status["intake"] = StageStatus.NEEDS_INPUT
    store.save_state(state)
    store.write_response_template(state)

    payload = yaml.safe_load(store.response_template_path.read_text(encoding="utf-8"))
    payload["answers"]["Q-001"] = "User-entered scientific evidence."
    store.response_template_path.write_text(
        yaml.safe_dump(payload, sort_keys=False), encoding="utf-8"
    )

    # This is the write that v0.2.0 performed at the beginning of every run.
    store.write_response_template(store.load_state())
    assert store.read_response_answers()["Q-001"] == "User-entered scientific evidence."


def test_invalid_response_yaml_is_reported_without_rewriting_the_file(tmp_path: Path) -> None:
    default = Path(__file__).parents[1] / "config" / "default.yaml"
    store = ProjectStore(tmp_path / "paper")
    state = store.initialize(
        ResearchProfile(topic="A response-file integrity engineering study"), default
    )
    invalid = "answers:\n  Q-001: [unterminated\n"
    store.response_template_path.write_text(invalid, encoding="utf-8")

    with pytest.raises(ValueError, match="invalid YAML and was left unchanged"):
        store.write_response_template(state)
    assert store.response_template_path.read_text(encoding="utf-8") == invalid
