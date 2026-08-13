from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from paperforge.config import AppConfig
from paperforge.domain import (
    Claim,
    Finding,
    ManuscriptPatch,
    QuestionProposal,
    ResearchProfileUpdate,
    Severity,
    StageResult,
    StageStatus,
    utc_now,
)
from paperforge.prompts import BASE_SYSTEM, STAGE_INSTRUCTIONS
from paperforge.providers.base import ModelProvider, ModelRequest, ModelResponse
from paperforge.storage import ProjectStore
from paperforge.validators import (
    validate_claim_links,
    validate_engineering_manuscript,
    validate_manuscript_structure,
)


class LLMStagePayload(BaseModel):
    score: float = Field(ge=0, le=1)
    findings: list[Finding] = Field(default_factory=list)
    questions: list[QuestionProposal] = Field(default_factory=list)
    patches: list[ManuscriptPatch] = Field(default_factory=list)
    replacement_document: str | None = None
    profile_update: ResearchProfileUpdate | None = None
    claim_updates: list[Claim] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


@dataclass(slots=True)
class AppliedChanges:
    manuscript_changes: int = 0
    profile_changed: bool = False
    claims_changed: bool = False

    @property
    def any(self) -> bool:
        return bool(self.manuscript_changes or self.profile_changed or self.claims_changed)


def _extract_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        closing_fence = stripped.rfind("```")
        if first_newline >= 0 and closing_fence > first_newline:
            stripped = stripped[first_newline + 1 : closing_fence].strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        start = stripped.find("{")
        if start < 0:
            raise
        value, _ = decoder.raw_decode(stripped[start:])
    if not isinstance(value, dict):
        raise json.JSONDecodeError("top-level model response must be an object", stripped, 0)
    return value


