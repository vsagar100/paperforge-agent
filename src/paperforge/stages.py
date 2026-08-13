from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from paperforge.citations import normalize_citation_markers
from paperforge.config import AppConfig
from paperforge.domain import (
    DisplayItemPlan,
    DraftBatch,
    EvidenceCoverageStatus,
    EvidenceItem,
    EvidenceKind,
    IssueDisposition,
    LiteratureNote,
    LiteratureSynthesis,
    ManuscriptOutline,
    OutlineSection,
    PaperType,
    RequirementLevel,
    ResearchPlan,
    ReviewIssue,
    ReviewReport,
    RevisionBatch,
    Severity,
    StageStatus,
    WorkflowState,
    utc_now,
)
from paperforge.evidence import (
    assess_evidence_coverage,
    author_questions_markdown,
    build_claim_ledger,
)
from paperforge.ingestion import DocumentIngestor
from paperforge.literature import LiteratureService
from paperforge.llm import LLMClient, ModelOutputError
from paperforge.prompts import (
    DRAFTER_SYSTEM,
    FINAL_AUDITOR_SYSTEM,
    PLANNER_SYSTEM,
    REVIEWER_SYSTEM,
    REVISER_SYSTEM,
    draft_prompt,
    outline_prompt,
    planning_prompt,
    review_prompt,
    revision_prompt,
    synthesis_prompt,
)
from paperforge.standards import (
    build_publication_profile,
    normalize_heading,
    section_minimum,
)
from paperforge.statistics import StatisticsDeriver
from paperforge.storage import ProjectStore
from paperforge.validators import (
    IssuePolicy,
    ValidationContext,
    has_integrity_blocker,
    markdown_sections,
    quality_score,
    validate_draft_batch,
    validate_manuscript,
    validate_revision,
)


@dataclass(slots=True)
class StageOutcome:
    status: StageStatus
    score: float = 1.0
    issues: list[ReviewIssue] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    model: str | None = None


