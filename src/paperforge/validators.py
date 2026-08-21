from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher

from paperforge.citations import (
    cited_reference_ids,
    invalid_reference_markers,
    normalize_citation_markers,
    strip_reference_section,
    unknown_reference_ids,
)
from paperforge.config import AppConfig
from paperforge.domain import (
    ClaimLedger,
    DraftBatch,
    EvidenceCoverage,
    EvidenceItem,
    IssueDisposition,
    OutlineSection,
    PaperType,
    PublicationProfile,
    ReferenceRecord,
    ResearchPlan,
    ReviewIssue,
    Severity,
)
from paperforge.standards import normalize_heading, section_minimum

INTEGRITY_CODES = {
    "fabricated_citation",
    "invalid_citation_syntax",
    "unknown_citation",
    "unverified_citation",
    "unsupported_numeric_claim",
    "unsupported_declaration",
    "unsupported_result",
    "factual_contradiction",
    "contradictory_result",
    "missing_required_section",
    "duplicate_section",
    "unexpected_section",
    "empty_required_section",
    "section_too_short",
    "unresolved_placeholder",
    "manuscript_truncated",
    "related_work_empty",
    "draft_heading_mismatch",
}

OPTIONAL_PATTERNS = {
    "baseline": "external_baseline_not_in_scope",
    "simulation": "simulation_not_in_scope",
    "regulat": "regulatory_detail_not_in_scope",
}

DISCLOSURE_PATTERNS = {
    "calibrat": "calibration_not_reported",
    "uncertaint": "uncertainty_not_reported",
    "reproduc": "reproducibility_detail_not_reported",
    "data availability": "data_availability_not_reported",
}

SECTION_ALIASES = {
    "methodology": {
        "methodology",
        "materials and methods",
        "methods",
        "review methodology",
    },
    "materials and methods": {"materials and methods", "methodology", "methods"},
    "results": {"results", "findings", "thematic findings", "thematic synthesis"},
    "related work": {
        "related work",
        "literature review",
        "background and related work",
        "thematic analysis",
    },
    "limitations": {"limitations", "study limitations", "limitations and future work"},
    "conclusion": {"conclusion", "conclusions"},
}

DECLARATION_TOPICS = {
    "data availability": ("data", "dataset", "repository", "available", "access"),
    "conflict of interest": ("conflict", "competing interest"),
    "funding": ("funding", "funded", "grant", "sponsor"),
    "author contributions": ("author contribution", "contributor", "credit"),
    "declaration of ai use": ("ai use", "artificial intelligence", "language model"),
}


@dataclass(slots=True)
class ValidationContext:
    config: AppConfig
    plan: ResearchPlan
    evidence: list[EvidenceItem]
    references: list[ReferenceRecord]
    publication_profile: PublicationProfile | None = None
    claim_ledger: ClaimLedger | None = None
    evidence_coverage: EvidenceCoverage | None = None


@dataclass(frozen=True, slots=True)
class SectionBlock:
    heading: str
    body: str
    level: int
    start: int
    end: int


class IssuePolicy:
    """Convert reviewer opinions into scope-aware, deterministic dispositions."""

    def __init__(self, context: ValidationContext) -> None:
        self.context = context

    def normalize(self, issue: ReviewIssue) -> ReviewIssue:
        combined = f"{issue.code} {issue.description} {issue.required_change}".casefold()
        if ("related work" in combined or "relatedwork" in combined) and (
            "empty" in combined or "missing" in combined
        ):
            issue.code = "related_work_empty"
            issue.severity = Severity.BLOCKING
            issue.disposition = IssueDisposition.INTEGRITY_BLOCKER
            issue.requires_new_evidence = False
            return issue
        if issue.code in INTEGRITY_CODES or any(
            token in combined
            for token in (
                "fabricated citation",
                "invalid citation",
                "unknown citation",
                "unverified citation",
                "unsupported numeric",
                "unsupported result",
                "unsupported declaration",
                "factual contradiction",
                "contradictory result",
                "empty required section",
                "unresolved placeholder",
                "duplicate section",
            )
        ):
            issue.severity = Severity.BLOCKING
            issue.disposition = IssueDisposition.INTEGRITY_BLOCKER
            return issue
        for pattern, code in OPTIONAL_PATTERNS.items():
            if pattern in combined:
                if not self._explicitly_required(pattern):
                    issue.code = code
                    issue.severity = Severity.LOW
                    issue.disposition = IssueDisposition.RECOMMENDATION
                    issue.requires_new_evidence = True
                    return issue
                required_codes = {
                    "baseline": "baseline_required_by_scope",
                    "simulation": "simulation_required_by_scope",
                    "regulat": "regulatory_required_by_scope",
                }
                issue.code = required_codes[pattern]
                issue.severity = Severity.HIGH
                issue.disposition = IssueDisposition.AUTHOR_ACTION
                issue.requires_new_evidence = True
                return issue
        for pattern, code in DISCLOSURE_PATTERNS.items():
            if pattern in combined:
                issue.code = code
                issue.severity = Severity.MEDIUM
                issue.disposition = IssueDisposition.AUTHOR_ACTION
                issue.requires_new_evidence = True
                return issue
        issue.disposition = (
            IssueDisposition.AUTHOR_ACTION
            if issue.requires_new_evidence
            else IssueDisposition.AUTO_FIX
        )
        if issue.severity == Severity.BLOCKING:
            issue.severity = Severity.HIGH
        return issue

    def normalize_all(self, issues: list[ReviewIssue]) -> list[ReviewIssue]:
        unique: dict[tuple[str, str | None, str], ReviewIssue] = {}
        for issue in issues:
            normalized = self.normalize(issue)
            key = (normalized.code, normalized.section, normalized.description.casefold())
            unique.setdefault(key, normalized)
        return list(unique.values())

    def _explicitly_required(self, term: str) -> bool:
        structured_requirements = " ".join(
            [
                *self.context.plan.required_sections,
                *self.context.config.journal.required_sections,
                *self.context.config.journal.required_declarations,
            ]
        ).casefold()
        if term in structured_requirements:
            return True
        scope = self.context.plan.scope.casefold()
        scope_patterns = {
            "baseline": ("formal comparison", "benchmark evaluation", "comparative evaluation"),
            "simulation": ("simulation study", "simulation-based", "simulation evaluation"),
            "regulat": ("regulatory compliance analysis", "regulatory compliance evaluation"),
        }
        return any(pattern in scope for pattern in scope_patterns.get(term, ()))


