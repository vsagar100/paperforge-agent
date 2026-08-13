from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from paperforge.config import InteractionConfig
from paperforge.domain import (
    Question,
    QuestionProposal,
    QuestionRound,
    QuestionStatus,
    WorkflowState,
    normalize_question_key,
    utc_now,
)


@dataclass(slots=True)
class ReconciliationResult:
    accepted: list[Question]
    suppressed: list[QuestionProposal]
    reason: str | None = None


class QuestionManager:
    """Owns the one-round, provider-independent interaction contract."""

    def __init__(self, policy: InteractionConfig) -> None:
        self.policy = policy

    def reconcile(
        self,
        state: WorkflowState,
        stage: str,
        proposals: list[QuestionProposal],
        context_fingerprint: str | None,
    ) -> ReconciliationResult:
        candidates = [proposal for proposal in proposals if proposal.blocking]
        if not candidates:
            return ReconciliationResult([], [])

        if stage != self.policy.question_stage and not self.policy.allow_later_stage_questions:
            return ReconciliationResult(
                [], candidates, "Questions are restricted to the consolidated intake gate."
            )

        rounds = [
            question_round
            for question_round in state.question_rounds
            if question_round.stage == stage
        ]
        if len(rounds) >= self.policy.max_question_rounds:
            return ReconciliationResult(
                [], candidates, "The configured question-round budget is already closed."
            )

        existing = {question.key: question for question in state.pending_questions}
        accepted: list[Question] = []
        suppressed: list[QuestionProposal] = []
        seen_keys: set[str] = set()
        remaining_total = max(0, self.policy.max_total_questions - len(state.pending_questions))
        limit = min(self.policy.max_questions_per_round, remaining_total)

        for proposal in candidates:
            key = normalize_question_key(proposal.key or proposal.text)
            duplicate = existing.get(key) or self._similar_question(state, proposal.text)
            if duplicate or key in seen_keys:
                suppressed.append(proposal)
                continue
            if len(accepted) >= limit:
                suppressed.append(proposal)
                continue
            question = Question(
                id=f"Q-{state.next_question_number:03d}",
                key=key,
                text=proposal.text.strip(),
                reason=proposal.reason.strip(),
                stage=stage,
                blocking=True,
            )
            state.next_question_number += 1
            accepted.append(question)
            seen_keys.add(key)

        if accepted:
            state.pending_questions.extend(accepted)
            state.question_rounds.append(
                QuestionRound(
                    stage=stage,
                    number=len(rounds) + 1,
                    question_ids=[question.id for question in accepted],
                    context_fingerprint=context_fingerprint,
                )
            )
            state.intake_closed = False
        reason = None
        if suppressed:
            reason = "Duplicate or over-budget questions were converted to review findings."
        return ReconciliationResult(accepted, suppressed, reason)

    def answer(self, state: WorkflowState, question_id_or_key: str, answer: str) -> Question:
        clean_answer = answer.strip()
        if not clean_answer:
            raise ValueError("An answer cannot be empty. Use 'not available' when appropriate.")
        lookup = question_id_or_key.strip().casefold()
        for question in state.pending_questions:
            if question.id.casefold() == lookup or question.key.casefold() == lookup:
                question.answer = clean_answer
                question.status = QuestionStatus.ANSWERED
                question.answered_at = utc_now()
                state.closed_question_keys.add(question.key)
                self.close_completed_rounds(state)
                return question
        raise KeyError(f"Unknown question ID or key: {question_id_or_key}")

    def close_completed_rounds(self, state: WorkflowState) -> None:
        by_id = {question.id: question for question in state.pending_questions}
        for question_round in state.question_rounds:
            questions = [by_id[item] for item in question_round.question_ids if item in by_id]
            if questions and all(not question.is_open for question in questions):
                question_round.closed_at = question_round.closed_at or utc_now()
        intake_rounds = [
            question_round
            for question_round in state.question_rounds
            if question_round.stage == self.policy.question_stage
        ]
        if intake_rounds and all(question_round.closed_at for question_round in intake_rounds):
            state.intake_closed = True

    def enforce_existing_budget(self, state: WorkflowState) -> int:
        """Close excess legacy questions created before the one-round policy existed."""
        open_questions = state.open_questions()
        answered_count = sum(bool(question.answer) for question in state.pending_questions)
        remaining = max(0, self.policy.max_total_questions - answered_count)
        excess = open_questions[remaining:]
        for question in excess:
            question.status = QuestionStatus.DISMISSED
            question.answered_at = utc_now()
            state.closed_question_keys.add(question.key)
        self.close_completed_rounds(state)
        return len(excess)

    @staticmethod
    def _similar_question(state: WorkflowState, text: str) -> Question | None:
        normalized = " ".join(text.casefold().split())
        for question in state.pending_questions:
            existing = " ".join(question.text.casefold().split())
            if SequenceMatcher(None, normalized, existing).ratio() >= 0.88:
                return question
        return None
