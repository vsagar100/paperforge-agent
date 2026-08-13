from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from paperforge.config import AppConfig
from paperforge.domain import (
    AuthorAction,
    IssueDisposition,
    StageRecord,
    StageStatus,
    WorkflowState,
    utc_now,
)
from paperforge.exporters import ExportReport, OutputExporter
from paperforge.providers.base import ProviderError
from paperforge.stages import StageOutcome, StageRunner
from paperforge.storage import ProjectStore


@dataclass(slots=True)
class WorkflowReport:
    records: list[StageRecord] = field(default_factory=list)
    export: ExportReport = field(default_factory=ExportReport)
    resumed_stages: int = 0
    invalidated: bool = False


class WorkflowEngine:
    """Resumable, deterministic state machine for the PaperForge 1.0 pipeline."""

    def __init__(
        self,
        store: ProjectStore,
        config: AppConfig,
        runner: StageRunner,
    ) -> None:
        self.store = store
        self.config = config
        self.runner = runner
        self.last_report = WorkflowReport()

    def run(self, *, force_rebuild: bool = False) -> WorkflowReport:
        with self.store.workflow_lock():
            self.last_report = WorkflowReport()
            state = self.store.load_state()
            self._synchronize_inputs(state, force_rebuild)

            for stage in self.config.workflow.stages:
                state = self.store.load_state()
                if state.status_for(stage).complete and self.config.workflow.resume:
                    self.last_report.resumed_stages += 1
                    continue
                if stage == "export":
                    record = self._export_stage(state)
                    self.last_report.records.append(record)
                    break
                record = self._execute_stage(stage, state)
                self.last_report.records.append(record)
                if record.status in {StageStatus.BLOCKED, StageStatus.FAILED}:
                    self._write_blocked_outputs(self.store.load_state())
                    break
            return self.last_report

    def _execute_stage(self, stage: str, state: WorkflowState) -> StageRecord:
        started = utc_now()
        attempt = 1 + sum(item.stage == stage for item in state.run_history)
        state.current_stage = stage
        state.stage_status[stage] = StageStatus.RUNNING
        self.store.save_state(state)
        input_fingerprint = self._stage_fingerprint(stage, state)
        try:
            outcome = self.runner.execute(stage, state)
        except (ProviderError, OSError, ValueError, KeyError) as exc:
            failed = StageRecord(
                stage=stage,
                status=StageStatus.FAILED,
                attempt=attempt,
                input_fingerprint=input_fingerprint,
                notes=[str(exc)],
                started_at=started,
                completed_at=utc_now(),
            )
            state = self.store.load_state()
            self.store.record_stage(state, failed)
            raise
        record = self._record_from_outcome(
            stage,
            attempt,
            input_fingerprint,
            started,
            outcome,
        )
        state = self.store.load_state()
        self.store.record_stage(state, record)
        return record

    def _export_stage(self, state: WorkflowState) -> StageRecord:
        started = utc_now()
        attempt = 1 + sum(item.stage == "export" for item in state.run_history)
        input_fingerprint = self._stage_fingerprint("export", state)
        self._set_readiness(state)
        exporter = OutputExporter(self.store)
        export_report = exporter.export(state)
        self.last_report.export = export_report
        outcome = StageOutcome(
            status=StageStatus.PASSED,
            score=1.0,
            artifacts=[str(path.relative_to(self.store.root)) for path in export_report.files],
            notes=list(export_report.warnings),
        )
        record = self._record_from_outcome(
            "export",
            attempt,
            input_fingerprint,
            started,
            outcome,
        )
        state = self.store.load_state()
        self.store.record_stage(state, record)
        state.workflow_completed = True
        state.current_stage = None
        state.completed_at = utc_now()
        self.store.save_state(state)
        # Rewrite the report after export is recorded so stage history is complete.
        self.last_report.export = exporter.export(state)
        return record

    def _write_blocked_outputs(self, state: WorkflowState) -> None:
        state.workflow_completed = False
        state.submission_ready = False
        state.author_actions = self._author_actions(state)
        self.store.save_state(state)
        if self.store.read_manuscript().strip():
            self.last_report.export = OutputExporter(self.store).export(state)

    def _set_readiness(self, state: WorkflowState) -> None:
        final = state.stage_records.get("final_review")
        state.author_actions = self._author_actions(state)
        state.submission_ready = bool(
            final
            and final.status == StageStatus.PASSED
            and final.score >= self.config.quality.minimum_review_score
            and not any(
                issue.disposition == IssueDisposition.INTEGRITY_BLOCKER and not issue.resolved
                for issue in final.issues
            )
        )
        state.workflow_completed = True
        state.completed_at = utc_now()
        self.store.save_state(state)

    @staticmethod
    def _author_actions(state: WorkflowState) -> list[AuthorAction]:
        final = state.stage_records.get("final_review")
        if final is None:
            latest_issues = [
                issue
                for record in state.stage_records.values()
                for issue in record.issues
                if not issue.resolved
            ]
        else:
            latest_issues = [issue for issue in final.issues if not issue.resolved]
        actions: list[AuthorAction] = []
        seen: set[tuple[str, str | None]] = set()
        for issue in latest_issues:
            key = (issue.code, issue.section)
            if key in seen:
                continue
            seen.add(key)
            actions.append(
                AuthorAction(
                    id=f"ACT-{len(actions) + 1:03d}",
                    section=issue.section,
                    action=issue.required_change,
                    reason=issue.description,
                    blocking=issue.disposition == IssueDisposition.INTEGRITY_BLOCKER,
                )
            )
        return actions

    def _synchronize_inputs(self, state: WorkflowState, force_rebuild: bool) -> None:
        input_fingerprint = self.store.input_fingerprint(state)
        config_fingerprint = self.store.config_fingerprint()
        changed = bool(
            state.input_fingerprint
            and (
                state.input_fingerprint != input_fingerprint
                or state.config_fingerprint != config_fingerprint
            )
        )
        if force_rebuild or (
            changed and self.config.workflow.invalidate_on_input_change and state.stage_records
        ):
            reason = (
                "Generated stages invalidated by explicit rebuild."
                if force_rebuild
                else "Generated stages invalidated because project inputs or configuration changed."
            )
            self.store.reset_generated_state(state, reason)
            self.last_report.invalidated = True
            state = self.store.load_state()
        state.input_fingerprint = input_fingerprint
        state.config_fingerprint = config_fingerprint
        self.store.save_state(state)

    def _stage_fingerprint(self, stage: str, state: WorkflowState) -> str:
        digest = hashlib.sha256()
        digest.update(stage.encode("utf-8"))
        digest.update((state.input_fingerprint or "").encode("ascii"))
        digest.update((state.config_fingerprint or "").encode("ascii"))
        digest.update(self.store.read_manuscript().encode("utf-8"))
        return digest.hexdigest()

    def _record_from_outcome(
        self,
        stage: str,
        attempt: int,
        input_fingerprint: str,
        started,
        outcome: StageOutcome,
    ) -> StageRecord:
        output_material = (
            self.store.read_manuscript() + "\n".join(outcome.artifacts) + "\n".join(outcome.changes)
        )
        return StageRecord(
            stage=stage,
            status=outcome.status,
            attempt=attempt,
            score=outcome.score,
            input_fingerprint=input_fingerprint,
            output_fingerprint=hashlib.sha256(output_material.encode("utf-8")).hexdigest(),
            model=outcome.model,
            issues=outcome.issues,
            changes=outcome.changes,
            artifacts=outcome.artifacts,
            notes=outcome.notes,
            started_at=started,
            completed_at=utc_now(),
        )