def validate_draft_batch(
    batch: DraftBatch,
    requested: list[OutlineSection],
    context: ValidationContext,
) -> list[ReviewIssue]:
    issues: list[ReviewIssue] = []
    expected = [normalize_heading(section.heading) for section in requested]
    actual = [normalize_heading(section.heading) for section in batch.sections]
    if actual != expected:
        issues.append(
            _issue(
                "draft_heading_mismatch",
                Severity.BLOCKING,
                "Manuscript",
                f"Draft headings do not match the requested group. Expected {expected}; got {actual}.",
                "Return exactly one section for each requested heading, in order.",
                IssueDisposition.INTEGRITY_BLOCKER,
            )
        )
        return issues
    allowed_claims = (
        {claim.id for claim in context.claim_ledger.claims} if context.claim_ledger else set()
    )
    allowed_evidence = {item.id for item in context.evidence}
    allowed_references = {item.id for item in context.references}
    requested_by_heading = {normalize_heading(item.heading): item for item in requested}
    for section in batch.sections:
        normalized = normalize_heading(section.heading)
        specification = requested_by_heading[normalized]
        body = normalize_citation_markers(section.body).strip()
        if re.search(r"(?m)^#{1,2}\s+", body):
            issues.append(
                _issue(
                    "unexpected_section",
                    Severity.BLOCKING,
                    section.heading,
                    "A structured section body contains a level-1 or level-2 heading.",
                    "Return body content only; PaperForge owns manuscript assembly.",
                    IssueDisposition.INTEGRITY_BLOCKER,
                )
            )
        if allowed_claims and set(section.claim_ids) - allowed_claims:
            issues.append(
                _issue(
                    "unknown_claim_provenance",
                    Severity.BLOCKING,
                    section.heading,
                    "The draft declares claim IDs outside the registered claim ledger.",
                    "Use only assigned registered claim IDs.",
                    IssueDisposition.INTEGRITY_BLOCKER,
                )
            )
        if set(section.evidence_ids) - allowed_evidence:
            issues.append(
                _issue(
                    "unknown_evidence_provenance",
                    Severity.BLOCKING,
                    section.heading,
                    "The draft declares evidence IDs outside the evidence registry.",
                    "Use only assigned registered evidence IDs.",
                    IssueDisposition.INTEGRITY_BLOCKER,
                )
            )
        if set(section.reference_ids) - allowed_references:
            issues.append(
                _issue(
                    "unknown_citation",
                    Severity.BLOCKING,
                    section.heading,
                    "The draft declares references outside the verified catalogue.",
                    "Use only assigned verified references.",
                    IssueDisposition.INTEGRITY_BLOCKER,
                )
            )
        permitted_claims = set(specification.claim_ids)
        permitted_evidence = set(specification.evidence_ids)
        permitted_references = set(specification.reference_ids)
        if permitted_claims and set(section.claim_ids) - permitted_claims:
            issues.append(
                _issue(
                    "cross_section_claim_leakage",
                    Severity.BLOCKING,
                    section.heading,
                    "The section uses a claim not assigned by the outline.",
                    "Use only the claim ledger entries assigned to this section.",
                    IssueDisposition.INTEGRITY_BLOCKER,
                )
            )
        if permitted_evidence and set(section.evidence_ids) - permitted_evidence:
            issues.append(
                _issue(
                    "cross_section_evidence_leakage",
                    Severity.BLOCKING,
                    section.heading,
                    "The section uses evidence not assigned by the outline.",
                    "Use only evidence assigned to this section.",
                    IssueDisposition.INTEGRITY_BLOCKER,
                )
            )
        if permitted_references and set(section.reference_ids) - permitted_references:
            issues.append(
                _issue(
                    "cross_section_reference_leakage",
                    Severity.BLOCKING,
                    section.heading,
                    "The section uses a reference not assigned by the outline.",
                    "Use only references assigned to this section.",
                    IssueDisposition.INTEGRITY_BLOCKER,
                )
            )
        cited_in_body = set(cited_reference_ids(body))
        if cited_in_body - permitted_references:
            issues.append(
                _issue(
                    "cross_section_reference_leakage",
                    Severity.BLOCKING,
                    section.heading,
                    "The section body cites a reference not assigned by the outline.",
                    "Use only the verified references assigned to this section.",
                    IssueDisposition.INTEGRITY_BLOCKER,
                )
            )
        fragment = f"# Draft\n\n## {section.heading}\n\n{body}\n"
        issues.extend(_validate_internal_markers(fragment))
        issues.extend(_validate_citation_integrity(fragment, context, coverage=False))
        if context.config.quality.require_supported_numeric_claims:
            issues.extend(_validate_numeric_claims(fragment, context))
        issues.extend(_validate_section_depth(section.heading, body, context))
        issues.extend(_validate_declaration(section.heading, body, context))
    return IssuePolicy(context).normalize_all(issues)