class StageExecutor:
    def __init__(self, store: ProjectStore, config: AppConfig, provider: ModelProvider) -> None:
        self.store = store
        self.config = config
        self.provider = provider

    def execute(self, stage: str, attempt: int = 1) -> StageResult:
        started = utc_now()
        deterministic = self._deterministic(stage)
        if any(finding.severity == Severity.BLOCKING for finding in deterministic):
            return StageResult(
                stage=stage,
                status=StageStatus.FAILED,
                score=self._score(deterministic),
                findings=deterministic,
                attempt=attempt,
                input_fingerprint=self._input_fingerprint(stage),
                started_at=started,
                completed_at=utc_now(),
                notes=["Blocking deterministic validation failed before model invocation."],
            )

        role = self._role_for(stage)
        request = ModelRequest(
            role=role,
            system=BASE_SYSTEM,
            prompt=self._build_prompt(stage, deterministic, attempt),
            response_schema=LLMStagePayload.model_json_schema(),
            temperature=self.config.models[role].temperature,
            metadata={"stage": stage, "attempt": attempt},
        )
        payload, response, schema_errors = self._generate_validated(request)
        if payload is None or response is None:
            return StageResult(
                stage=stage,
                status=StageStatus.FAILED,
                score=0,
                findings=deterministic,
                notes=[f"Model response validation failed: {schema_errors[-1]}"],
                model_role=role,
                model_name=response.model if response else None,
                attempt=attempt,
                input_fingerprint=self._input_fingerprint(stage),
                started_at=started,
                completed_at=utc_now(),
            )

        findings = self._deduplicate_findings(deterministic + payload.findings)
        score = min(payload.score, self._score(findings))
        questions = [question for question in payload.questions if question.blocking]
        has_blocking_finding = any(
            finding.severity == Severity.BLOCKING and not finding.resolved for finding in findings
        )
        if questions and stage == self.config.interaction.question_stage:
            status = StageStatus.NEEDS_INPUT
        elif has_blocking_finding:
            status = StageStatus.FAILED
        elif score >= self.config.quality.minimum_stage_score:
            status = StageStatus.PASSED
        else:
            status = StageStatus.FAILED

        notes = list(payload.notes)
        if schema_errors:
            notes.append(f"Schema repair attempts used: {len(schema_errors)}")
        return StageResult(
            stage=stage,
            status=status,
            score=score,
            findings=findings,
            questions=questions,
            patches=payload.patches,
            replacement_document=payload.replacement_document,
            profile_update=payload.profile_update,
            claim_updates=payload.claim_updates,
            notes=notes,
            model_role=role,
            model_name=response.model,
            attempt=attempt,
            input_fingerprint=self._input_fingerprint(stage),
            started_at=started,
            completed_at=utc_now(),
        )

    def apply_safe_changes(self, result: StageResult) -> AppliedChanges:
        """Apply supported, non-scientific changes once and version the document atomically."""
        state = self.store.load_state()
        changes = AppliedChanges()
        evidence_ids = {item.id for item in self.store.load_evidence()}
        if (
            result.profile_update
            and result.profile_update.evidence_ids
            and not (set(result.profile_update.evidence_ids) - evidence_ids)
        ):
            update = result.profile_update
            if update.objectives and not state.profile.objectives:
                state.profile.objectives = update.objectives
                changes.profile_changed = True
            if update.contribution and not state.profile.contribution:
                state.profile.contribution = update.contribution
                changes.profile_changed = True
            if update.target_journal and not state.profile.target_journal:
                state.profile.target_journal = update.target_journal
                changes.profile_changed = True
            for key, value in update.constraints.items():
                if key not in state.profile.constraints:
                    state.profile.constraints[key] = value
                    changes.profile_changed = True

        text = self.store.read_manuscript()
        if (
            result.stage == "initial_draft"
            and not text.strip()
            and result.replacement_document
            and result.replacement_document.strip()
        ):
            text = result.replacement_document.strip() + "\n"
            changes.manuscript_changes += 1

        for patch in result.patches:
            if patch.scientific_change and self.config.workflow.require_human_for_scientific_change:
                continue
            if set(patch.evidence_ids) - evidence_ids:
                continue
            if patch.section == "__document__" and not text.strip() and not patch.before:
                text = patch.after
                changes.manuscript_changes += 1
            elif patch.before and text.count(patch.before) == 1:
                text = text.replace(patch.before, patch.after, 1)
                changes.manuscript_changes += 1

        if result.claim_updates and result.stage in {"initial_draft", "citation_audit"}:
            existing_claims = self.store.load_claims()
            current_claims = {claim.id: claim for claim in existing_claims}
            for claim in result.claim_updates:
                current_claims[claim.id] = claim
            updated_claims = list(current_claims.values())
            if updated_claims != existing_claims:
                self.store.save_claims(updated_claims)
                changes.claims_changed = True

        if changes.manuscript_changes:
            self.store.save_manuscript_version(text, result.stage, state)
        elif changes.profile_changed:
            self.store.save_state(state)
        return changes

    def _generate_validated(
        self, request: ModelRequest
    ) -> tuple[LLMStagePayload | None, ModelResponse | None, list[str]]:
        errors: list[str] = []
        response: ModelResponse | None = None
        current_request = request
        for repair_attempt in range(self.config.provider.max_schema_retries + 1):
            response = self.provider.generate(current_request)
            try:
                return (
                    LLMStagePayload.model_validate(_extract_json(response.content)),
                    response,
                    errors,
                )
            except (json.JSONDecodeError, ValidationError) as exc:
                errors.append(str(exc))
                if repair_attempt >= self.config.provider.max_schema_retries:
                    break
                current_request = ModelRequest(
                    role=request.role,
                    system=request.system,
                    prompt=(
                        request.prompt
                        + "\n\nThe previous response failed schema validation. Return one corrected JSON "
                        "object only. Do not add commentary. Validation error:\n" + str(exc)[:1500]
                    ),
                    response_schema=request.response_schema,
                    temperature=0,
                    metadata={**request.metadata, "schema_repair": repair_attempt + 1},
                )
        return None, response, errors

    def _deterministic(self, stage: str) -> list[Finding]:
        text = self.store.read_manuscript()
        if stage == "citation_audit":
            return validate_claim_links(
                self.store.load_claims(), self.store.load_evidence()
            ).findings
        if stage == "engineering_integrity":
            return validate_engineering_manuscript(text).findings
        if stage in {"abstract_review", "manuscript_consistency", "journal_compliance"}:
            limit = int(self.config.journal.get("abstract_max_words", 250))
            return validate_manuscript_structure(text, limit).findings
        return []

    def _build_prompt(self, stage: str, findings: list[Finding], attempt: int) -> str:
        state = self.store.load_state()
        evidence = self._evidence_context()
        complete_manuscript = self.store.read_manuscript()
        manuscript = complete_manuscript[: self.config.context.max_manuscript_chars]
        questions_allowed = (
            stage == self.config.interaction.question_stage
            and not state.intake_closed
            and not any(item.stage == stage for item in state.question_rounds)
        )
        prior_runs = [
            {
                "stage": run.stage,
                "status": run.status,
                "score": run.score,
                "finding_ids": [finding.id for finding in run.findings if not finding.resolved],
                "notes": run.notes[-3:],
            }
            for run in state.stage_runs[-8:]
        ]
        context = {
            "stage": stage,
            "attempt": attempt,
            "instruction": STAGE_INSTRUCTIONS[stage],
            "interaction_policy": {
                "mode": self.config.interaction.mode,
                "questions_allowed": questions_allowed,
                "max_questions": (
                    self.config.interaction.max_questions_per_round if questions_allowed else 0
                ),
                "one_round_only": True,
                "group_related_questions": self.config.interaction.group_related_questions,
                "safe_default": "Use not reported or REQUIRED[...] for non-critical unknowns.",
            },
            "research_profile": state.profile.model_dump(mode="json"),
            "answered_questions": [
                {
                    "key": question.key,
                    "question": question.text,
                    "answer": question.answer,
                }
                for question in state.pending_questions
                if question.answer
            ],
            "open_questions": [
                {"key": question.key, "question": question.text}
                for question in state.open_questions()
            ],
            "manuscript": manuscript,
            "manuscript_context_truncated": len(manuscript) < len(complete_manuscript),
            "evidence": evidence,
            "claims": [claim.model_dump(mode="json") for claim in self.store.load_claims()],
            "deterministic_findings": [finding.model_dump(mode="json") for finding in findings],
            "prior_stage_summaries": prior_runs,
            "journal_rules": self.config.journal,
        }
        return json.dumps(context, ensure_ascii=False)

    def _evidence_context(self) -> list[dict[str, Any]]:
        remaining = self.config.context.max_total_evidence_chars
        selected: list[dict[str, Any]] = []
        for item in self.store.load_evidence():
            if remaining <= 0:
                break
            limit = min(self.config.context.max_chars_per_evidence, remaining)
            content = item.content[:limit]
            remaining -= len(content)
            selected.append(
                {
                    "id": item.id,
                    "kind": item.kind,
                    "title": item.title,
                    "source_path": item.source_path,
                    "locator": item.locator,
                    "verified": item.verified,
                    "checksum": item.checksum,
                    "content": content,
                    "context_truncated": len(content) < len(item.content),
                }
            )
        return selected

    def _input_fingerprint(self, stage: str) -> str:
        state = self.store.load_state()
        digest = hashlib.sha256()
        digest.update(stage.encode("utf-8"))
        digest.update((state.source_fingerprint or "").encode("ascii"))
        digest.update(self.store.read_manuscript().encode("utf-8"))
        digest.update(
            json.dumps(
                [(question.key, question.answer) for question in state.pending_questions],
                sort_keys=True,
            ).encode("utf-8")
        )
        return digest.hexdigest()

    @staticmethod
    def _role_for(stage: str) -> str:
        if stage in {"methodology_review", "engineering_integrity", "final_audit"}:
            return "scientific_review" if stage != "final_audit" else "final_audit"
        if stage in {"initial_draft", "outline"}:
            return "drafting"
        return "enhancement"

    @staticmethod
    def _score(findings: list[Finding]) -> float:
        weights = {
            Severity.LOW: 0.03,
            Severity.MEDIUM: 0.08,
            Severity.HIGH: 0.2,
            Severity.BLOCKING: 0.4,
        }
        return max(
            0.0,
            1.0 - sum(weights[finding.severity] for finding in findings if not finding.resolved),
        )

    @staticmethod
    def _deduplicate_findings(findings: list[Finding]) -> list[Finding]:
        by_id: dict[str, Finding] = {}
        for finding in findings:
            by_id.setdefault(finding.id, finding)
        return list(by_id.values())
