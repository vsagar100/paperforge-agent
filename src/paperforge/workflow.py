from __future__ import annotations

import json

from paperforge.analytics import analyze
from paperforge.audit import audit_sections, issue, source_text, unresolved_placeholders
from paperforge.config import Settings, load_settings
from paperforge.context import ContextComposer, section_batches, size, text_parts
from paperforge.environment import load_environment
from paperforge.export import export_packet
from paperforge.inputs import Ingestor, input_signature
from paperforge.literature import Literature, LiteratureFailure
from paperforge.llm import EvidenceValidationError, Gateway
from paperforge.prompts import APPRAISE, DRAFT, PLAN, REVIEW
from paperforge.schemas import (
    InputItem,
    Project,
    Review,
    Section,
    Source,
    SourceAssessment,
    StudyPlan,
)
from paperforge.store import STAGES, Store, fingerprint

RESEARCH_SECTIONS = [
    "Introduction",
    "Related Work",
    "Research Gap",
    "Problem Formulation",
    "Proposed Methodology",
    "Mathematical Model / Algorithm",
    "Methodology Flowchart",
    "System / Architecture Block Diagram",
    "Experimental or Simulation Setup",
    "Evaluation Metrics",
    "Results",
    "Comparative Analysis",
    "Statistical Validation",
    "Ablation / Sensitivity Analysis",
    "Discussion",
    "Limitations",
    "Future Work",
    "Conclusion",
    "Declarations",
    "Abstract",
    "Keywords",
]
REVIEW_SECTIONS = [
    "Introduction",
    "Review Methodology",
    "Related Work",
    "Research Gap",
    "Critical Synthesis",
    "Discussion",
    "Limitations",
    "Future Work",
    "Conclusion",
    "Declarations",
    "Abstract",
    "Keywords",
]


class Blocked(RuntimeError):
    def __init__(self, message: str, output: dict):
        super().__init__(message)
        self.output = output