def validate_manuscript(
    manuscript: str,
    context: ValidationContext,
    *,
    review_type: str,
) -> list[ReviewIssue]:
    manuscript = normalize_citation_markers(manuscript)
    issues: list[ReviewIssue] = []
    sections = markdown_sections(manuscript)
    normalized_headings = {normalize_heading(heading): body for heading, body in sections.items()}

    if review_type in {"writing_review", "journal_review", "final_review"}:
        issues.extend(_validate_structure(manuscript, context))
        issues.extend(_validate_abstract(normalized_headings, context))
        issues.extend(_validate_keywords(normalized_headings, context))
        issues.extend(_validate_internal_markers(manuscript))
        issues.extend(_validate_declarations(normalized_headings, context))

    if review_type in {"journal_review", "final_review"}:
        issues.extend(_validate_publication_contract(context))

    if review_type in {"evidence_review", "final_review"}:
        issues.extend(_validate_citation_integrity(manuscript, context, coverage=True))
        if context.config.quality.require_supported_numeric_claims:
            issues.extend(_validate_numeric_claims(manuscript, context))
        issues.extend(_validate_literature_grounding(normalized_headings, context))

    if review_type in {"methodology_review", "final_review"}:
        issues.extend(_validate_method_disclosure(normalized_headings, context))

    if review_type in {"results_review", "final_review"}:
        issues.extend(_validate_results_alignment(normalized_headings, context))
        issues.extend(_validate_tables(manuscript, context))

    if review_type in {"discussion_review", "final_review"}:
        issues.extend(_validate_discussion(normalized_headings, context))

    if review_type in {"writing_review", "journal_review", "final_review"}:
        word_count = _word_count(strip_reference_section(manuscript))
        minimum = _minimum_manuscript_words(context)
        maximum = context.publication_profile.word_max if context.publication_profile else None
        if word_count < minimum:
            issues.append(
                _issue(
                    "manuscript_too_short",
                    Severity.HIGH,
                    "Manuscript",
                    f"The manuscript contains {word_count} words; the evidence-based minimum is {minimum}.",
                    "Develop the supported methods, results, synthesis, and discussion without filler.",
                    IssueDisposition.AUTO_FIX,
                )
            )
        if maximum and word_count > maximum:
            issues.append(
                _issue(
                    "manuscript_too_long",
                    Severity.HIGH,
                    "Manuscript",
                    f"The manuscript contains {word_count} words; the journal profile maximum is {maximum}.",
                    "Remove repetition while preserving evidence and required detail.",
                    IssueDisposition.AUTO_FIX,
                )
            )
    return IssuePolicy(context).normalize_all(issues)


def validate_revision(
    previous: str,
    candidate: str,
    context: ValidationContext,
) -> list[ReviewIssue]:
    candidate = normalize_citation_markers(candidate)
    issues: list[ReviewIssue] = []
    previous_words = _word_count(previous)
    candidate_words = _word_count(candidate)
    if previous_words and candidate_words < previous_words * 0.85:
        issues.append(
            _issue(
                "manuscript_truncated",
                Severity.BLOCKING,
                "Manuscript",
                f"The proposed revision drops from {previous_words} to {candidate_words} words.",
                "Reject the truncated revision and preserve the complete manuscript.",
                IssueDisposition.INTEGRITY_BLOCKER,
            )
        )
    issues.extend(_validate_citation_integrity(candidate, context, coverage=False))
    previous_atoms = {_canonical_numeric_atom(atom) for atom in _numeric_atoms(previous)}
    evidence_atoms = _evidence_numeric_atoms(context.evidence)
    for claim in _claim_units(candidate):
        cited = cited_reference_ids(claim)
        source_atoms = _reference_numeric_atoms(cited, context.references)
        for atom in _numeric_atoms(claim):
            canonical = _canonical_numeric_atom(atom)
            if (
                canonical in previous_atoms
                or canonical in evidence_atoms
                or canonical in source_atoms
            ):
                continue
            issues.append(
                _issue(
                    "unsupported_numeric_claim",
                    Severity.BLOCKING,
                    "Manuscript",
                    f"The revision introduced numeric content not found in evidence or the cited source: {atom}.",
                    "Remove the number or support it with registered evidence/source text.",
                    IssueDisposition.INTEGRITY_BLOCKER,
                )
            )
    issues.extend(_validate_internal_markers(candidate))
    issues.extend(_validate_duplicate_sections(candidate))
    return IssuePolicy(context).normalize_all(issues)


def quality_score(issues: list[ReviewIssue], model_score: float | None = None) -> float:
    weights = {
        Severity.INFO: 0.0,
        Severity.LOW: 0.02,
        Severity.MEDIUM: 0.06,
        Severity.HIGH: 0.15,
        Severity.BLOCKING: 0.35,
    }
    deterministic = max(
        0.0,
        1.0 - sum(weights[issue.severity] for issue in issues if not issue.resolved),
    )
    del model_score
    return deterministic