class StageRunner:
    """Publication-contract pipeline with deterministic control and evidence gates."""

    def __init__(
        self,
        store: ProjectStore,
        config: AppConfig,
        llm: LLMClient,
        literature: LiteratureService,
    ) -> None:
        self.store = store
        self.config = config
        self.llm = llm
        self.literature = literature

    def execute(self, stage: str, state: WorkflowState) -> StageOutcome:
        handler = getattr(self, f"_stage_{stage}", None)
        if handler is None:
            raise ValueError(f"Unknown pipeline stage: {stage}")
        return handler(state)

    def _stage_prepare(self, state: WorkflowState) -> StageOutcome:
        del state
        report = DocumentIngestor(self.store, self.config.ingestion).refresh()
        evidence = StatisticsDeriver.derive(self.store.load_evidence())
        self.store.save_evidence(evidence)
        notes = [
            f"Evidence: {report.extracted} extracted, {report.unchanged} unchanged, "
            f"{report.skipped} skipped."
        ]
        notes.extend(report.warnings)
        if any(item.metadata.get("calculator") == "confusion_matrix" for item in evidence):
            notes.append("Derived confusion-matrix metrics and Wilson intervals deterministically.")
        return StageOutcome(
            status=StageStatus.PASSED_WITH_ACTIONS if report.warnings else StageStatus.PASSED,
            artifacts=["evidence/registry.json"],
            notes=notes,
        )

    def _stage_journal_profile(self, state: WorkflowState) -> StageOutcome:
        resolved_type = self._resolve_paper_type(state, self.store.load_evidence())
        state.profile.resolved_paper_type = resolved_type
        self.store.save_state(state)
        profile = build_publication_profile(state.profile, self.config, resolved_type)
        self.store.save_publication_profile(profile)
        return StageOutcome(
            status=StageStatus.PASSED,
            artifacts=["planning/publication-profile.json"],
            notes=[
                f"Resolved {resolved_type.value}; loaded {profile.source_label}.",
                profile.indexing_context,
            ],
        )

    def _stage_evidence_mapping(self, state: WorkflowState) -> StageOutcome:
        paper_type = state.profile.resolved_paper_type or self._resolve_paper_type(
            state, self.store.load_evidence()
        )
        ledger = build_claim_ledger(self.store.load_evidence())
        coverage = assess_evidence_coverage(paper_type, ledger)
        self.store.save_claim_ledger(ledger)
        self.store.save_evidence_coverage(coverage)
        self.store.write_text(
            "author-actions/evidence-required.md",
            author_questions_markdown(coverage),
        )
        blockers = [
            item
            for item in coverage.requirements
            if item.level == RequirementLevel.DRAFT_BLOCKING
            and item.status != EvidenceCoverageStatus.SUPPORTED
        ]
        issues = [self._coverage_issue(item) for item in blockers]
        return StageOutcome(
            status=StageStatus.BLOCKED if blockers else StageStatus.PASSED,
            score=(
                1.0
                if not coverage.requirements
                else sum(
                    item.status == EvidenceCoverageStatus.SUPPORTED
                    for item in coverage.requirements
                )
                / len(coverage.requirements)
            ),
            issues=issues,
            artifacts=[
                "evidence/claim-ledger.json",
                "evidence/coverage.json",
                "author-actions/evidence-required.md",
            ],
            notes=[
                f"Registered {len(ledger.claims)} exact claim atoms; "
                f"{len(blockers)} draft-blocking evidence gap(s)."
            ],
        )

    def _stage_plan(self, state: WorkflowState) -> StageOutcome:
        profile = self.store.load_publication_profile()
        ledger = self.store.load_claim_ledger()
        coverage = self.store.load_evidence_coverage()
        resolved_type = state.profile.resolved_paper_type or profile.article_type
        context = {
            "research_input": state.profile.complete_input,
            "domain": state.profile.domain,
            "requested_paper_type": state.profile.requested_paper_type,
            "policy_resolved_paper_type": resolved_type,
            "publication_profile": profile.model_dump(mode="json"),
            "evidence_coverage": coverage.model_dump(mode="json"),
            "registered_claims": self._claim_context(ledger.claims),
            "target_word_count": profile.word_target,
        }
        plan, response = self.llm.structured(
            ResearchPlan,
            role="planner",
            system=PLANNER_SYSTEM,
            prompt=planning_prompt(context),
            metadata={"operation": "plan", "stage": "plan"},
        )
        paper_type = PaperType(resolved_type)
        plan.paper_type = paper_type
        plan.required_sections = self._required_sections(
            profile.section_order,
            plan.required_sections
            + self.config.journal.required_sections
            + profile.required_declarations,
        )
        plan.search_queries = [query[:250] for query in plan.search_queries if query.strip()][:8]
        if len(plan.search_queries) < 2:
            plan.search_queries = [state.profile.topic, f"{state.profile.topic} review"]
        plan.keywords = list(
            dict.fromkeys(candidate.strip() for candidate in plan.keywords if candidate.strip())
        )[: profile.keyword_max]
        if len(plan.keywords) < profile.keyword_min:
            candidates = re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", state.profile.topic)
            plan.keywords = list(dict.fromkeys([*plan.keywords, *candidates]))[
                : profile.keyword_max
            ]
        self.store.save_plan(plan)
        return StageOutcome(
            status=StageStatus.PASSED,
            artifacts=["planning/research-plan.json"],
            notes=[
                f"Planned a {paper_type.value} against profile {profile.profile_id}; "
                f"target {profile.word_target} words."
            ],
            model=response.model,
        )

    def _stage_literature(self, state: WorkflowState) -> StageOutcome:
        del state
        plan = self.store.load_plan()
        report = self.literature.collect(plan.search_queries, self.store.load_evidence())
        self.store.save_references(report.references)
        generated_at = utc_now().isoformat()
        manifest = {
            "generated_at": generated_at,
            "provider": "OpenAlex with optional Crossref DOI verification",
            "queries": report.queries,
            "discovered": report.discovered,
            "deduplicated": report.deduplicated,
            "selected": len(report.references),
            "verified": report.verified,
            "warnings": report.warnings,
        }
        self.store.write_json("literature/search-manifest.json", manifest)
        evidence = [
            item
            for item in self.store.load_evidence()
            if item.id != "EV-COMPUTED-LITERATURE-SEARCH"
        ]
        evidence.append(
            EvidenceItem(
                id="EV-COMPUTED-LITERATURE-SEARCH",
                kind=EvidenceKind.COMPUTED,
                title="Reproducible literature-search manifest",
                content=(
                    f"Search timestamp: {generated_at}.\n"
                    "Catalogue: OpenAlex; DOI metadata verification: Crossref when enabled.\n"
                    f"Queries: {'; '.join(report.queries)}.\n"
                    f"Discovered records: {report.discovered}. Deduplicated records: "
                    f"{report.deduplicated}. Selected records: {len(report.references)}. "
                    f"Externally indexed records: {report.verified}.\n"
                    + (
                        "Warnings: " + " | ".join(report.warnings)
                        if report.warnings
                        else "Warnings: none."
                    )
                ),
                locator="literature/search-manifest.json",
                verified=True,
                metadata={
                    "generated": True,
                    "calculator": "literature_search_manifest",
                    "formula_version": 1,
                },
            )
        )
        self.store.save_evidence(evidence)
        profile = self.store.load_publication_profile()
        required = max(self.config.literature.min_sources, profile.reference_min)
        status = (
            StageStatus.PASSED if report.verified >= required else StageStatus.PASSED_WITH_ACTIONS
        )
        return StageOutcome(
            status=status,
            score=min(1.0, report.verified / max(1, required)),
            artifacts=["literature/references.json", "literature/search-manifest.json"],
            notes=[
                f"Selected {len(report.references)} sources; {report.verified} externally indexed; "
                f"profile minimum {required}."
            ]
            + report.warnings,
        )

    def _stage_source_appraisal(self, state: WorkflowState) -> StageOutcome:
        del state
        references = self.store.load_references()
        profile = self.store.load_publication_profile()
        verified = sum(item.verified for item in references)
        abstracts = sum(bool(item.abstract) for item in references)
        recent = sum(bool(item.year and utc_now().year - item.year <= 10) for item in references)
        venues = len({item.venue.casefold() for item in references if item.venue})
        payload = {
            "profile_minimum": profile.reference_min,
            "selected": len(references),
            "verified": verified,
            "with_abstract": abstracts,
            "abstract_fraction": abstracts / len(references) if references else 0.0,
            "published_within_ten_years": recent,
            "distinct_venues": venues,
            "retracted_records": [item.id for item in references if item.retracted],
            "criteria": [
                "external metadata verification",
                "abstract/source-text availability",
                "recency",
                "venue diversity",
                "retraction exclusion",
                "topical ranking recorded by the literature service",
            ],
        }
        self.store.write_json("literature/source-appraisal.json", payload)
        sufficient = (
            verified >= profile.reference_min
            and not payload["retracted_records"]
            and payload["abstract_fraction"] >= self.config.literature.require_abstract_fraction
        )
        return StageOutcome(
            status=StageStatus.PASSED if sufficient else StageStatus.PASSED_WITH_ACTIONS,
            score=min(
                1.0,
                0.5 * verified / max(1, profile.reference_min)
                + 0.5
                * min(
                    1.0,
                    payload["abstract_fraction"]
                    / max(0.01, self.config.literature.require_abstract_fraction),
                ),
            ),
            artifacts=["literature/source-appraisal.json"],
            notes=[
                f"Appraised {len(references)} records: {verified} verified, {abstracts} with abstracts, "
                f"{venues} venues."
            ],
        )

    def _stage_synthesis(self, state: WorkflowState) -> StageOutcome:
        del state
        plan = self.store.load_plan()
        references = self.store.load_references()
        context = {
            "research_plan": plan.model_dump(mode="json"),
            "publication_profile": self.store.load_publication_profile().model_dump(mode="json"),
            "verified_references": self._reference_context(references),
            "source_count": len(references),
        }
        synthesis, response = self.llm.structured(
            LiteratureSynthesis,
            role="planner",
            system=PLANNER_SYSTEM,
            prompt=synthesis_prompt(context),
            metadata={"operation": "synthesis", "stage": "synthesis"},
        )
        allowed = {reference.id for reference in references}
        notes_by_id = {
            note.reference_id: note
            for note in synthesis.source_notes
            if note.reference_id in allowed
        }
        for reference in references:
            notes_by_id.setdefault(
                reference.id,
                LiteratureNote(
                    reference_id=reference.id,
                    relevance="Selected by the reproducible literature search.",
                ),
            )
        synthesis.source_notes = list(notes_by_id.values())
        self.store.save_synthesis(synthesis)
        return StageOutcome(
            status=StageStatus.PASSED,
            artifacts=["literature/synthesis.json"],
            model=response.model,
        )

    def _stage_outline(self, state: WorkflowState) -> StageOutcome:
        del state
        plan = self.store.load_plan()
        synthesis = self.store.load_synthesis()
        evidence = self.store.load_evidence()
        references = self.store.load_references()
        ledger = self.store.load_claim_ledger()
        profile = self.store.load_publication_profile()
        context = {
            "research_plan": plan.model_dump(mode="json"),
            "publication_profile": profile.model_dump(mode="json"),
            "literature_synthesis": synthesis.model_dump(mode="json"),
            "registered_claims": self._claim_context(ledger.claims),
            "allowed_claim_ids": [item.id for item in ledger.claims],
            "allowed_evidence_ids": [item.id for item in evidence],
            "allowed_reference_ids": [item.id for item in references],
            "target_word_count": profile.word_target,
        }
        proposed, response = self.llm.structured(
            ManuscriptOutline,
            role="planner",
            system=PLANNER_SYSTEM,
            prompt=outline_prompt(context),
            metadata={"operation": "outline", "stage": "outline"},
        )
        outline = self._enforce_outline_contract(
            proposed,
            plan,
            profile,
            ledger.claims,
            evidence,
            references,
        )
        self.store.save_outline(outline)
        return StageOutcome(
            status=StageStatus.PASSED,
            artifacts=["planning/outline.json"],
            notes=[
                f"Locked {len(outline.sections)} unique sections in journal order and "
                f"{len(outline.display_items)} evidence-linked display item(s)."
            ],
            model=response.model,
        )

    def _stage_draft(self, state: WorkflowState) -> StageOutcome:
        plan = self.store.load_plan()
        outline = self.store.load_outline()
        synthesis = self.store.load_synthesis()
        evidence = self.store.load_evidence()
        references = self.store.load_references()
        ledger = self.store.load_claim_ledger()
        profile = self.store.load_publication_profile()
        validation = self._validation_context()
        chunks = _chunked(outline.sections, self.config.workflow.sections_per_draft_call)
        manuscript_parts = [f"# {outline.title.strip()}"]
        models: list[str] = []
        claims_by_id = {item.id: item for item in ledger.claims}
        references_by_id = {item.id: item for item in references}
        evidence_by_id = {item.id: item for item in evidence}
        for index, sections in enumerate(chunks, start=1):
            claim_ids = list(
                dict.fromkeys(item for section in sections for item in section.claim_ids)
            )
            evidence_ids = list(
                dict.fromkeys(item for section in sections for item in section.evidence_ids)
            )
            reference_ids = list(
                dict.fromkeys(item for section in sections for item in section.reference_ids)
            )
            context = {
                "research_plan": plan.model_dump(mode="json"),
                "publication_profile": profile.model_dump(mode="json"),
                "literature_synthesis": synthesis.model_dump(mode="json"),
                "requested_sections": [section.model_dump(mode="json") for section in sections],
                "assigned_claims": self._claim_context(
                    [claims_by_id[item] for item in claim_ids if item in claims_by_id]
                ),
                "assigned_evidence": self._evidence_context(
                    [evidence_by_id[item] for item in evidence_ids if item in evidence_by_id]
                ),
                "verified_references": self._reference_context(
                    [references_by_id[item] for item in reference_ids if item in references_by_id]
                ),
                "planned_display_items": [
                    item.model_dump(mode="json")
                    for item in outline.display_items
                    if normalize_heading(item.section)
                    in {normalize_heading(section.heading) for section in sections}
                ],
                "draft_group": index,
                "draft_groups_total": len(chunks),
            }
            batch, response = self._draft_chunk(context, sections, index, validation)
            for section in batch.sections:
                body = normalize_citation_markers(section.body).strip()
                manuscript_parts.append(f"## {section.heading}\n\n{body}")
            models.append(response.model)
        manuscript = "\n\n".join(part.strip() for part in manuscript_parts if part.strip()) + "\n"
        final_guard = validate_manuscript(manuscript, validation, review_type="writing_review")
        if has_integrity_blocker(final_guard):
            summary = "; ".join(f"{item.code}: {item.description}" for item in final_guard[:6])
            raise ModelOutputError(f"Assembled draft failed the structural contract: {summary}")
        version = self.store.save_manuscript(manuscript, "draft", state)
        return StageOutcome(
            status=StageStatus.PASSED,
            artifacts=[str(version.relative_to(self.store.root)), "manuscript/current.md"],
            notes=[
                f"Drafted {len(outline.sections)} exact sections in {len(chunks)} schema-validated calls; "
                "every chunk passed citation, number, depth, and heading gates before assembly."
            ],
            model=", ".join(dict.fromkeys(models)),
        )

    def _stage_evidence_review(self, state: WorkflowState) -> StageOutcome:
        return self._review_and_revise("evidence_review", state)

    def _stage_methodology_review(self, state: WorkflowState) -> StageOutcome:
        return self._review_and_revise("methodology_review", state)

    def _stage_results_review(self, state: WorkflowState) -> StageOutcome:
        return self._review_and_revise("results_review", state)

    def _stage_discussion_review(self, state: WorkflowState) -> StageOutcome:
        return self._review_and_revise("discussion_review", state)

    def _stage_writing_review(self, state: WorkflowState) -> StageOutcome:
        return self._review_and_revise("writing_review", state)

    def _stage_journal_review(self, state: WorkflowState) -> StageOutcome:
        return self._review_and_revise("journal_review", state)

    def _stage_final_review(self, state: WorkflowState) -> StageOutcome:
        return self._review_and_revise("final_review", state)

    def _review_and_revise(self, review_type: str, state: WorkflowState) -> StageOutcome:
        context = self._validation_context()
        manuscript = self.store.read_manuscript()
        changes: list[str] = []
        artifacts: list[str] = []
        last_model: str | None = None
        final_issues: list[ReviewIssue] = []
        final_score = 0.0

        for cycle in range(1, self.config.workflow.max_review_cycles + 2):
            deterministic = validate_manuscript(manuscript, context, review_type=review_type)
            if review_type == "final_review":
                deterministic.extend(self._prior_unresolved_items(state))
            model_context = self._review_context(review_type, manuscript, deterministic)
            role = "final_auditor" if review_type == "final_review" else "reviewer"
            system = FINAL_AUDITOR_SYSTEM if review_type == "final_review" else REVIEWER_SYSTEM
            report, response = self.llm.structured(
                ReviewReport,
                role=role,
                system=system,
                prompt=review_prompt(review_type, model_context),
                metadata={
                    "operation": "review",
                    "stage": review_type,
                    "review_cycle": cycle,
                },
            )
            last_model = response.model
            policy = IssuePolicy(context)
            issues = policy.normalize_all(deterministic + report.issues)
            self._resolve_false_structure_items(issues, manuscript, context)
            self._resolve_disclosed_recommendations(issues, manuscript)
            final_issues = [issue for issue in issues if not issue.resolved]
            final_score = quality_score(final_issues, report.score)
            fixable = [
                issue
                for issue in final_issues
                if not issue.requires_new_evidence
                and issue.disposition
                in {IssueDisposition.AUTO_FIX, IssueDisposition.INTEGRITY_BLOCKER}
            ]
            if not fixable or cycle > self.config.workflow.max_review_cycles:
                break
            headings = self._revision_headings(review_type, manuscript, fixable)
            if not headings:
                break
            candidate, guard_issues, revision_model, rejected_attempts = self._revise_sections(
                review_type,
                manuscript,
                headings,
                fixable,
                context,
                cycle,
            )
            last_model = revision_model or last_model
            if rejected_attempts:
                changes.append(
                    f"Rejected {rejected_attempts} unsafe targeted revision attempt(s) in cycle "
                    f"{cycle}; the prior manuscript was preserved until validation passed."
                )
            if has_integrity_blocker(guard_issues):
                final_issues = policy.normalize_all(final_issues + guard_issues)
                final_score = quality_score(final_issues, report.score)
                changes.append(
                    f"Rejected section revision in cycle {cycle}; the prior manuscript was preserved."
                )
                break
            manuscript = candidate
            version = self.store.save_manuscript(manuscript, review_type, state)
            artifacts.append(str(version.relative_to(self.store.root)))
            changes.append(
                f"Replaced and validated {len(headings)} targeted section(s) in cycle {cycle}; "
                "unaffected sections were preserved byte-for-byte."
            )

        status = StageStatus.BLOCKED if has_integrity_blocker(final_issues) else StageStatus.PASSED
        actionable = [
            issue for issue in final_issues if issue.disposition != IssueDisposition.RECOMMENDATION
        ]
        if status == StageStatus.PASSED and (
            actionable or final_score < self.config.quality.minimum_review_score
        ):
            status = StageStatus.PASSED_WITH_ACTIONS
        return StageOutcome(
            status=status,
            score=final_score,
            issues=final_issues,
            changes=changes,
            artifacts=artifacts,
            notes=[f"Completed {review_type} with {len(final_issues)} unresolved item(s)."],
            model=last_model,
        )

    def _stage_export(self, state: WorkflowState) -> StageOutcome:
        del state
        return StageOutcome(status=StageStatus.PASSED)

    def _draft_chunk(
        self,
        prompt_context: dict[str, Any],
        sections: list[OutlineSection],
        group_number: int,
        validation: ValidationContext,
    ):
        feedback = ""
        last_response = None
        for attempt in range(1, self.config.provider.max_schema_retries + 2):
            prompt = draft_prompt(prompt_context)
            if feedback:
                prompt += "\n\nPrevious deterministic defects to correct:\n" + feedback
            batch, response = self.llm.structured(
                DraftBatch,
                role="drafter",
                system=DRAFTER_SYSTEM,
                prompt=prompt,
                metadata={
                    "operation": "draft",
                    "stage": "draft",
                    "draft_group": group_number,
                    "draft_attempt": attempt,
                },
            )
            last_response = response
            for section in batch.sections:
                section.body = normalize_citation_markers(section.body).strip()
            issues = validate_draft_batch(batch, sections, validation)
            if not has_integrity_blocker(issues):
                return batch, response
            feedback = "\n".join(
                f"- {issue.code} ({issue.section or 'group'}): {issue.description}"
                for issue in issues[:12]
            )
        model = last_response.model if last_response else "drafter"
        raise ModelOutputError(
            f"Draft group {group_number} from '{model}' remained unsafe after bounded retries: "
            f"{feedback[:3000]}"
        )

    def _revise_sections(
        self,
        review_type: str,
        manuscript: str,
        headings: list[str],
        issues: list[ReviewIssue],
        validation: ValidationContext,
        cycle: int,
    ) -> tuple[str, list[ReviewIssue], str | None, int]:
        outline_by_heading = {
            normalize_heading(section.heading): section
            for section in self.store.load_outline().sections
        }
        current = _level_two_sections(manuscript)
        requested = [outline_by_heading[normalize_heading(item)] for item in headings]
        last_model: str | None = None
        guard_issues: list[ReviewIssue] = []
        rejected_attempts = 0
        for attempt in range(1, self.config.provider.max_schema_retries + 2):
            revision_context = self._revision_context(
                review_type,
                headings,
                current,
                issues,
            )
            if guard_issues:
                revision_context["rejected_revision_guard_issues"] = [
                    issue.model_dump(mode="json") for issue in guard_issues
                ]
            batch, response = self.llm.structured(
                RevisionBatch,
                role="reviser",
                system=REVISER_SYSTEM,
                prompt=revision_prompt(revision_context),
                metadata={
                    "operation": "revise",
                    "stage": review_type,
                    "review_cycle": cycle,
                    "revision_attempt": attempt,
                },
            )
            last_model = response.model
            draft_batch = DraftBatch(sections=batch.sections)
            for section in draft_batch.sections:
                section.body = normalize_citation_markers(section.body).strip()
            guard_issues = validate_draft_batch(draft_batch, requested, validation)
            if has_integrity_blocker(guard_issues):
                rejected_attempts += 1
                continue
            replacements = {section.heading: section.body for section in draft_batch.sections}
            candidate = _replace_level_two_sections(manuscript, replacements)
            guard_issues = validate_revision(manuscript, candidate, validation)
            if not has_integrity_blocker(guard_issues):
                return candidate, guard_issues, last_model, rejected_attempts
            rejected_attempts += 1
        return manuscript, guard_issues, last_model, rejected_attempts

    def _resolve_paper_type(self, state: WorkflowState, evidence) -> PaperType:
        requested = state.profile.requested_paper_type
        if requested == PaperType.AUTO:
            requested = self.config.paper.requested_type
        has_original = _has_original_study_evidence(evidence, state.profile)
        if requested == PaperType.ORIGINAL_RESEARCH and not has_original:
            if not self.config.paper.fallback_to_review_without_results:
                raise ValueError(
                    "Original-research mode requires authentic methods and results in the synopsis "
                    "or project files."
                )
            return PaperType.REVIEW_ARTICLE
        if requested != PaperType.AUTO:
            return requested
        return PaperType.ORIGINAL_RESEARCH if has_original else self.config.paper.topic_only_default

    @staticmethod
    def _required_sections(profile_order: list[str], proposed: list[str]) -> list[str]:
        required: list[str] = []
        seen: set[str] = set()
        for item in [*profile_order, *proposed]:
            normalized = normalize_heading(item)
            if not normalized or normalized == "references" or normalized in seen:
                continue
            required.append(item.strip())
            seen.add(normalized)
        return required

    def _validation_context(self) -> ValidationContext:
        return ValidationContext(
            config=self.config,
            plan=self.store.load_plan(),
            evidence=self.store.load_evidence(),
            references=self.store.load_references(),
            publication_profile=self.store.load_publication_profile(),
            claim_ledger=self.store.load_claim_ledger(),
            evidence_coverage=self.store.load_evidence_coverage(),
        )

    def _review_context(
        self,
        review_type: str,
        manuscript: str,
        deterministic: list[ReviewIssue],
    ) -> dict[str, Any]:
        context = {
            "review_type": review_type,
            "research_plan": self.store.load_plan().model_dump(mode="json"),
            "publication_profile": self.store.load_publication_profile().model_dump(mode="json"),
            "evidence_coverage": self.store.load_evidence_coverage().model_dump(mode="json"),
            "manuscript": manuscript[: self.config.context.max_manuscript_chars],
            "verified_reference_ids": [
                item.id for item in self.store.load_references() if item.verified
            ],
            "deterministic_issues": [issue.model_dump(mode="json") for issue in deterministic],
        }
        if review_type == "final_review":
            context["prior_unresolved_issues"] = [
                issue.model_dump(mode="json")
                for issue in self._prior_unresolved_items(self.store.load_state())
            ]
        return context

    def _revision_context(
        self,
        review_type: str,
        headings: list[str],
        current_sections: dict[str, str],
        issues: list[ReviewIssue],
    ) -> dict[str, Any]:
        outline_by_heading = {
            normalize_heading(section.heading): section
            for section in self.store.load_outline().sections
        }
        requested = [outline_by_heading[normalize_heading(item)] for item in headings]
        claim_ids = list(dict.fromkeys(item for section in requested for item in section.claim_ids))
        evidence_ids = list(
            dict.fromkeys(item for section in requested for item in section.evidence_ids)
        )
        reference_ids = list(
            dict.fromkeys(item for section in requested for item in section.reference_ids)
        )
        claims = {item.id: item for item in self.store.load_claim_ledger().claims}
        evidence = {item.id: item for item in self.store.load_evidence()}
        references = {item.id: item for item in self.store.load_references()}
        return {
            "review_type": review_type,
            "research_plan": self.store.load_plan().model_dump(mode="json"),
            "publication_profile": self.store.load_publication_profile().model_dump(mode="json"),
            "requested_sections": [
                {
                    **section.model_dump(mode="json"),
                    "current_body": current_sections[normalize_heading(section.heading)],
                }
                for section in requested
            ],
            "issues": [issue.model_dump(mode="json") for issue in issues],
            "assigned_claims": self._claim_context(
                [claims[item] for item in claim_ids if item in claims]
            ),
            "assigned_evidence": self._evidence_context(
                [evidence[item] for item in evidence_ids if item in evidence]
            ),
            "verified_references": self._reference_context(
                [references[item] for item in reference_ids if item in references]
            ),
        }

    def _enforce_outline_contract(
        self,
        proposed: ManuscriptOutline,
        plan: ResearchPlan,
        profile,
        claims,
        evidence,
        references,
    ) -> ManuscriptOutline:
        proposed_by_heading = {
            normalize_heading(section.heading): section for section in proposed.sections
        }
        evidence_ids = {item.id for item in evidence}
        reference_ids = {item.id for item in references if item.verified}
        claim_ids = {item.id for item in claims}
        sections: list[OutlineSection] = []
        for heading in plan.required_sections:
            normalized = normalize_heading(heading)
            section = proposed_by_heading.get(normalized) or OutlineSection(
                heading=heading,
                purpose=f"Develop the required {heading} section from assigned evidence.",
            )
            section.heading = heading
            section.content_requirements = list(
                dict.fromkeys(
                    section.content_requirements
                    or self._content_requirements(normalized, plan.paper_type)
                )
            )
            assigned_claims = self._claims_for_section(normalized, claims)
            section.claim_ids = [item for item in section.claim_ids if item in claim_ids]
            if not section.claim_ids:
                section.claim_ids = [item.id for item in assigned_claims]
            section.evidence_ids = [item for item in section.evidence_ids if item in evidence_ids]
            if not section.evidence_ids:
                section.evidence_ids = list(
                    dict.fromkeys(item.evidence_id for item in assigned_claims)
                )
            section.reference_ids = [
                item for item in section.reference_ids if item in reference_ids
            ]
            if self._section_uses_literature(normalized) and not section.reference_ids:
                section.reference_ids = [item.id for item in references if item.verified]
            section.target_words = self._target_words(profile, heading)
            sections.append(section)
        displays = [
            item
            for item in proposed.display_items
            if normalize_heading(item.section)
            in {normalize_heading(section.heading) for section in sections}
            and set(item.claim_ids) <= claim_ids
            and set(item.evidence_ids) <= evidence_ids
        ]
        if plan.paper_type == PaperType.ORIGINAL_RESEARCH and not any(
            item.kind == "table" for item in displays
        ):
            numeric_claims = [item for item in claims if item.numeric_atoms]
            if numeric_claims:
                displays.append(
                    DisplayItemPlan(
                        id="TAB-01",
                        kind="table",
                        title="Supported experimental dataset and performance results",
                        purpose="Present dense reported values with definitions and validity boundaries.",
                        section="Results",
                        claim_ids=[item.id for item in numeric_claims],
                        evidence_ids=list(
                            dict.fromkeys(item.evidence_id for item in numeric_claims)
                        ),
                        status="available",
                    )
                )
        if plan.paper_type == PaperType.ORIGINAL_RESEARCH and not any(
            item.kind == "figure" for item in displays
        ):
            supplied_figures = [item for item in evidence if item.kind == EvidenceKind.FIGURE]
            architecture_claims = [
                item
                for item in claims
                if re.search(
                    r"\b(controller|processor|sensor|camera|module|cloud|communication)\b",
                    item.text,
                    re.IGNORECASE,
                )
            ]
            displays.append(
                DisplayItemPlan(
                    id="FIG-01",
                    kind="figure",
                    title="Implemented system architecture and information flow",
                    purpose=(
                        "Show only the supplied hardware, onboard processing stages, event payload, "
                        "communication link, and cloud boundary."
                    ),
                    section="Materials and Methods",
                    claim_ids=[item.id for item in architecture_claims],
                    evidence_ids=list(
                        dict.fromkeys(
                            [
                                *(item.evidence_id for item in architecture_claims),
                                *(item.id for item in supplied_figures),
                            ]
                        )
                    ),
                    status="available" if supplied_figures else "author_required",
                )
            )
        return ManuscriptOutline(
            title=proposed.title.strip() or plan.working_title,
            sections=sections,
            display_items=displays,
        )

    @staticmethod
    def _claims_for_section(heading: str, claims):
        if heading in {"related work", "review methodology"}:
            return []
        if heading in {"results", "conclusion"}:
            selected = [
                item
                for item in claims
                if item.numeric_atoms
                or re.search(
                    r"\b(result|achieved|accuracy|precision|recall|specificity|mcc)\b",
                    item.text,
                    re.I,
                )
            ]
            return selected or claims
        if heading == "limitations":
            selected = [
                item
                for item in claims
                if re.search(
                    r"\b(limit|not |rather than|controlled|constraint|consideration)\b",
                    item.text,
                    re.I,
                )
            ]
            return selected or claims
        if heading in DECLARATION_HEADINGS:
            terms = DECLARATION_HEADINGS[heading]
            return [item for item in claims if any(term in item.text.casefold() for term in terms)]
        return claims

    @staticmethod
    def _section_uses_literature(heading: str) -> bool:
        return heading in {
            "introduction",
            "related work",
            "review methodology",
            "thematic synthesis",
            "discussion",
            "research gaps and future directions",
        }

    @staticmethod
    def _content_requirements(heading: str, paper_type: PaperType) -> list[str]:
        common = {
            "abstract": [
                "problem and objective",
                "actual method/design",
                "principal supported results",
                "bounded conclusion",
            ],
            "introduction": [
                "specific problem context",
                "critical literature-backed gap",
                "research question and objectives",
                "explicit contribution without superiority claims",
            ],
            "related work": [
                "theme-based synthesis",
                "method/dataset/sensor contrasts",
                "limitations and comparability boundaries",
                "gap derived from multiple verified sources",
            ],
            "methodology": [
                "study design and setting",
                "equipment and configuration",
                "algorithm with all supported parameters",
                "data acquisition and ground truth",
                "evaluation and statistical procedure",
                "validity and reproducibility boundary",
            ],
            "materials and methods": [
                "study design and setting",
                "equipment and configuration",
                "algorithm with all supported parameters",
                "data acquisition and ground truth",
                "evaluation and statistical procedure",
                "validity and reproducibility boundary",
            ],
            "results": [
                "dataset/accounting",
                "primary outcomes with denominators",
                "supported table(s)",
                "failure/error behavior",
                "no interpretation beyond evidence",
            ],
            "discussion": [
                "answer to research question",
                "comparison accounting for protocol differences",
                "mechanisms and alternative explanations",
                "generalisability and practical implications",
                "limitations without repeating results",
            ],
            "limitations": ["all known validity threats", "missing evidence", "scope boundaries"],
            "conclusion": ["supported contribution", "principal results", "bounded implications"],
        }
        if paper_type == PaperType.REVIEW_ARTICLE:
            common["review methodology"] = [
                "databases/catalogues and exact queries",
                "search date",
                "eligibility and exclusion criteria",
                "deduplication and appraisal",
                "scope limitation and non-systematic status when applicable",
            ]
            common["thematic synthesis"] = [
                "evidence organized by theme",
                "methodological contrasts",
                "agreement and disagreement",
                "evidence-strength limitations",
            ]
        return common.get(heading, ["journal-required, evidence-supported content"])

    @staticmethod
    def _target_words(profile, heading: str) -> int:
        normalized = normalize_heading(heading)
        if normalized == "keywords":
            return profile.keyword_max
        if normalized in DECLARATION_HEADINGS:
            return 35
        minimum = section_minimum(profile, heading)
        if normalized == "abstract":
            return min(profile.abstract_max_words, max(profile.abstract_min_words, 225))
        return min(3000, max(minimum, round(minimum * 1.12)))

    def _revision_headings(
        self,
        review_type: str,
        manuscript: str,
        issues: list[ReviewIssue],
    ) -> list[str]:
        sections = _level_two_sections(manuscript)
        headings: list[str] = []
        for issue in issues:
            if not issue.section:
                continue
            normalized = normalize_heading(issue.section)
            matched = next(
                (
                    heading
                    for heading in sections
                    if normalized == heading or normalized in heading or heading in normalized
                ),
                None,
            )
            if matched:
                headings.append(matched)
        if not headings:
            defaults = {
                "evidence_review": ["related work", "introduction"],
                "methodology_review": ["methodology", "materials and methods"],
                "results_review": ["results"],
                "discussion_review": ["discussion", "limitations"],
                "writing_review": ["introduction", "discussion", "conclusion"],
                "journal_review": ["abstract", "keywords"],
                "final_review": ["discussion", "conclusion"],
            }
            headings = [item for item in defaults.get(review_type, []) if item in sections]
        ordered = [heading for heading in sections if heading in set(headings)]
        return ordered[: self.config.workflow.sections_per_draft_call]

    def _evidence_context(self, evidence) -> list[dict[str, Any]]:
        remaining = self.config.context.max_evidence_chars
        selected: list[dict[str, Any]] = []
        for item in evidence:
            if remaining <= 0:
                break
            content = item.content[:remaining]
            remaining -= len(content)
            selected.append(
                {
                    "id": item.id,
                    "kind": item.kind,
                    "title": item.title,
                    "locator": item.locator,
                    "content": content,
                }
            )
        return selected

    def _claim_context(self, claims) -> list[dict[str, Any]]:
        remaining = self.config.context.max_evidence_chars
        selected: list[dict[str, Any]] = []
        for item in claims:
            if remaining <= 0:
                break
            text = item.text[:remaining]
            remaining -= len(text)
            selected.append(
                {
                    "id": item.id,
                    "evidence_id": item.evidence_id,
                    "kind": item.kind,
                    "exact_text": text,
                    "numeric_atoms": item.numeric_atoms,
                }
            )
        return selected

    def _reference_context(self, references) -> list[dict[str, Any]]:
        remaining = self.config.context.max_reference_abstract_chars
        selected: list[dict[str, Any]] = []
        for item in references:
            abstract = (item.abstract or "")[: min(2500, remaining)]
            remaining -= len(abstract)
            selected.append(
                {
                    "id": item.id,
                    "title": item.title,
                    "authors": item.authors,
                    "year": item.year,
                    "venue": item.venue,
                    "doi": item.doi,
                    "abstract": abstract or "Abstract unavailable.",
                    "verified": item.verified,
                }
            )
            if remaining <= 0:
                break
        return selected

    @staticmethod
    def _coverage_issue(requirement) -> ReviewIssue:
        digest = hashlib.sha1(requirement.code.encode()).hexdigest()[:10].upper()
        return ReviewIssue(
            id=f"EVID-{digest}",
            code=f"insufficient_study_evidence_{requirement.code}",
            severity=Severity.BLOCKING,
            section="Evidence",
            description=(
                f"{requirement.label} is {requirement.status.value.replace('_', ' ')}: "
                f"{requirement.explanation}"
            ),
            required_change=requirement.requested_detail or "Supply the missing study evidence.",
            evidence_ids=[],
            requires_new_evidence=True,
            disposition=IssueDisposition.INTEGRITY_BLOCKER,
        )

    @staticmethod
    def _resolve_false_structure_items(
        issues: list[ReviewIssue],
        manuscript: str,
        context: ValidationContext,
    ) -> None:
        sections = {
            normalize_heading(key): value for key, value in markdown_sections(manuscript).items()
        }
        for issue in issues:
            combined = f"{issue.code} {issue.description}".casefold()
            if ("related work" in combined or "relatedwork" in combined) and (
                "empty" in combined or "missing" in combined
            ):
                body = sections.get("related work") or sections.get("literature review") or ""
                minimum = (
                    section_minimum(context.publication_profile, "Related Work")
                    if context.publication_profile
                    else 30
                )
                if len(body.split()) >= minimum:
                    issue.resolved = True
                    issue.resolution = (
                        "The current manuscript contains a substantive Related Work section."
                    )
            if (
                issue.code
                in {"missing_required_section", "empty_required_section", "section_too_short"}
                and issue.section
            ):
                body = sections.get(normalize_heading(issue.section), "")
                minimum = (
                    section_minimum(context.publication_profile, issue.section)
                    if context.publication_profile
                    else 30
                )
                if len(body.split()) >= minimum:
                    issue.resolved = True
                    issue.resolution = (
                        "The current manuscript satisfies the section-depth contract."
                    )

    @staticmethod
    def _resolve_disclosed_recommendations(issues: list[ReviewIssue], manuscript: str) -> None:
        paragraphs = [
            paragraph.casefold()
            for paragraph in re.split(r"\n\s*\n", manuscript)
            if paragraph.strip()
        ]
        disclosure_terms = (
            "not available",
            "not reported",
            "was not performed",
            "were not performed",
            "outside the scope",
        )
        for issue in issues:
            if issue.disposition != IssueDisposition.RECOMMENDATION:
                continue
            topic = issue.code.split("_", 1)[0]
            if any(
                topic in paragraph and any(term in paragraph for term in disclosure_terms)
                for paragraph in paragraphs
            ):
                issue.resolved = True
                issue.resolution = (
                    "The manuscript transparently discloses the optional scope boundary."
                )

    @staticmethod
    def _prior_unresolved_items(state: WorkflowState) -> list[ReviewIssue]:
        carried: list[ReviewIssue] = []
        for stage, record in state.stage_records.items():
            if stage == "final_review":
                continue
            for issue in record.issues:
                if (
                    issue.resolved
                    or not issue.requires_new_evidence
                    or issue.disposition
                    not in {
                        IssueDisposition.AUTHOR_ACTION,
                        IssueDisposition.INTEGRITY_BLOCKER,
                    }
                ):
                    continue
                carried.append(issue.model_copy(deep=True))
        return carried