class Workflow:
    """An explicit state machine. Every transition is durable; no hidden LLM loop."""

    def __init__(
        self,
        store: Store,
        settings: Settings | None = None,
        gateway: Gateway | None = None,
        literature: Literature | None = None,
    ):
        load_environment(store.root)
        self.store = store
        self.settings = settings or load_settings(store.config_path)
        self.gateway = gateway or Gateway(store, self.settings)
        self.literature = literature or Literature(store)
        self.project = Project.model_validate(store.get("project"))

    def close(self):
        self.gateway.close()
        self.literature.close()

    def run(self, until: str | None = None) -> dict:
        if until and until not in STAGES:
            raise ValueError("Unknown stop stage")
        with self.store.lock():
            if self.store.get("status") in {"cancelled", "paused"}:
                return self.store.snapshot()
            self._sync()
            if self.store.get("status") == "awaiting_author":
                return self.store.snapshot()
            for stage in STAGES:
                record = self.store.stage(stage)
                if record and record["status"] == "completed":
                    if stage == "export":
                        self.store.set(
                            "status",
                            "completed_with_actions"
                            if record["output"]["author_actions"]
                            else "ready_for_author_review",
                        )
                    if stage == until:
                        break
                    continue
                self.store.start(stage)
                try:
                    output = getattr(self, "_" + stage)()
                    self.store.finish(stage, output)
                except Blocked as exc:
                    self.store.finish(stage, exc.output, "blocked", str(exc))
                    self.store.set("status", "awaiting_author")
                    self._partial_packet()
                    break
                except Exception as exc:
                    self.store.finish(stage, {}, "failed", str(exc))
                    self.store.set("status", "failed")
                    self.store.event(
                        "recovery",
                        {
                            "stage": stage,
                            "action": "Retry with run/resume after correcting the failure",
                        },
                    )
                    raise
                if stage == "export":
                    self.store.set(
                        "status",
                        "completed_with_actions"
                        if output["author_actions"]
                        else "ready_for_author_review",
                    )
                elif stage == until:
                    self.store.set("status", "pending")
                    break
            return self.store.snapshot()

    def _sync(self):
        signature = input_signature(self.store)
        scientific = fingerprint(
            {
                "project": self.project.model_dump(mode="json"),
                "ingestion": self.settings.ingestion.model_dump(mode="json"),
                "target_sources": self.settings.target_sources,
                "minimum_sources": self.settings.minimum_sources,
                "recent_years": self.settings.recent_years,
                "literature_enabled": self.settings.literature_enabled,
                "quality": [
                    self.settings.minimum_section_words,
                    self.settings.abstract_min_words,
                    self.settings.abstract_max_words,
                    self.settings.minimum_cited_sources,
                ],
            }
        )
        prior = self.store.get("input_signature")
        if prior and (prior != signature or self.store.get("scientific_signature") != scientific):
            self.store.invalidate(
                "intake",
                "Evidence or scientific configuration changed; dependent stages require revalidation",
            )
        # Routing and budget changes affect future calls without erasing accepted work.
        self.store.set("input_signature", signature)
        self.store.set("scientific_signature", scientific)
        self.store.set("resolved_settings", self.settings.model_dump(mode="json"))

    def _output(self, stage: str) -> dict:
        return (self.store.stage(stage) or {}).get("output") or {}

    def _inputs(self) -> list[InputItem]:
        return [InputItem.model_validate(item) for item in self._output("intake").get("inputs", [])]

    def _sources(self) -> list[Source]:
        return [
            Source.model_validate(source)
            for source in self._output("literature").get("sources", [])
        ]

    def _sections(self) -> list[Section]:
        reviewed = self._output("review").get("sections")
        return [
            Section.model_validate(section)
            for section in (reviewed or self._output("draft").get("sections", []))
        ]

    def _intake(self) -> dict:
        items = Ingestor(self.store, self.settings.ingestion).run()
        output = {
            "inputs": [item.model_dump(mode="json") for item in items],
            "warnings": [f"{item.path}: {warning}" for item in items for warning in item.warnings],
            "unsupported": [
                item.path
                for item in items
                if item.status in {"unsupported", "failed", "conversion_required"}
            ],
        }
        self.store.write(
            "outputs/input-inventory.json", json.dumps(output, indent=2, ensure_ascii=False)
        )
        return output

    def _literature(self) -> dict:
        sources = (
            self.literature.search(
                self.project.topic, self.settings.target_sources, self.settings.recent_years
            )
            if self.settings.literature_enabled
            else []
        )
        # Explicit DOI-to-file association: don't mistake bibliography DOIs for the PDF's DOI.
        association = self.store.root / "inputs" / "source-dois.json"
        if association.exists():
            mapping = json.loads(association.read_text(encoding="utf-8"))
            if not isinstance(mapping, dict):
                raise ValueError(
                    "source-dois.json must map input-relative filenames to DOI strings"
                )
            by_path = {item.path.removeprefix("inputs/"): item for item in self._inputs()}
            for name, doi in mapping.items():
                item = by_path.get(name)
                if not item or not item.chunks or not isinstance(doi, str):
                    raise ValueError(
                        "Every source DOI association requires an extracted attached file"
                    )
                source = self.literature.crossref(doi)
                source.passages = item.chunks
                source.access_level = "full_text"
                source.verification += "; file association supplied by author"
                sources = [old for old in sources if old.id != source.id] + [source]
        output = {
            "sources": [source.model_dump(mode="json") for source in sources],
            "search_query": self.project.topic,
            "search_limitations": "Ranked search is not exhaustive; metadata does not certify peer review, Scopus/SCI indexing or novelty",
        }
        readable = [source for source in sources if source.abstract or source.passages]
        if len(readable) < self.settings.minimum_sources:
            raise Blocked(
                "Too few accessible sources to support a literature-grounded manuscript",
                {
                    **output,
                    "required_action": "Attach accessible papers with source-dois.json, or choose a defensible source threshold; metadata alone is insufficient",
                },
            )
        assessments = []
        for source in readable:
            material = source_text(source)
            key = "appraisal:" + fingerprint([source.id, material, 1])
            cached = self.store.cached_checkpoint(key)
            assessment = (
                SourceAssessment.model_validate(cached)
                if cached
                else self._appraise(source, material)
            )
            # Revalidate saved checkpoints too; only accepted appraisals may be reused.
            self._validate_appraisal(assessment, source, material)
            self.store.checkpoint(key, assessment.model_dump(mode="json"))
            assessments.append(assessment.model_dump(mode="json"))
        output["assessments"] = assessments
        return output

    def _appraise(self, source: Source, material: str) -> SourceAssessment:
        base = {
            "source_id": source.id,
            "source_title": source.title,
            "access_level": source.access_level,
        }
        full = {**base, "accessible_text": material}
        if size(full) <= self.settings.max_context_chars:
            parts = [(0, material)]
        else:
            overhead = size(
                {
                    **base,
                    "accessible_text": "",
                    "appraisal_batch": {"index": 0, "count": 0, "start": 0},
                }
            )
            budget = min(30000, self.settings.max_context_chars - overhead - 128)
            parts = text_parts(material, budget)
        assessments = []
        for index, (start, text) in enumerate(parts):
            context = {**base, "accessible_text": text}
            if len(parts) > 1:
                context["appraisal_batch"] = {"index": index, "count": len(parts), "start": start}
            key = "appraisal-batch:" + fingerprint(context)
            cached = self.store.cached_checkpoint(key)
            assessment = (
                SourceAssessment.model_validate(cached)
                if cached
                else self.gateway.structured(
                    SourceAssessment,
                    stage="literature",
                    role="extractor",
                    system=APPRAISE,
                    context=context,
                    validate=lambda value, text=text: self._validate_appraisal(value, source, text),
                )
            )
            self._validate_appraisal(assessment, source, text)
            self.store.checkpoint(key, assessment.model_dump(mode="json"))
            assessments.append(assessment)
        merged = assessments[0].model_copy(deep=True)
        fields = ("method", "dataset_or_system", "finding", "reported_limitation")
        for field in fields:
            setattr(
                merged,
                field,
                next((getattr(a, field) for a in assessments if getattr(a, field)), None),
            )
        if len(parts) > 1:
            # Missing within one batch is never presented as missing from the entire paper.
            merged.missing_from_accessible_text = [
                field for field in fields if getattr(merged, field) is None
            ]
            self.store.write(
                "audit/appraisal/" + fingerprint([source.id, material]) + ".json",
                json.dumps(
                    {
                        "source_id": source.id,
                        "scope": "All accessible text appraised in contiguous batches; canonical matrix retains first supported fact per field",
                        "batches": [
                            {
                                "start": start,
                                "end": start + len(text),
                                "assessment": a.model_dump(mode="json"),
                            }
                            for (start, text), a in zip(parts, assessments, strict=True)
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
        return merged

    @staticmethod
    def _validate_appraisal(assessment: SourceAssessment, source: Source, material: str) -> None:
        errors = []
        if assessment.source_id != source.id:
            errors.append("source_id differs from the supplied source_id")
        for field in ("method", "dataset_or_system", "finding", "reported_limitation"):
            fact = getattr(assessment, field)
            if fact and (not fact.quote.strip() or fact.quote not in material):
                errors.append(f"{field}.quote does not occur exactly in accessible_text")
        if errors:
            raise EvidenceValidationError(
                f"Source appraisal {source.id}: {'; '.join(errors)}. "
                "Use the supplied source_id and verbatim supporting quotes; "
                "return null for unsupported facts. If repairs fail, select another extractor "
                "model or supply accessible source text, then resume."
            )

    def _legacy_context(self) -> dict:
        # Explicit excerpt selection; do not claim complete reading of omitted document chunks.
        inputs = [
            {
                "id": item.id,
                "role": item.role,
                "status": item.status,
                "path": item.path,
                "excerpts": item.chunks[:20],
                "omitted_chunks": max(0, len(item.chunks) - 20),
                "warnings": item.warnings,
            }
            for item in self._inputs()
            if item.chunks
        ]
        return {
            "project": self.project.model_dump(mode="json"),
            "sources": [source.model_dump(mode="json") for source in self._sources()],
            "source_assessments": self._output("literature").get("assessments", []),
            "inputs": inputs,
            "results": self._output("analysis").get("results", []),
        }

    def _context(self, task: dict | None = None) -> dict:
        return ContextComposer(self.store, self.settings.max_context_chars).compose(
            project=self.project.model_dump(mode="json"),
            sources=self._sources(),
            assessments=self._output("literature").get("assessments", []),
            inputs=self._inputs(),
            results=self._output("analysis").get("results", []),
            task=task,
        )

    def _validate_section(self, section: Section, title: str) -> None:
        if section.title != title:
            raise EvidenceValidationError(f"Return the requested section title {title!r}")
        findings = audit_sections(
            [section], self._sources(), self._inputs(), self._output("analysis").get("results", [])
        )
        if findings:
            raise EvidenceValidationError(
                "Section failed evidence gates: " + "; ".join(f.description for f in findings)
            )

    def _plan(self) -> dict:
        def validate(plan):
            if set(plan.gap_source_ids) - {source.id for source in self._sources()}:
                raise EvidenceValidationError("Research gap cites unknown sources")

        plan = self.gateway.structured(
            StudyPlan,
            stage="plan",
            role="planner",
            system=PLAN,
            context=self._context(),
            validate=validate,
        )
        validate(plan)
        candidates, warnings = [], []
        if plan.missing_evidence and plan.dataset_search_terms and self.settings.literature_enabled:
            try:
                candidates = self.literature.dataset_candidates(plan.dataset_search_terms)
            except LiteratureFailure as exc:
                warnings.append(str(exc))
        return {
            "plan": plan.model_dump(mode="json"),
            "dataset_candidates": candidates,
            "warnings": warnings,
            "novelty_status": "provisional within searched accessible literature",
        }

    def _analysis(self) -> dict:
        results = analyze(self._inputs())
        sources = self._sources()
        results.append(
            {
                "id": "SEARCH-" + fingerprint([self.project.topic, [s.id for s in sources]])[:12],
                "input_id": "verified-source-register",
                "calculator": "literature_inventory_v1",
                "metrics": {
                    "retrieved_source_count": len(sources),
                    "accessible_source_count": sum(bool(s.abstract or s.passages) for s in sources),
                },
                "query": self.project.topic,
                "assumptions": [
                    "Ranked search is not exhaustive and does not establish indexing or global novelty"
                ],
            }
        )
        return {
            "results": results,
            "unperformed_experiments": self._output("plan").get("plan", {}).get("experiments", []),
            "execution_policy": "Only built-in deterministic calculators ran. Uploaded/model-generated code, physical experiments and native simulations were not executed",
        }

    def _draft(self) -> dict:
        has_data = any(item.role == "dataset" and item.chunks for item in self._inputs()) or any(
            result.get("calculator") == "confusion_matrix_v1"
            for result in self._output("analysis").get("results", [])
        )
        resolved = self.project.paper_type
        if resolved == "auto":
            resolved = "original_research" if has_data else "review"
        titles = RESEARCH_SECTIONS if resolved == "original_research" else REVIEW_SECTIONS
        sections = []
        for title in titles:
            task = {
                "plan": self._output("plan")["plan"],
                "paper_type": resolved,
                "requested_section": title,
                "generation": self.store.get("epoch:draft", 0),
                "section_word_target": [
                    self.settings.abstract_min_words,
                    self.settings.abstract_max_words,
                ]
                if title == "Abstract"
                else self.settings.minimum_section_words,
            }
            # Accepted writing belongs to canonical evidence, not to one packet layout.
            key = "section:" + fingerprint([title, {**self._legacy_context(), **task}, 1])
            cached = self.store.cached_checkpoint(key)
            if cached:
                section = Section.model_validate(cached)
                self._validate_section(section, title)
            else:
                context = self._context(task)
                section = self.gateway.structured(
                    Section,
                    stage="draft",
                    role="writer",
                    system=DRAFT,
                    context=context,
                    validate=lambda value, title=title: self._validate_section(value, title),
                )
                if section.title != title:
                    raise ValueError(f"Expected section {title!r}, received {section.title!r}")
                issues = audit_sections(
                    [section],
                    self._sources(),
                    self._inputs(),
                    self._output("analysis").get("results", []),
                )
                for _ in range(self.settings.max_schema_repairs):
                    if not issues:
                        break
                    context = self._context(
                        {
                            **task,
                            "corrections": [finding.model_dump(mode="json") for finding in issues],
                            "previous_section": section.model_dump(mode="json"),
                        }
                    )
                    section = self.gateway.structured(
                        Section,
                        stage="draft",
                        role="reviser",
                        system=DRAFT,
                        context=context,
                        validate=lambda value, title=title: self._validate_section(value, title),
                    )
                    issues = audit_sections(
                        [section],
                        self._sources(),
                        self._inputs(),
                        self._output("analysis").get("results", []),
                    )
                if issues or section.title != title:
                    raise Blocked(
                        "Section failed evidence gates",
                        {
                            "issues": [finding.model_dump(mode="json") for finding in issues],
                            "section": section.model_dump(mode="json"),
                            "completed_sections": [s.model_dump(mode="json") for s in sections],
                        },
                    )
                self.store.checkpoint(key, section.model_dump(mode="json"))
            sections.append(section)
        return {
            "paper_type": resolved,
            "sections": [section.model_dump(mode="json") for section in sections],
        }

    def _review_batches(self, sections: list[Section], round_id: int) -> Review:
        batches = section_batches(
            sections,
            self.settings.max_context_chars // 3,
            max_paragraph_budget=self.settings.max_context_chars * 2 // 3,
        )
        outline = [
            {
                "title": s.title,
                "paragraphs": len(s.paragraphs),
                "words": sum(len(p.text.split()) for p in s.paragraphs),
                "source_ids": sorted(
                    {identifier for p in s.paragraphs for identifier in p.source_ids}
                ),
            }
            for s in sections
        ]
        known_titles = {s.title for s in sections}

        def validate(review):
            if any(finding.section not in known_titles for finding in review.issues):
                raise EvidenceValidationError("Reviewer must identify an existing section")

        reviews = []
        for index, batch in enumerate(batches):
            context = self._context(
                {
                    "plan": self._output("plan")["plan"],
                    "sections": batch,
                    "manuscript_outline": outline,
                    "review_batch": {
                        "index": index,
                        "count": len(batches),
                        "scope": "Review the supplied paragraphs; use outline for manuscript structure. Other batches cover remaining paragraphs.",
                    },
                    "generation": self.store.get("epoch:review", 0),
                    "review_round": round_id,
                }
            )
            key = "review-batch:" + fingerprint(context)
            cached = self.store.cached_checkpoint(key)
            review = (
                Review.model_validate(cached)
                if cached
                else self.gateway.structured(
                    Review,
                    stage="review",
                    role="reviewer",
                    system=REVIEW,
                    context=context,
                    validate=validate,
                )
            )
            validate(review)
            # Do not cache author blockers: a continue decision must allow a fresh assessment.
            if not any(finding.blocking and finding.needs_author for finding in review.issues):
                self.store.checkpoint(key, review.model_dump(mode="json"))
            reviews.append(review)
        issues, seen = [], set()
        for review in reviews:
            for finding in review.issues:
                key = fingerprint(finding.model_dump(mode="json"))
                if key not in seen:
                    seen.add(key)
                    issues.append(finding)
        return Review(issues=issues, summary="\n".join(r.summary for r in reviews))

    def _review(self) -> dict:
        context_key = "review:" + fingerprint(
            [
                self._output("draft"),
                self._legacy_context(),
                self._output("plan"),
                self.store.get("epoch:review", 0),
            ]
        )
        checkpoint = self.store.cached_checkpoint(context_key)
        sections = [
            Section.model_validate(section)
            for section in (checkpoint or self._output("draft"))["sections"]
        ]
        completed_rounds = (checkpoint or {}).get("round", 0)
        for round_id in range(completed_rounds, self.settings.max_review_rounds + 1):
            review = self._review_batches(sections, round_id)
            review.issues += audit_sections(
                sections,
                self._sources(),
                self._inputs(),
                self._output("analysis").get("results", []),
            )
            output = {
                "sections": [section.model_dump(mode="json") for section in sections],
                "review": review.model_dump(mode="json"),
                "round": round_id,
            }
            for section in sections:
                text = " ".join(p.text for p in section.paragraphs)
                words = len(text.split())
                if (
                    section.title == "Abstract"
                    and not self.settings.abstract_min_words
                    <= words
                    <= self.settings.abstract_max_words
                ):
                    review.issues.append(
                        issue(
                            section.title,
                            "Abstract word count outside configured limits",
                            "writing",
                        )
                    )
                elif section.title == "Keywords":
                    terms = [term for term in text.replace(";", ",").split(",") if term.strip()]
                    if not 5 <= len(terms) <= 8:
                        review.issues.append(
                            issue(
                                section.title,
                                "Provide 5–8 comma-separated technical keywords",
                                "format",
                            )
                        )
                elif (
                    section.title != "Declarations" and words < self.settings.minimum_section_words
                ):
                    review.issues.append(
                        issue(
                            section.title,
                            "Section lacks configured minimum depth; develop the supported argument",
                            "writing",
                        )
                    )
            cited = {
                identifier
                for section in sections
                for p in section.paragraphs
                for identifier in p.source_ids
            }
            if len(cited) < self.settings.minimum_cited_sources:
                review.issues.append(
                    issue(
                        "Related Work",
                        "Too few distinct supporting sources; add relevant evidence rather than citation padding",
                        "citation",
                    )
                )
            output["review"] = review.model_dump(mode="json")
            if not any(finding.blocking for finding in review.issues):
                return output
            if round_id >= self.settings.max_review_rounds or any(
                finding.blocking and finding.needs_author for finding in review.issues
            ):
                raise Blocked(
                    "Review requires author evidence or exceeded automatic revision limit", output
                )
            for index, section in enumerate(sections):
                issues = [
                    finding.model_dump(mode="json")
                    for finding in review.issues
                    if finding.section == section.title
                ]
                if not issues:
                    continue
                revised = self.gateway.structured(
                    Section,
                    stage="review",
                    role="reviser",
                    system=DRAFT,
                    context=self._context(
                        {
                            "requested_section": section.title,
                            "plan": self._output("plan")["plan"],
                            "previous_section": section.model_dump(mode="json"),
                            "corrections": issues,
                        }
                    ),
                    validate=lambda value, title=section.title: self._validate_section(
                        value, title
                    ),
                )
                if revised.title != section.title:
                    raise ValueError("Revision changed requested section title")
                guard = audit_sections(
                    [revised],
                    self._sources(),
                    self._inputs(),
                    self._output("analysis").get("results", []),
                )
                if guard:
                    raise Blocked(
                        "Revision introduced unsupported content; previous sections retained",
                        {
                            **output,
                            "rejected_revision": revised.model_dump(mode="json"),
                            "issues": [i.model_dump(mode="json") for i in guard],
                        },
                    )
                sections[index] = revised
                # Each accepted section survives a crash midway through the revision round.
                self.store.checkpoint(
                    context_key,
                    {"sections": [s.model_dump(mode="json") for s in sections], "round": round_id},
                )
            self.store.checkpoint(
                context_key,
                {"sections": [s.model_dump(mode="json") for s in sections], "round": round_id + 1},
            )
        raise RuntimeError("Review loop ended unexpectedly")

    def _export(self) -> dict:
        sections = self._sections()
        actions = unresolved_placeholders(sections)
        if self._output("draft").get("paper_type") == "original_research":
            actions.extend(self._output("plan").get("plan", {}).get("missing_evidence", []))
        actions.extend(self._output("intake").get("warnings", []))
        if self._output("plan").get("plan", {}).get("figures"):
            actions.append(
                "Proposed diagrams require author verification and figure callouts in the manuscript"
            )
        return export_packet(
            self.store,
            sections,
            self._sources(),
            self._output("analysis").get("results", []),
            actions,
            review=self._output("review").get("review"),
            recent_years=self.settings.recent_years,
            plan=self._output("plan").get("plan"),
            assessments=self._output("literature").get("assessments", []),
        )

    def _partial_packet(self):
        sections = self._sections()
        if not sections:
            sections = [
                Section.model_validate(s)
                for s in self._output("draft").get("completed_sections", [])
            ]
        if sections:
            export_packet(
                self.store,
                sections,
                self._sources(),
                self._output("analysis").get("results", []),
                ["Workflow blocked; this is an incomplete draft", self.store.get("status")],
                review=self._output("review").get("review"),
                recent_years=self.settings.recent_years,
            )