def has_integrity_blocker(issues: list[ReviewIssue]) -> bool:
    return any(
        not issue.resolved and issue.disposition == IssueDisposition.INTEGRITY_BLOCKER
        for issue in issues
    )


def markdown_section_blocks(manuscript: str) -> list[SectionBlock]:
    matches = list(re.finditer(r"(?m)^(#{1,3})\s+(.+?)\s*$", manuscript))
    blocks: list[SectionBlock] = []
    for index, match in enumerate(matches):
        level = len(match.group(1))
        end = len(manuscript)
        for following in matches[index + 1 :]:
            if len(following.group(1)) <= level:
                end = following.start()
                break
        blocks.append(
            SectionBlock(
                heading=match.group(2).strip(),
                body=manuscript[match.end() : end].strip(),
                level=level,
                start=match.start(),
                end=end,
            )
        )
    return blocks


def markdown_sections(manuscript: str) -> dict[str, str]:
    return {block.heading: block.body for block in markdown_section_blocks(manuscript)}


def minimum_section_words(heading: str) -> int:
    normalized = normalize_heading(heading)
    if "keyword" in normalized:
        return 3
    if any(
        term in normalized
        for term in (
            "data availability",
            "conflict of interest",
            "funding",
            "author contribution",
            "declaration of ai",
            "ethics",
        )
    ):
        return 5
    if normalized == "abstract":
        return 80
    return 30


def _validate_structure(manuscript: str, context: ValidationContext) -> list[ReviewIssue]:
    issues: list[ReviewIssue] = []
    blocks = markdown_section_blocks(manuscript)
    titles = [block for block in blocks if block.level == 1]
    if len(titles) != 1:
        issues.append(
            _issue(
                "invalid_title_structure",
                Severity.HIGH,
                "Manuscript",
                f"The manuscript must contain exactly one level-1 title; found {len(titles)}.",
                "Keep one manuscript title and use level-2 headings for sections.",
                IssueDisposition.AUTO_FIX,
            )
        )
    if any(normalize_heading(block.heading) == "references" for block in blocks):
        issues.append(
            _issue(
                "model_generated_references",
                Severity.HIGH,
                "References",
                "The working manuscript contains a model-generated References section.",
                "Remove it; PaperForge renders references deterministically.",
                IssueDisposition.AUTO_FIX,
            )
        )
    issues.extend(_validate_duplicate_sections(manuscript))
    level_two = [block for block in blocks if block.level == 2]
    by_heading = {normalize_heading(block.heading): block.body for block in level_two}
    actual_order = [normalize_heading(block.heading) for block in level_two]
    last_position = -1
    for raw in context.plan.required_sections:
        normalized = normalize_heading(raw)
        matched = _find_heading_key(by_heading, normalized)
        if matched is None:
            issues.append(
                _issue(
                    "missing_required_section",
                    Severity.BLOCKING,
                    raw,
                    f"Required section '{raw}' is missing.",
                    "Create a substantive evidence-grounded section.",
                    IssueDisposition.INTEGRITY_BLOCKER,
                )
            )
            continue
        position = actual_order.index(matched)
        if position < last_position:
            issues.append(
                _issue(
                    "section_order_mismatch",
                    Severity.HIGH,
                    raw,
                    f"Required section '{raw}' is out of publication-profile order.",
                    "Restore the configured section order.",
                    IssueDisposition.AUTO_FIX,
                )
            )
        last_position = max(last_position, position)
        body = by_heading[matched]
        minimum = _section_minimum(context, raw)
        if _word_count(body) < minimum:
            code = (
                "related_work_empty"
                if "related" in normalized or "literature" in normalized
                else "section_too_short"
            )
            issues.append(
                _issue(
                    code,
                    Severity.BLOCKING,
                    raw,
                    f"Section '{raw}' has {_word_count(body)} words; at least {minimum} are required.",
                    "Develop the section from assigned evidence and verified sources.",
                    IssueDisposition.INTEGRITY_BLOCKER,
                )
            )
    return issues


def _validate_duplicate_sections(manuscript: str) -> list[ReviewIssue]:
    seen: dict[str, int] = {}
    issues: list[ReviewIssue] = []
    for block in markdown_section_blocks(manuscript):
        if block.level != 2:
            continue
        normalized = normalize_heading(block.heading)
        canonical = _canonical_section_heading(normalized)
        seen[canonical] = seen.get(canonical, 0) + 1
    for heading, count in seen.items():
        if count > 1:
            issues.append(
                _issue(
                    "duplicate_section",
                    Severity.BLOCKING,
                    heading.title(),
                    f"The manuscript contains {count} level-2 versions of '{heading}'.",
                    "Keep one authoritative section and merge only supported unique content.",
                    IssueDisposition.INTEGRITY_BLOCKER,
                )
            )
    return issues


