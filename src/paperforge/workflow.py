from __future__ import annotations

from paperforge.config import AppConfig
from paperforge.domain import Finding, Severity, StageResult, StageStatus, utc_now
from paperforge.exporters import ExportReport, OutputExporter
from paperforge.ingestion import DocumentIngestor, IngestionReport
from paperforge.questions import QuestionManager
from paperforge.stages import StageExecutor
from paperforge.storage import ProjectStore


class WorkflowEngine:
    def __init__(self, store: ProjectStore, config: AppConfig, executor: StageExecutor) -> None:
        self.store = store
        self.config = config
        self.executor = executor
        self.questions = QuestionManager(config.interaction)
        self.last_ingestion_report = IngestionReport()
        self.last_export_report = ExportReport()
        self.last_imported_answers = 0

    def run(self) -> list[StageResult]:
        with self.store.workflow_lock():
            self.last_imported_answers = 0
            state = self.store.load_state()
            self._repair_interrupted_state(state)
            dismissed = self.questions.enforce_existing_budget(state)
            if dismissed:
                self.store.save_state(state)

            self.last_imported_answers = self._import_response_answers(state)
            self.last_ingestion_report = DocumentIngestor(
                self.store, self.config.ingestion
            ).refresh()
            state.source_fingerprint = self.last_ingestion_report.fingerprint
            self.questions.close_completed_rounds(state)

            open_questions = state.open_questions()
            if open_questions:
                for stage in {question.stage for question in open_questions}:
                    state.stage_status[stage] = StageStatus.NEEDS_INPUT
                state.current_stage = open_questions[0].stage
                self.store.save_state(state)
                self.store.write_response_template(state)
                return []

            results: list[StageResult] = []
            for stage in self.config.workflow.stages:
                state = self.store.load_state()
                if state.stage_status.get(stage, StageStatus.PENDING).advances_workflow:
                    continue

                state.current_stage = stage
                state.stage_status[stage] = StageStatus.RUNNING
                self.store.save_state(state)

                if stage == self.config.interaction.question_stage and state.intake_closed:
                    result = StageResult(
                        stage=stage,
                        status=StageStatus.PASSED,
                        score=1,
                        notes=[
                            "The single consolidated intake round is closed; answered facts were reused without another model call."
                        ],
                        completed_at=utc_now(),
                    )
                else:
                    result = self._run_bounded(stage)

                state = self.store.load_state()
                reconciliation = self.questions.reconcile(
                    state, stage, result.questions, result.input_fingerprint
                )
                result.question_ids = [question.id for question in reconciliation.accepted]
                if reconciliation.accepted:
                    result.status = StageStatus.NEEDS_INPUT
                if reconciliation.suppressed:
                    result.findings.extend(
                        self._suppressed_question_findings(stage, reconciliation.suppressed)
                    )
                    result.notes.append(
                        reconciliation.reason
                        or "Question proposals were converted to author-review findings."
                    )
                    if not reconciliation.accepted and result.status in {
                        StageStatus.NEEDS_INPUT,
                        StageStatus.PASSED,
                    }:
                        result.status = StageStatus.PASSED_WITH_WARNINGS

                if stage == self.config.interaction.question_stage and not result.questions:
                    state.intake_closed = True

                if result.status == StageStatus.FAILED and self._may_continue_with_warning(result):
                    result.status = StageStatus.PASSED_WITH_WARNINGS
                    result.notes.append(
                        "Quality threshold was not reached after bounded enhancement; workflow continued and retained findings for author review."
                    )

                results.append(result)
                state.stage_runs.append(result)
                state.stage_status[stage] = result.status
                self.store.write_stage_review(
                    stage,
                    1 + sum(run.stage == stage for run in state.stage_runs[:-1]),
                    result.model_dump(mode="json"),
                )
                self.store.save_state(state)
                self.store.write_response_template(state)

                if result.status == StageStatus.NEEDS_INPUT or result.status == StageStatus.FAILED:
                    break

            self._finalize_if_complete()
            return results

    def _import_response_answers(self, state) -> int:
        """Commit non-empty answers from the generated YAML before evaluating the gate."""
        open_questions = state.open_questions()
        if not open_questions or not self.store.response_template_path.exists():
            return 0

        supplied = self.store.read_response_answers()
        by_identifier = {
            identifier.casefold(): question
            for question in open_questions
            for identifier in (question.id, question.key)
        }
        imported = 0
        for identifier, answer in supplied.items():
            if not answer:
                continue
            question = by_identifier.get(identifier.casefold())
            if question is None:
                known = any(
                    identifier.casefold() in {item.id.casefold(), item.key.casefold()}
                    for item in state.pending_questions
                )
                if known:
                    continue
                raise ValueError(f"Response file contains unknown question ID or key: {identifier}")
            self.questions.answer(state, question.id, answer)
            imported += 1

        if not imported:
            return 0
        if (
            state.intake_closed
            and state.stage_status.get(self.config.interaction.question_stage)
            == StageStatus.NEEDS_INPUT
        ):
            state.stage_status[self.config.interaction.question_stage] = StageStatus.PENDING
        self.store.save_state(state)
        self.store.write_response_template(state)
        return imported

    def _run_bounded(self, stage: str) -> StageResult:
        last_result: StageResult | None = None
        cycles = 0
        while cycles < self.config.workflow.max_enhancement_cycles:
            cycles += 1
            result = self.executor.execute(stage, attempt=cycles)
            changes = self.executor.apply_safe_changes(result)
            last_result = result
            if changes.manuscript_changes:
                result.notes.append(
                    f"Safely applied manuscript changes: {changes.manuscript_changes}"
                )
            if changes.profile_changed:
                result.notes.append("Applied evidence-supported research-profile updates.")
            if changes.claims_changed:
                result.notes.append("Synchronized the structured claim-to-evidence registry.")
            if result.status != StageStatus.FAILED or result.questions:
                break
            if not changes.any:
                result.notes.append(
                    "Stopped enhancement early because the attempt made no safe change."
                )
                break
            if any(
                finding.severity == Severity.BLOCKING and not finding.resolved
                for finding in result.findings
            ):
                break
        if last_result is None:
            raise RuntimeError(f"stage '{stage}' did not execute")
        last_result.notes.append(f"Enhancement cycles used: {cycles}")
        return last_result

    def answer(self, question_id_or_key: str, answer: str) -> None:
        self.answer_many({question_id_or_key: answer})

    def answer_many(self, answers: dict[str, str]) -> None:
        if not answers:
            raise ValueError("No answers were supplied.")
        with self.store.workflow_lock():
            original = self.store.load_state()
            state = original.model_copy(deep=True)
            for identifier, answer in answers.items():
                self.questions.answer(state, identifier, answer)
            if state.intake_closed and state.stage_status.get("intake") == StageStatus.NEEDS_INPUT:
                state.stage_status["intake"] = StageStatus.PENDING
            self.store.save_state(state)
            self.store.write_response_template(state)

    def _finalize_if_complete(self) -> None:
        state = self.store.load_state()
        statuses = [
            state.stage_status.get(stage, StageStatus.PENDING)
            for stage in self.config.workflow.stages
        ]
        if not statuses or not all(status.advances_workflow for status in statuses):
            return
        latest_by_stage: dict[str, StageResult] = {}
        for run in state.stage_runs:
            latest_by_stage[run.stage] = run
        unresolved_serious = [
            finding
            for run in latest_by_stage.values()
            for finding in run.findings
            if not finding.resolved and finding.severity in {Severity.HIGH, Severity.BLOCKING}
        ]
        state.workflow_completed = True
        state.submission_ready = not unresolved_serious and all(
            status == StageStatus.PASSED for status in statuses
        )
        state.current_stage = None
        state.completed_at = utc_now()
        self.store.save_state(state)
        self.last_export_report = OutputExporter(self.store).export(state)

    def _repair_interrupted_state(self, state) -> None:
        changed = False
        for stage, status in list(state.stage_status.items()):
            if status == StageStatus.RUNNING:
                state.stage_status[stage] = StageStatus.PENDING
                changed = True
            if status == StageStatus.NEEDS_INPUT and not state.open_questions(stage):
                state.stage_status[stage] = StageStatus.PENDING
                changed = True
        if state.intake_closed and state.stage_status.get("intake") == StageStatus.NEEDS_INPUT:
            state.stage_status["intake"] = StageStatus.PENDING
            changed = True
        if changed:
            self.store.save_state(state)

    def _may_continue_with_warning(self, result: StageResult) -> bool:
        if not self.config.workflow.continue_on_quality_failure:
            return False
        return not any(
            finding.severity == Severity.BLOCKING and not finding.resolved
            for finding in result.findings
        )

    @staticmethod
    def _suppressed_question_findings(stage: str, proposals) -> list[Finding]:
        findings: list[Finding] = []
        for index, proposal in enumerate(proposals, start=1):
            key = proposal.key or f"item_{index}"
            findings.append(
                Finding(
                    id=f"INPUT-DEFERRED-{stage.upper()}-{key.upper()[:40]}",
                    stage=stage,
                    severity=Severity.HIGH,
                    problem=proposal.text,
                    required_action=(
                        "Review this item in outputs/quality-report.json. It was not reopened as "
                        "another user-input round under the minimal-interaction policy."
                    ),
                )
            )
        return findings
