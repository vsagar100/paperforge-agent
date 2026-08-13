from paperforge.config import InteractionConfig
from paperforge.domain import (
    Question,
    QuestionProposal,
    QuestionStatus,
    ResearchProfile,
    WorkflowState,
)
from paperforge.questions import QuestionManager


def test_question_manager_caps_deduplicates_and_closes_one_round() -> None:
    state = WorkflowState(
        project_id="project",
        profile=ResearchProfile(topic="A complete engineering research topic"),
    )
    manager = QuestionManager(InteractionConfig(max_questions_per_round=3, max_total_questions=3))
    proposals = [
        QuestionProposal(key=f"field_{index}", text=f"Question {index}?", reason="Needed")
        for index in range(5)
    ]
    result = manager.reconcile(state, "intake", proposals, "fingerprint")
    assert [question.id for question in result.accepted] == ["Q-001", "Q-002", "Q-003"]
    assert len(result.suppressed) == 2

    for question in result.accepted:
        manager.answer(state, question.id, f"Answer for {question.id}")
    assert state.intake_closed is True
    assert state.open_questions() == []

    repeated = manager.reconcile(state, "intake", proposals, "fingerprint")
    assert repeated.accepted == []
    assert len(repeated.suppressed) == 5


def test_excess_legacy_open_questions_are_closed_to_the_configured_budget() -> None:
    state = WorkflowState(
        project_id="project",
        profile=ResearchProfile(topic="A complete engineering research topic"),
        pending_questions=[
            Question(
                id=f"Q-{index:03d}",
                key=f"legacy_{index}",
                text=f"Legacy question {index}?",
                reason="Old intake behavior",
            )
            for index in range(1, 19)
        ],
        next_question_number=19,
    )
    manager = QuestionManager(InteractionConfig(max_questions_per_round=5, max_total_questions=5))
    assert manager.enforce_existing_budget(state) == 13
    assert len(state.open_questions()) == 5
    assert (
        sum(question.status == QuestionStatus.DISMISSED for question in state.pending_questions)
        == 13
    )