def _validate_abstract(sections: dict[str, str], context: ValidationContext) -> list[ReviewIssue]:
    abstract = _find_section(sections, "abstract")
    if abstract is None:
        return []
    count = _word_count(abstract)
    minimum = (
        context.publication_profile.abstract_min_words
        if context.publication_profile
        else min(150, context.config.journal.abstract_max_words)
    )
    maximum = (
        context.publication_profile.abstract_max_words
        if context.publication_profile
        else context.config.journal.abstract_max_words
    )
    issues: list[ReviewIssue] = []
    if count < minimum or count > maximum:
        issues.append(
            _issue(
                "abstract_word_limit",
                Severity.HIGH,
                "Abstract",
                f"The abstract contains {count} words; the required range is {minimum}-{maximum}.",
                "Write a self-contained problem-method-results-conclusion abstract within the range.",
                IssueDisposition.AUTO_FIX,
            )
        )
    if cited_reference_ids(abstract):
        issues.append(
            _issue(
                "abstract_contains_citation",
                Severity.HIGH,
                "Abstract",
                "The abstract contains citation markers, contrary to the journal profile.",
                "Remove citations and keep only supported study statements.",
                IssueDisposition.AUTO_FIX,
            )
        )
    return issues


def _validate_keywords(sections: dict[str, str], context: ValidationContext) -> list[ReviewIssue]:
    body = _find_section(sections, "keywords")
    if body is None:
        return []
    values = [item.strip(" .") for item in re.split(r"[,;\n]+", body) if item.strip(" .")]
    minimum = context.publication_profile.keyword_min if context.publication_profile else 4
    maximum = context.publication_profile.keyword_max if context.publication_profile else 8
    if minimum <= len(values) <= maximum:
        return []
    return [
        _issue(
            "keyword_count",
            Severity.HIGH,
            "Keywords",
            f"The manuscript contains {len(values)} keywords; the required range is {minimum}-{maximum}.",
            "Retain only specific indexing terms and remove raw search-query vocabulary.",
            IssueDisposition.AUTO_FIX,
        )
    ]


def _validate_internal_markers(manuscript: str) -> list[ReviewIssue]:
    markers = re.findall(
        r"(?i)(REQUIRED\[[^\]]*\]|\[(?:citation needed|source required)\]|\bTBD\b|"
        r"\bTODO\b|PAPERFORGE:BEGIN|PAPERFORGE:END|<requested Markdown>)",
        manuscript,
    )
    if not markers:
        return []
    return [
        _issue(
            "unresolved_placeholder",
            Severity.BLOCKING,
            "Manuscript",
            f"The manuscript contains {len(markers)} unresolved internal marker(s).",
            "Resolve the text from evidence or state the limitation in publishable prose.",
            IssueDisposition.INTEGRITY_BLOCKER,
        )
    ]


def _validate_citation_integrity(
    manuscript: str,
    context: ValidationContext,
    *,
    coverage: bool,
) -> list[ReviewIssue]:
    issues: list[ReviewIssue] = []
    invalid = invalid_reference_markers(manuscript)
    if invalid:
        issues.append(
            _issue(
                "invalid_citation_syntax",
                Severity.BLOCKING,
                "Manuscript",
                f"Citation marker(s) are not canonical bracketed REF blocks: {', '.join(invalid)}.",
                "Use [@REF001] or semicolon-separated [@REF001; @REF002].",
                IssueDisposition.INTEGRITY_BLOCKER,
            )
        )
    unknown = unknown_reference_ids(manuscript, context.references)
    if unknown:
        issues.append(
            _issue(
                "unknown_citation",
                Severity.BLOCKING,
                "Manuscript",
                f"Unknown citation marker(s): {', '.join(unknown)}.",
                "Use only verified references in the catalogue.",
                IssueDisposition.INTEGRITY_BLOCKER,
            )
        )
    cited = cited_reference_ids(manuscript)
    verified = {reference.id for reference in context.references if reference.verified}
    unverified = sorted(set(cited) - verified)
    if context.config.quality.require_verified_citations and unverified:
        issues.append(
            _issue(
                "unverified_citation",
                Severity.BLOCKING,
                "Manuscript",
                f"Cited source(s) lack external metadata verification: {', '.join(unverified)}.",
                "Verify or remove the affected sources.",
                IssueDisposition.INTEGRITY_BLOCKER,
            )
        )
    if not coverage:
        return issues
    required_sources = max(
        context.config.quality.minimum_verified_sources,
        context.publication_profile.reference_min if context.publication_profile else 0,
    )
    if len(verified) < required_sources:
        issues.append(
            _issue(
                "insufficient_verified_sources",
                Severity.HIGH,
                "Related Work",
                f"Only {len(verified)} verified sources are available; {required_sources} are required.",
                "Expand the reproducible search or supply relevant scholarly sources.",
                IssueDisposition.AUTHOR_ACTION,
                requires_new_evidence=True,
            )
        )
    abstract_fraction = (
        sum(bool(reference.abstract) for reference in context.references) / len(context.references)
        if context.references
        else 0.0
    )
    if abstract_fraction < context.config.literature.require_abstract_fraction:
        issues.append(
            _issue(
                "insufficient_source_content",
                Severity.HIGH,
                "Related Work",
                f"Only {abstract_fraction:.0%} of selected sources expose abstracts; the configured minimum is "
                f"{context.config.literature.require_abstract_fraction:.0%}.",
                "Supply accessible source text or expand the search before source-level claims.",
                IssueDisposition.AUTHOR_ACTION,
                requires_new_evidence=True,
            )
        )
    minimum_cited = max(
        context.config.quality.minimum_cited_sources,
        context.publication_profile.reference_min if context.publication_profile else 0,
    )
    if len(cited) < minimum_cited:
        issues.append(
            _issue(
                "insufficient_citation_coverage",
                Severity.HIGH,
                "Related Work",
                f"Only {len(cited)} unique sources are cited; {minimum_cited} are required.",
                "Strengthen the synthesis using relevant verified catalogue entries.",
                IssueDisposition.AUTO_FIX,
            )
        )
    return issues


