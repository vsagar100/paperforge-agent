from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from paperforge.citations import strip_reference_section, unknown_reference_ids
from paperforge.config import AppConfig
from paperforge.domain import (
    EvidenceItem,
    EvidenceKind,
    IssueDisposition,
    LiteratureNote,
    LiteratureSynthesis,
    ManuscriptOutline,
    OutlineSection,
    PaperType,
    ResearchPlan,
    ReviewIssue,
    ReviewReport,
    StageStatus,
    WorkflowState,
    utc_now,
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
from paperforge.statistics import StatisticsDeriver
from paperforge.storage import ProjectStore
from paperforge.validators import (
    IssuePolicy,
    ValidationContext,
    has_integrity_blocker,
    markdown_sections,
    minimum_section_words,
    quality_score,
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
    """Implements each substantive pipeline stage without model-generated control flow."""

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

    def _stage_plan(self, state: WorkflowState) -> StageOutcome:
        evidence = self.store.load_evidence()
        resolved_type = self._resolve_paper_type(state, evidence)
        planning_evidence = [
            item for item in evidence if item.id != "EV-COMPUTED-LITERATURE-SEARCH"
        ]
        context = {
            "research_input": state.profile.complete_input,
            "domain": state.profile.domain,
            "requested_paper_type": state.profile.requested_paper_type,
            "policy_resolved_paper_type": resolved_type,
            "target_journal": state.profile.target_journal or self.config.journal.name,
            "user_evidence": self._evidence_context(planning_evidence),
            "target_word_count": self.config.paper.target_word_count,
        }
        plan, response = self.llm.structured(
            ResearchPlan,
            role="planner",
            system=PLANNER_SYSTEM,
            prompt=planning_prompt(context),
            metadata={"operation": "plan", "stage": "plan"},
        )
        plan.paper_type = resolved_type
        plan.required_sections = self._required_sections(
            resolved_type,
            plan.required_sections
            + self.config.journal.required_sections
            + self.config.journal.required_declarations,
        )
        plan.search_queries = [query[:250] for query in plan.search_queries if query.strip()][:8]
        if len(plan.search_queries) < 2:
            plan.search_queries = [
                state.profile.topic,
                f"{state.profile.topic} review",
            ]
        keyword_candidates = [
            *plan.keywords,
            state.profile.domain,
            *re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", state.profile.topic),
            "research",
        ]
        plan.keywords = list(
            dict.fromkeys(
                candidate.strip() for candidate in keyword_candidates if candidate.strip()
            )
        )[:10]
        state.profile.resolved_paper_type = resolved_type
        self.store.save_state(state)
        self.store.save_plan(plan)
        return StageOutcome(
            status=StageStatus.PASSED,
            artifacts=["planning/research-plan.json"],
            notes=[f"Selected {resolved_type.value} using the evidence-availability policy."],
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
        self.store.write_json(
            "literature/search-manifest.json",
            manifest,
        )
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
        status = (
            StageStatus.PASSED
            if report.verified >= self.config.literature.min_sources
            else StageStatus.PASSED_WITH_ACTIONS
        )
        return StageOutcome(
            status=status,
            score=min(1.0, report.verified / self.config.literature.min_sources),
            artifacts=["literature/references.json", "literature/search-manifest.json"],
            notes=[
                f"Selected {len(report.references)} sources; {report.verified} externally indexed."
            ]
            + report.warnings,
        )

    def _stage_synthesis(self, state: WorkflowState) -> StageOutcome:
        del state
        plan = self.store.load_plan()
        references = self.store.load_references()
        context = {
            "research_plan": plan.model_dump(mode="json"),
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
        context = {
            "research_plan": plan.model_dump(mode="json"),
            "literature_synthesis": synthesis.model_dump(mode="json"),
            "allowed_evidence_ids": [item.id for item in evidence],
            "allowed_reference_ids": [item.id for item in references],
            "target_word_count": self.config.paper.target_word_count,
        }
        outline, response = self.llm.structured(
            ManuscriptOutline,
            role="planner",
            system=PLANNER_SYSTEM,
            prompt=outline_prompt(context),
            metadata={"operation": "outline", "stage": "outline"},
        )
        outline.title = outline.title.strip() or plan.working_title
        unique_sections: dict[str, OutlineSection] = {}
        for section in outline.sections:
            unique_sections.setdefault(_normalize_heading(section.heading), section)
        outline.sections = list(unique_sections.values())
        evidence_ids = {item.id for item in evidence}
        reference_ids = {item.id for item in references}
        for section in outline.sections:
            section.evidence_ids = [item for item in section.evidence_ids if item in evidence_ids]
            section.reference_ids = [
                item for item in section.reference_ids if item in reference_ids
            ]
        existing = {_normalize_heading(section.heading) for section in outline.sections}
        for required in plan.required_sections:
            if _normalize_heading(required) not in existing:
                outline.sections.append(
                    OutlineSection(
                        heading=required,
                        purpose=f"Address the required {required} content using available evidence.",
                        evidence_ids=list(evidence_ids),
                        reference_ids=list(reference_ids),
                        target_words=300,
                    )
                )
                existing.add(_normalize_heading(required))
        self.store.save_outline(outline)
        return StageOutcome(
            status=StageStatus.PASSED,
            artifacts=["planning/outline.json"],
            model=response.model,
        )

    def _stage_draft(self, state: WorkflowState) -> StageOutcome:
        plan = self.store.load_plan()
        outline = self.store.load_outline()
        synthesis = self.store.load_synthesis()
        evidence = self.store.load_evidence()
        references = self.store.load_references()
        chunks = _chunked(outline.sections, self.config.workflow.sections_per_draft_call)
        manuscript_parts = [f"# {outline.title.strip()}\n"]
        models: list[str] = []
        for index, sections in enumerate(chunks, start=1):
            context = {
                "research_plan": plan.model_dump(mode="json"),
                "literature_synthesis": synthesis.model_dump(mode="json"),
                "requested_sections": [section.model_dump(mode="json") for section in sections],
                "user_evidence": self._evidence_context(evidence),
                "verified_references": self._reference_context(references),
                "allowed_reference_ids": [reference.id for reference in references],
                "draft_group": index,
                "draft_groups_total": len(chunks),
            }
            chunk, response = self._draft_chunk(context, sections, index)
            chunk = strip_reference_section(chunk)
            chunk = re.sub(r"(?m)^#\s+.+?\s*$", "", chunk, count=1).strip()
            manuscript_parts.append(chunk)
            models.append(response.model)
        manuscript = "\n\n".join(part.strip() for part in manuscript_parts if part.strip()) + "\n"
        unknown = unknown_reference_ids(manuscript, references)
        if unknown:
            raise ModelOutputError(
                "Initial draft introduced citation IDs outside the verified catalogue: "
                + ", ".join(unknown)
            )
        version = self.store.save_manuscript(manuscript, "draft", state)
        return StageOutcome(
            status=StageStatus.PASSED,
            artifacts=[
                str(version.relative_to(self.store.root)),
                "manuscript/current.md",
            ],
            notes=[f"Drafted {len(outline.sections)} sections in {len(chunks)} bounded calls."],
            model=", ".join(dict.fromkeys(models)),
        )

    def _stage_evidence_review(self, state: WorkflowState) -> StageOutcome:
        return self._review_and_revise("evidence_review", state)

    def _stage_methodology_review(self, state: WorkflowState) -> StageOutcome:
        return self._review_and_revise("methodology_review", state)

    def _stage_results_review(self, state: WorkflowState) -> StageOutcome:
        return self._review_and_revise("results_review", state)

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
            deterministic = validate_manuscript(
                manuscript,
                context,
                review_type=review_type,
            )
            if review_type == "final_review":
                deterministic.extend(self._prior_unresolved_integrity_items(state))
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
            self._resolve_factually_false_structure_items(issues, manuscript)
            self._resolve_transparently_disclosed_items(issues, manuscript)
            final_issues = [issue for issue in issues if not issue.resolved]
            final_score = quality_score(final_issues, report.score)
            fixable = [
                issue
                for issue in final_issues
                if issue.disposition
                in {
                    IssueDisposition.AUTO_FIX,
                    IssueDisposition.AUTHOR_ACTION,
                    IssueDisposition.INTEGRITY_BLOCKER,
                }
                and (
                    issue.disposition == IssueDisposition.AUTHOR_ACTION
                    or not issue.requires_new_evidence
                )
            ]
            if not fixable or cycle > self.config.workflow.max_review_cycles:
                break

            guard_issues: list[ReviewIssue] = []
            candidate = manuscript
            for revision_attempt in range(1, self.config.provider.max_schema_retries + 2):
                revision_context = self._revision_context(
                    review_type,
                    manuscript,
                    final_issues,
                )
                if guard_issues:
                    revision_context["rejected_revision_guard_issues"] = [
                        issue.model_dump(mode="json") for issue in guard_issues
                    ]
                candidate, revision_response = self.llm.text(
                    role="reviser",
                    system=REVISER_SYSTEM,
                    prompt=revision_prompt(revision_context),
                    metadata={
                        "operation": "revise",
                        "stage": review_type,
                        "review_cycle": cycle,
                        "revision_attempt": revision_attempt,
                    },
                )
                last_model = revision_response.model
                candidate = strip_reference_section(candidate)
                guard_issues = validate_revision(manuscript, candidate, context)
                if not has_integrity_blocker(guard_issues):
                    break
                changes.append(
                    f"Rejected revision attempt {revision_attempt} in cycle {cycle}; "
                    "the prior manuscript was preserved."
                )
            if has_integrity_blocker(guard_issues):
                final_issues = policy.normalize_all(final_issues + guard_issues)
                final_score = quality_score(final_issues, report.score)
                break
            manuscript = candidate
            version = self.store.save_manuscript(manuscript, review_type, state)
            artifacts.append(str(version.relative_to(self.store.root)))
            changes.append(
                f"Applied and validated revision cycle {cycle} for {len(fixable)} issue(s)."
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
            notes=[
                f"Completed {review_type} with {len(final_issues)} unresolved non-dismissed issue(s)."
            ],
            model=last_model,
        )

    def _stage_export(self, state: WorkflowState) -> StageOutcome:
        del state
        # Export is performed by WorkflowEngine after the readiness decision so the
        # quality report contains the final state.
        return StageOutcome(status=StageStatus.PASSED)

    def _draft_chunk(
        self,
        context: dict[str, Any],
        sections: list[OutlineSection],
        group_number: int,
    ):
        feedback = ""
        for attempt in range(1, self.config.provider.max_schema_retries + 2):
            prompt = draft_prompt(context)
            if feedback:
                prompt += "\n\nPrevious draft defect to correct:\n" + feedback
            content, response = self.llm.text(
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
            missing = [
                section.heading
                for section in sections
                if not re.search(
                    rf"(?im)^##\s+(?:\d+(?:\.\d+)*[.)]?\s*)?{re.escape(section.heading)}\s*$",
                    content,
                )
            ]
            if not missing:
                return content, response
            feedback = (
                "The response omitted these exact level-2 section headings: "
                + ", ".join(missing)
                + ". Return the complete requested group."
            )
        raise ModelOutputError(f"Draft group {group_number} remained incomplete: {feedback}")

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
    def _required_sections(paper_type: PaperType, proposed: list[str]) -> list[str]:
        if paper_type == PaperType.REVIEW_ARTICLE:
            mandatory = [
                "Abstract",
                "Keywords",
                "Introduction",
                "Review Methodology",
                "Related Work",
                "Thematic Synthesis",
                "Discussion",
                "Research Gaps and Future Directions",
                "Limitations",
                "Conclusion",
                "Data Availability",
                "Conflict of Interest",
            ]
        else:
            mandatory = [
                "Abstract",
                "Keywords",
                "Introduction",
                "Related Work",
                "Methodology",
                "Results",
                "Discussion",
                "Limitations",
                "Conclusion",
                "Data Availability",
                "Conflict of Interest",
            ]
        normalized = {_normalize_heading(item) for item in mandatory}
        additions: list[str] = []
        for item in proposed:
            heading = _normalize_heading(item)
            if heading in normalized or heading == "references":
                continue
            additions.append(item)
            normalized.add(heading)
        return mandatory + additions

    def _validation_context(self) -> ValidationContext:
        return ValidationContext(
            config=self.config,
            plan=self.store.load_plan(),
            evidence=self.store.load_evidence(),
            references=self.store.load_references(),
        )

    def _review_context(
        self,
        review_type: str,
        manuscript: str,
        deterministic: list[ReviewIssue],
    ) -> dict[str, Any]:
        plan = self.store.load_plan()
        context = {
            "review_type": review_type,
            "research_plan": plan.model_dump(mode="json"),
            "journal_rules": self.config.journal.model_dump(mode="json"),
            "manuscript": manuscript[: self.config.context.max_manuscript_chars],
            "verified_reference_ids": [
                item.id for item in self.store.load_references() if item.verified
            ],
            "user_evidence_ids": [item.id for item in self.store.load_evidence()],
            "deterministic_issues": [issue.model_dump(mode="json") for issue in deterministic],
        }
        if review_type == "final_review":
            context["prior_unresolved_issues"] = [
                issue.model_dump(mode="json")
                for issue in self._prior_unresolved_integrity_items(self.store.load_state())
            ]
        return context

    def _revision_context(
        self,
        review_type: str,
        manuscript: str,
        issues: list[ReviewIssue],
    ) -> dict[str, Any]:
        return {
            "review_type": review_type,
            "research_plan": self.store.load_plan().model_dump(mode="json"),
            "manuscript": manuscript[: self.config.context.max_manuscript_chars],
            "issues": [issue.model_dump(mode="json") for issue in issues],
            "user_evidence": self._evidence_context(self.store.load_evidence()),
            "verified_references": self._reference_context(self.store.load_references()),
            "allowed_reference_ids": [item.id for item in self.store.load_references()],
            "journal_rules": self.config.journal.model_dump(mode="json"),
        }

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
    def _resolve_factually_false_structure_items(
        issues: list[ReviewIssue], manuscript: str
    ) -> None:
        sections = {
            _normalize_heading(heading): body
            for heading, body in markdown_sections(manuscript).items()
        }
        for issue in issues:
            combined = f"{issue.code} {issue.description}".casefold()
            if ("related work" in combined or "relatedwork" in combined) and (
                "empty" in combined or "missing" in combined
            ):
                body = next(
                    (
                        value
                        for heading, value in sections.items()
                        if "related work" in heading or "literature review" in heading
                    ),
                    "",
                )
                if len(body.split()) >= 30:
                    issue.resolved = True
                    issue.resolution = (
                        "The current manuscript contains a substantive related-work section."
                    )
            if (
                issue.code in {"missing_required_section", "empty_required_section"}
                and issue.section
            ):
                body = sections.get(_normalize_heading(issue.section), "")
                if len(body.split()) >= minimum_section_words(_normalize_heading(issue.section)):
                    issue.resolved = True
                    issue.resolution = "The current manuscript contains the required section."

    @staticmethod
    def _resolve_transparently_disclosed_items(issues: list[ReviewIssue], manuscript: str) -> None:
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
            "limitation",
            "outside the scope",
        )
        for issue in issues:
            if issue.disposition not in {
                IssueDisposition.AUTHOR_ACTION,
                IssueDisposition.RECOMMENDATION,
            }:
                continue
            topic = issue.code.split("_", 1)[0]
            if any(
                topic in paragraph and any(term in paragraph for term in disclosure_terms)
                for paragraph in paragraphs
            ):
                issue.resolved = True
                issue.resolution = "The manuscript transparently discloses the scope limitation."

    @staticmethod
    def _prior_unresolved_integrity_items(state: WorkflowState) -> list[ReviewIssue]:
        carried: list[ReviewIssue] = []
        for stage, record in state.stage_records.items():
            if stage == "final_review":
                continue
            for issue in record.issues:
                if issue.resolved or issue.disposition not in {
                    IssueDisposition.AUTHOR_ACTION,
                    IssueDisposition.INTEGRITY_BLOCKER,
                }:
                    continue
                carried.append(issue.model_copy(deep=True))
        return carried


def _chunked(items: list[Any], size: int) -> list[list[Any]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _normalize_heading(value: str) -> str:
    clean = re.sub(r"^\d+(?:\.\d+)*[.)]?\s*", "", value.casefold())
    return re.sub(r"[^a-z0-9]+", " ", clean).strip()


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
    has_number = bool(re.search(r"\d", content))
    return (
        has_number
        and any(term in content for term in method_terms)
        and any(term in content for term in result_terms)
        and any(term in content for term in assertion_terms)
    )