DECLARATION_HEADINGS = {
    "data availability": ("data availability", "repository", "available upon request"),
    "conflict of interest": ("conflict of interest", "competing interest"),
    "funding": ("funding", "grant", "sponsor"),
    "author contributions": ("author contribution", "credit"),
    "declaration of ai use": ("ai use", "artificial intelligence"),
}


def _chunked(items: list[Any], size: int) -> list[list[Any]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _level_two_sections(manuscript: str) -> dict[str, str]:
    matches = list(re.finditer(r"(?m)^##\s+(.+?)\s*$", manuscript))
    result: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(manuscript)
        result[normalize_heading(match.group(1))] = manuscript[match.end() : end].strip()
    return result


def _replace_level_two_sections(manuscript: str, replacements: dict[str, str]) -> str:
    normalized_replacements = {
        normalize_heading(heading): body.strip() for heading, body in replacements.items()
    }
    matches = list(re.finditer(r"(?m)^##\s+(.+?)\s*$", manuscript))
    if not matches:
        raise ModelOutputError("Cannot revise a manuscript without level-2 sections.")
    parts = [manuscript[: matches[0].start()].rstrip()]
    replaced: set[str] = set()
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(manuscript)
        heading = match.group(1).strip()
        normalized = normalize_heading(heading)
        original_body = manuscript[match.end() : end].strip()
        body = normalized_replacements.get(normalized, original_body)
        if normalized in normalized_replacements:
            replaced.add(normalized)
        parts.append(f"## {heading}\n\n{body}")
    missing = set(normalized_replacements) - replaced
    if missing:
        raise ModelOutputError(
            f"Revision targeted unknown section(s): {', '.join(sorted(missing))}"
        )
    return "\n\n".join(part for part in parts if part.strip()).rstrip() + "\n"


def _has_original_study_evidence(evidence, profile) -> bool:
    content_parts: list[str] = []
    if profile.synopsis and profile.synopsis.strip():
        content_parts.append(profile.synopsis.strip())
    for item in evidence:
        if item.metadata.get("generated"):
            continue
        content = item.content
        if item.source_path == "inputs/research_brief.md":
            content = content.replace(profile.topic, "")
            if profile.synopsis:
                content = content.replace(profile.synopsis.strip(), "")
            content = re.sub(r"(?im)^#{1,3}\s+(?:research input|topic|synopsis)\s*$", "", content)
        if content.strip():
            content_parts.append(content)
    content = "\n".join(content_parts).casefold()
    method_terms = (
        "experiment",
        "dataset",
        "prototype",
        "method",
        "algorithm",
        "validation",
        "ground truth",
        "labelled",
        "labeled",
    )
    result_terms = (
        "accuracy",
        "precision",
        "recall",
        "specificity",
        "result",
        "achieved",
        "measured",
        "sample size",
    )
    assertion_terms = (
        "we used",
        "we collected",
        "our system",
        "was used",
        "were used",
        "was evaluated",
        "was validated",
        "were collected",
        "were processed",
        "achieved",
        "measured",
        "dataset of",
        "dataset contains",
        "experiments",
        "ground-truth",
        "ground truth",
    )
    return (
        bool(re.search(r"\d", content))
        and any(term in content for term in method_terms)
        and any(term in content for term in result_terms)
        and any(term in content for term in assertion_terms)
    )