def _validate_numeric_claims(manuscript: str, context: ValidationContext) -> list[ReviewIssue]:
    issues: list[ReviewIssue] = []
    evidence_atoms = _evidence_numeric_atoms(context.evidence)
    seen: set[tuple[str, str]] = set()
    for claim in _claim_units(manuscript):
        source_atoms = _reference_numeric_atoms(cited_reference_ids(claim), context.references)
        for atom in _numeric_atoms(claim):
            canonical = _canonical_numeric_atom(atom)
            if canonical in evidence_atoms or canonical in source_atoms:
                continue
            key = (canonical, claim[:160])
            if key in seen:
                continue
            seen.add(key)
            issues.append(
                _issue(
                    "unsupported_numeric_claim",
                    Severity.BLOCKING,
                    "Manuscript",
                    f"Numeric content is not traceable to study evidence or the cited source: {atom}.",
                    "Remove, qualify, or support the number with registered evidence/source text.",
                    IssueDisposition.INTEGRITY_BLOCKER,
                )
            )
    return issues


def _validate_literature_grounding(
    sections: dict[str, str], context: ValidationContext
) -> list[ReviewIssue]:
    body = _find_section(sections, "related work")
    if body is None:
        return []
    paragraphs = [item for item in re.split(r"\n\s*\n", body) if _word_count(item) >= 35]
    uncited = [item for item in paragraphs if not cited_reference_ids(item)]
    issues: list[ReviewIssue] = []
    minimum_sources = min(12, max(3, context.config.quality.minimum_cited_sources // 2))
    cited = cited_reference_ids(body)
    if len(cited) < minimum_sources:
        issues.append(
            _issue(
                "related_work_citation_coverage",
                Severity.HIGH,
                "Related Work",
                f"The Related Work section cites {len(cited)} unique sources; at least {minimum_sources} are expected.",
                "Synthesize additional relevant verified sources by theme and methodological contrast.",
                IssueDisposition.AUTO_FIX,
            )
        )
    if paragraphs and len(uncited) / len(paragraphs) > 0.25:
        issues.append(
            _issue(
                "uncited_literature_paragraphs",
                Severity.HIGH,
                "Related Work",
                f"{len(uncited)} of {len(paragraphs)} substantive literature paragraphs lack citations.",
                "Ground source-dependent context and comparison in the verified catalogue.",
                IssueDisposition.AUTO_FIX,
            )
        )
    return issues


def _validate_method_disclosure(
    sections: dict[str, str], context: ValidationContext
) -> list[ReviewIssue]:
    paper_type = context.plan.paper_type
    heading = "review methodology" if paper_type == PaperType.REVIEW_ARTICLE else "methodology"
    body = _find_section(sections, heading)
    if body is None:
        return []
    issues: list[ReviewIssue] = []
    if paper_type == PaperType.REVIEW_ARTICLE:
        required_terms = {
            "search sources and queries": ("search", "query", "database", "catalogue"),
            "selection criteria": ("selection", "include", "exclude", "eligib"),
            "scope and date boundary": ("scope", "date", "period"),
            "deduplication/appraisal": ("duplicate", "deduplic", "appraisal", "quality"),
        }
    else:
        required_terms = {
            "study design and setting": ("design", "experiment", "setting", "site"),
            "materials and equipment": ("equipment", "hardware", "sensor", "material", "apparatus"),
            "procedure and algorithm": (
                "procedure",
                "pipeline",
                "algorithm",
                "threshold",
                "processing",
            ),
            "sampling and ground truth": (
                "sample",
                "dataset",
                "label",
                "ground-truth",
                "ground truth",
            ),
            "evaluation and statistics": (
                "evaluation",
                "metric",
                "statistic",
                "accuracy",
                "validation",
            ),
            "validity boundary": ("limitation", "constraint", "validity", "not reported"),
        }
    folded = body.casefold()
    for label, alternatives in required_terms.items():
        if not any(term in folded for term in alternatives):
            issues.append(
                _issue(
                    "method_component_missing",
                    Severity.HIGH,
                    "Review Methodology"
                    if paper_type == PaperType.REVIEW_ARTICLE
                    else "Methodology",
                    f"The method does not clearly report {label}.",
                    "Add only the procedure actually supported by registered evidence.",
                    IssueDisposition.AUTO_FIX,
                )
            )
    return issues


def _validate_results_alignment(
    sections: dict[str, str], context: ValidationContext
) -> list[ReviewIssue]:
    if context.plan.paper_type == PaperType.REVIEW_ARTICLE:
        body = _find_section(sections, "thematic synthesis") or _find_section(sections, "results")
        if body is not None and not cited_reference_ids(body):
            return [
                _issue(
                    "review_findings_uncited",
                    Severity.HIGH,
                    "Thematic Synthesis",
                    "The review findings do not cite the verified literature catalogue.",
                    "Ground each theme in relevant verified sources.",
                    IssueDisposition.AUTO_FIX,
                )
            ]
        return []
    body = _find_section(sections, "results")
    if body is None:
        return []
    issues: list[ReviewIssue] = []
    if re.search(r"\b(accuracy|precision|recall|specificity|f1|mcc)\b", body, re.IGNORECASE):
        if not re.search(
            r"\b(TP|TN|FP|FN|true positive|false positive|denominator|confidence interval)\b",
            body,
            re.IGNORECASE,
        ):
            issues.append(
                _issue(
                    "metric_denominator_missing",
                    Severity.HIGH,
                    "Results",
                    "Performance metrics are reported without raw outcomes, denominators, or uncertainty.",
                    "Report supplied confusion counts/intervals, or identify their absence as a validity limitation.",
                    IssueDisposition.AUTHOR_ACTION,
                    requires_new_evidence=True,
                )
            )
    evidence_text = " ".join(item.content for item in context.evidence).casefold()
    if "simulation" in body.casefold() and "not simulation" in evidence_text:
        issues.append(
            _issue(
                "factual_contradiction",
                Severity.BLOCKING,
                "Results",
                "The Results section presents simulation despite evidence stating the study was not simulated.",
                "Remove the contradictory claim and report the actual validation design.",
                IssueDisposition.INTEGRITY_BLOCKER,
            )
        )
    return issues


def _validate_tables(manuscript: str, context: ValidationContext) -> list[ReviewIssue]:
    if context.plan.paper_type != PaperType.ORIGINAL_RESEARCH:
        return []
    table_count = len(re.findall(r"(?m)^\|(?:[^\n|]+\|)+\s*$\n^\|\s*:?-{3,}", manuscript))
    required = context.config.quality.minimum_tables_for_original_research
    if table_count >= required:
        return []
    return [
        _issue(
            "results_table_missing",
            Severity.HIGH,
            "Results",
            f"The original-research manuscript contains {table_count} Markdown table(s); {required} are required.",
            "Create a self-contained table from supported system, dataset, or performance evidence.",
            IssueDisposition.AUTO_FIX,
        )
    ]


def _validate_discussion(sections: dict[str, str], context: ValidationContext) -> list[ReviewIssue]:
    discussion = _find_section(sections, "discussion")
    if discussion is None:
        return []
    issues: list[ReviewIssue] = []
    folded = discussion.casefold()
    if context.plan.paper_type == PaperType.ORIGINAL_RESEARCH:
        results = _find_section(sections, "results") or ""
        if (
            results
            and SequenceMatcher(
                None, _normalize_prose(results), _normalize_prose(discussion)
            ).ratio()
            > 0.72
        ):
            issues.append(
                _issue(
                    "discussion_repeats_results",
                    Severity.HIGH,
                    "Discussion",
                    "The Discussion substantially repeats Results instead of interpreting them.",
                    "Analyze mechanisms, uncertainty, generalisability, practical meaning, and alternatives.",
                    IssueDisposition.AUTO_FIX,
                )
            )
        required_concepts = {
            "comparison with literature": ("compared", "prior work", "literature", "reported by"),
            "generalisability or validity": ("general", "validity", "transfer", "controlled"),
            "limitations or uncertainty": (
                "limitation",
                "uncertainty",
                "not available",
                "not reported",
            ),
            "engineering implications": ("implication", "deployment", "practical", "engineering"),
        }
        for label, alternatives in required_concepts.items():
            if not any(term in folded for term in alternatives):
                issues.append(
                    _issue(
                        "discussion_dimension_missing",
                        Severity.HIGH,
                        "Discussion",
                        f"The Discussion does not address {label}.",
                        "Add an evidence-bounded interpretation without repeating results.",
                        IssueDisposition.AUTO_FIX,
                    )
                )
    return issues


def _validate_declarations(
    sections: dict[str, str], context: ValidationContext
) -> list[ReviewIssue]:
    required = (
        context.publication_profile.required_declarations
        if context.publication_profile
        else context.config.journal.required_declarations
    )
    issues: list[ReviewIssue] = []
    for heading in required:
        body = _find_section(sections, normalize_heading(heading))
        if body is not None:
            issues.extend(_validate_declaration(heading, body, context))
    return issues


def _validate_publication_contract(context: ValidationContext) -> list[ReviewIssue]:
    profile = context.publication_profile
    if profile is None or profile.target_rules_verified:
        return []
    journal = profile.journal_name or "the intended target journal"
    return [
        _issue(
            "target_journal_rules_unverified",
            Severity.HIGH,
            "Submission",
            f"A checked, journal-specific publication contract is not available for {journal}.",
            (
                "Select a supported target journal or verify its current author instructions, "
                "article type, template, length, declarations, and submission-file requirements."
            ),
            IssueDisposition.AUTHOR_ACTION,
            requires_new_evidence=True,
        )
    ]


def _validate_declaration(
    heading: str,
    body: str,
    context: ValidationContext,
) -> list[ReviewIssue]:
    normalized = normalize_heading(heading)
    topic_terms = DECLARATION_TOPICS.get(normalized)
    if not topic_terms:
        return []
    evidence_text = "\n".join(item.content for item in context.evidence).casefold()
    has_support = any(term in evidence_text for term in topic_terms)
    transparent = any(
        term in body.casefold()
        for term in (
            "not supplied",
            "not provided",
            "must verify",
            "must be verified",
            "author verification",
            "requires verification",
            "not yet declared",
        )
    )
    if has_support:
        return []
    if transparent:
        return [
            _issue(
                "declaration_requires_author_verification",
                Severity.HIGH,
                heading,
                f"The {heading} statement is transparently unresolved.",
                f"Supply and approve the final {heading} statement before submission.",
                IssueDisposition.AUTHOR_ACTION,
                requires_new_evidence=True,
            )
        ]
    return [
        _issue(
            "unsupported_declaration",
            Severity.BLOCKING,
            heading,
            f"The manuscript asserts a {heading} statement without registered author evidence.",
            "Remove the assertion or supply an author-approved declaration.",
            IssueDisposition.INTEGRITY_BLOCKER,
            requires_new_evidence=True,
        )
    ]


def _validate_section_depth(
    heading: str,
    body: str,
    context: ValidationContext,
) -> list[ReviewIssue]:
    minimum = _section_minimum(context, heading)
    count = _word_count(body)
    if count >= minimum:
        return []
    return [
        _issue(
            "section_too_short",
            Severity.BLOCKING,
            heading,
            f"Section '{heading}' has {count} words; at least {minimum} are required.",
            "Develop the assigned evidence and source synthesis to the required depth.",
            IssueDisposition.INTEGRITY_BLOCKER,
        )
    ]


def _section_minimum(context: ValidationContext, heading: str) -> int:
    if not context.config.quality.require_section_depth or not context.publication_profile:
        return minimum_section_words(heading)
    return section_minimum(context.publication_profile, heading)


def _minimum_manuscript_words(context: ValidationContext) -> int:
    if context.publication_profile:
        return max(
            context.config.quality.minimum_manuscript_words,
            context.publication_profile.word_min,
        )
    return context.config.quality.minimum_manuscript_words


def _find_section(sections: dict[str, str], required: str) -> str | None:
    required = normalize_heading(required)
    alternatives = SECTION_ALIASES.get(required, {required})
    for heading, body in sections.items():
        normalized = normalize_heading(heading)
        if normalized == required or normalized in alternatives or required in normalized:
            return body
    return None


def _find_heading_key(sections: dict[str, str], required: str) -> str | None:
    alternatives = SECTION_ALIASES.get(required, {required})
    for heading in sections:
        if heading == required or heading in alternatives or required in heading:
            return heading
    return None


def _canonical_section_heading(heading: str) -> str:
    for canonical, aliases in SECTION_ALIASES.items():
        if heading == canonical or heading in aliases:
            return canonical
    return heading


def _paragraphs(manuscript: str) -> list[str]:
    without_references = strip_reference_section(manuscript)
    return [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", without_references)
        if paragraph.strip() and not paragraph.lstrip().startswith("#")
    ]


def _numeric_atoms(text: str) -> list[str]:
    cleaned = re.sub(r"\[@REF\d{3,}(?:\s*;\s*@REF\d{3,})*\]", "", text)
    cleaned = re.sub(r"@?REF\d{3,}", "", cleaned)
    atoms = re.findall(
        r"(?<![A-Za-z])(?:[+\-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
        r"(?:\s*[×x]\s*[+\-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)?"
        r"(?:\s*(?:%|ms|s|Hz|kHz|MHz|GHz|fps|px|µm|μm|km/h|cm|mm|km|m|°C|K|V|A|W|"
        r"kg|g|MB|GB|kB|dB|dpi))?)",
        cleaned,
    )
    return [" ".join(atom.split()) for atom in atoms]


def _claim_units(manuscript: str) -> list[str]:
    units: list[str] = []
    for paragraph in _paragraphs(manuscript):
        units.extend(
            item.strip()
            for item in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9|])", paragraph)
            if item.strip()
        )
    return units


def _evidence_numeric_atoms(evidence: list[EvidenceItem]) -> set[str]:
    return {
        _canonical_numeric_atom(atom) for item in evidence for atom in _numeric_atoms(item.content)
    }


def _reference_numeric_atoms(
    reference_ids: list[str],
    references: list[ReferenceRecord],
) -> set[str]:
    selected = {item.id: item for item in references}
    atoms: set[str] = set()
    for reference_id in reference_ids:
        reference = selected.get(reference_id)
        if reference is None:
            continue
        source_text = " ".join(
            value
            for value in (
                reference.title,
                reference.abstract or "",
                str(reference.year or ""),
                reference.volume or "",
                reference.issue or "",
                reference.pages or "",
            )
            if value
        )
        atoms.update(_canonical_numeric_atom(atom) for atom in _numeric_atoms(source_text))
    return atoms


def _canonical_numeric_atom(atom: str) -> str:
    clean = atom.replace("−", "-").replace("×", "x").replace(",", "")
    clean = "".join(clean.split()).casefold()

    def normalize_number(match: re.Match[str]) -> str:
        try:
            value = Decimal(match.group())
        except InvalidOperation:
            return match.group()
        normalized = format(value, "f")
        if "." in normalized:
            normalized = normalized.rstrip("0").rstrip(".")
        return normalized or "0"

    return re.sub(r"[+\-]?\d+(?:\.\d+)?", normalize_number, clean)


def _normalize_prose(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text))


def _issue(
    code: str,
    severity: Severity,
    section: str | None,
    description: str,
    required_change: str,
    disposition: IssueDisposition,
    *,
    requires_new_evidence: bool = False,
) -> ReviewIssue:
    digest = hashlib.sha1(f"{code}|{section or ''}|{description}".encode()).hexdigest()[:10].upper()
    return ReviewIssue(
        id=f"ISS-{digest}",
        code=code,
        severity=severity,
        section=section,
        description=description,
        required_change=required_change,
        requires_new_evidence=requires_new_evidence,
        disposition=disposition,
    )
