from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from paperforge.citations import (
    cited_reference_ids,
    invalid_reference_markers,
    unknown_reference_ids,
)
from paperforge.config import AppConfig
from paperforge.domain import (
    EvidenceItem,
    IssueDisposition,
    PaperType,
    ReferenceRecord,
    ResearchPlan,
    ReviewIssue,
    Severity,
)

INTEGRITY_CODES = {
    "fabricated_citation",
    "invalid_citation_syntax",
    "unknown_citation",
    "unverified_citation",
    "unsupported_numeric_claim",
    "unsupported_result",
    "factual_contradiction",
    "contradictory_result",
    "missing_required_section",
    "empty_required_section",
    "unresolved_placeholder",
    "manuscript_truncated",
    "related_work_empty",
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
    "methodology": {"methodology", "materials and methods", "methods", "review methodology"},
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


@dataclass(slots=True)
class ValidationContext:
    config: AppConfig
    plan: ResearchPlan
    evidence: list[EvidenceItem]
    references: list[ReferenceRecord]


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
                "factual contradiction",
                "contradictory result",
                "empty required section",
                "unresolved placeholder",
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


def validate_manuscript(
    manuscript: str,
    context: ValidationContext,
    *,
    review_type: str,
) -> list[ReviewIssue]:
    issues: list[ReviewIssue] = []
    sections = markdown_sections(manuscript)
    normalized_headings = {_normalize_heading(heading): body for heading, body in sections.items()}

    if review_type in {"writing_review", "journal_review", "final_review"}:
        issues.extend(_validate_required_sections(normalized_headings, context.plan))
        issues.extend(
            _validate_abstract(normalized_headings, context.config.journal.abstract_max_words)
        )
        issues.extend(_validate_internal_markers(manuscript))

    if review_type in {"evidence_review", "final_review"}:
        issues.extend(_validate_citations(manuscript, context))
        if context.config.quality.require_supported_numeric_claims:
            issues.extend(_validate_numeric_claims(manuscript, context.evidence))

    if review_type in {"methodology_review", "final_review"}:
        issues.extend(_validate_method_disclosure(normalized_headings, context.plan.paper_type))

    if review_type in {"results_review", "final_review"}:
        issues.extend(_validate_results_alignment(normalized_headings, context.plan.paper_type))

    if review_type in {"writing_review", "final_review"}:
        word_count = _word_count(manuscript)
        if word_count < context.config.quality.minimum_manuscript_words:
            issues.append(
                _issue(
                    "manuscript_too_short",
                    Severity.HIGH,
                    "Manuscript",
                    f"The manuscript contains {word_count} words; the configured minimum is "
                    f"{context.config.quality.minimum_manuscript_words}.",
                    "Develop the evidence-supported analysis and discussion without adding filler.",
                    IssueDisposition.AUTO_FIX,
                )
            )
    return IssuePolicy(context).normalize_all(issues)


def validate_revision(
    previous: str,
    candidate: str,
    context: ValidationContext,
) -> list[ReviewIssue]:
    issues: list[ReviewIssue] = []
    previous_words = _word_count(previous)
    candidate_words = _word_count(candidate)
    if previous_words and candidate_words < previous_words * 0.7:
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
    unknown = unknown_reference_ids(candidate, context.references)
    if unknown:
        issues.append(
            _issue(
                "unknown_citation",
                Severity.BLOCKING,
                "Manuscript",
                f"The revision introduced unknown citation marker(s): {', '.join(unknown)}.",
                "Use only citation IDs in the verified reference catalogue.",
                IssueDisposition.INTEGRITY_BLOCKER,
            )
        )
    if context.config.quality.require_verified_citations:
        known = {reference.id for reference in context.references}
        verified = {reference.id for reference in context.references if reference.verified}
        unverified = sorted((set(cited_reference_ids(candidate)) & known) - verified)
        if unverified:
            issues.append(
                _issue(
                    "unverified_citation",
                    Severity.BLOCKING,
                    "Manuscript",
                    "The revision cites source(s) without external metadata verification: "
                    + ", ".join(unverified)
                    + ".",
                    "Verify or remove the affected sources.",
                    IssueDisposition.INTEGRITY_BLOCKER,
                )
            )
    evidence_atoms = _evidence_numeric_atoms(context.evidence)
    previous_atoms = {_canonical_numeric_atom(atom) for atom in _numeric_atoms(previous)}
    for claim in _claim_units(candidate):
        if cited_reference_ids(claim):
            continue
        for atom in _numeric_atoms(claim):
            canonical = _canonical_numeric_atom(atom)
            if canonical in previous_atoms or canonical in evidence_atoms:
                continue
            issues.append(
                _issue(
                    "unsupported_numeric_claim",
                    Severity.BLOCKING,
                    "Manuscript",
                    f"The revision introduced numeric content not found in supplied evidence: {atom}.",
                    "Remove the number or support it with supplied evidence or a verified citation.",
                    IssueDisposition.INTEGRITY_BLOCKER,
                )
            )
    issues.extend(_validate_internal_markers(candidate))
    invalid = invalid_reference_markers(candidate)
    if invalid:
        issues.append(
            _issue(
                "invalid_citation_syntax",
                Severity.BLOCKING,
                "Manuscript",
                f"Citation marker(s) are not in canonical [@REF001] form: {', '.join(invalid)}.",
                "Use canonical citation blocks with semicolon-separated REF identifiers.",
                IssueDisposition.INTEGRITY_BLOCKER,
            )
        )
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
    # The model's self-score is diagnostic only. Readiness is derived from normalized
    # issues so an arbitrary score cannot overrule the deterministic scope policy.
    del model_score
    return deterministic


def has_integrity_blocker(issues: list[ReviewIssue]) -> bool:
    return any(
        not issue.resolved and issue.disposition == IssueDisposition.INTEGRITY_BLOCKER
        for issue in issues
    )


def markdown_sections(manuscript: str) -> dict[str, str]:
    matches = list(re.finditer(r"(?m)^#{1,3}\s+(.+?)\s*$", manuscript))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(manuscript)
        sections[match.group(1).strip()] = manuscript[match.end() : end].strip()
    return sections


def _validate_required_sections(sections: dict[str, str], plan: ResearchPlan) -> list[ReviewIssue]:
    issues: list[ReviewIssue] = []
    for required in plan.required_sections:
        normalized = _normalize_heading(required)
        body = _find_section(sections, normalized)
        if body is None:
            issues.append(
                _issue(
                    "missing_required_section",
                    Severity.BLOCKING,
                    required,
                    f"Required section '{required}' is missing.",
                    "Create a substantive evidence-grounded section.",
                    IssueDisposition.INTEGRITY_BLOCKER,
                )
            )
        elif _word_count(body) < minimum_section_words(normalized):
            code = (
                "related_work_empty"
                if "related" in normalized or "literature" in normalized
                else "empty_required_section"
            )
            issues.append(
                _issue(
                    code,
                    Severity.BLOCKING,
                    required,
                    f"Required section '{required}' is empty or too short to be substantive.",
                    "Develop the section using supplied evidence and verified references.",
                    IssueDisposition.INTEGRITY_BLOCKER,
                )
            )
    return issues


def _validate_abstract(sections: dict[str, str], limit: int) -> list[ReviewIssue]:
    abstract = _find_section(sections, "abstract")
    if abstract is None:
        return []
    count = _word_count(abstract)
    if count <= limit:
        return []
    return [
        _issue(
            "abstract_word_limit",
            Severity.HIGH,
            "Abstract",
            f"The abstract contains {count} words; the configured maximum is {limit}.",
            "Shorten it while preserving the verified problem, method, principal findings, and conclusion.",
            IssueDisposition.AUTO_FIX,
        )
    ]


def _validate_internal_markers(manuscript: str) -> list[ReviewIssue]:
    markers = re.findall(
        r"(?i)(REQUIRED\[[^\]]*\]|\[(?:citation needed|source required)\]|\bTBD\b|"
        r"\bTODO\b|PAPERFORGE:BEGIN|PAPERFORGE:END)",
        manuscript,
    )
    if not markers:
        return []
    return [
        _issue(
            "unresolved_placeholder",
            Severity.BLOCKING,
            "Manuscript",
            f"The manuscript contains {len(markers)} unresolved internal placeholder(s).",
            "Resolve the text from evidence or state the limitation in publishable prose.",
            IssueDisposition.INTEGRITY_BLOCKER,
        )
    ]


def _validate_citations(manuscript: str, context: ValidationContext) -> list[ReviewIssue]:
    issues: list[ReviewIssue] = []
    invalid = invalid_reference_markers(manuscript)
    if invalid:
        issues.append(
            _issue(
                "invalid_citation_syntax",
                Severity.BLOCKING,
                "Manuscript",
                f"Citation marker(s) are not in canonical [@REF001] form: {', '.join(invalid)}.",
                "Use canonical citation blocks with semicolon-separated REF identifiers.",
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
    if len(verified) < context.config.quality.minimum_verified_sources:
        issues.append(
            _issue(
                "insufficient_verified_sources",
                Severity.HIGH,
                "Related Work",
                f"Only {len(verified)} verified sources are available; "
                f"{context.config.quality.minimum_verified_sources} are configured.",
                "Expand the reproducible literature search or supply relevant scholarly sources.",
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
                f"Only {abstract_fraction:.0%} of the selected sources expose abstracts; the "
                f"configured minimum is {context.config.literature.require_abstract_fraction:.0%}.",
                "Supply accessible source text or expand the search before relying on source-level claims.",
                IssueDisposition.AUTHOR_ACTION,
                requires_new_evidence=True,
            )
        )
    if len(cited) < context.config.quality.minimum_cited_sources:
        issues.append(
            _issue(
                "insufficient_citation_coverage",
                Severity.HIGH,
                "Related Work",
                f"Only {len(cited)} unique sources are cited; "
                f"{context.config.quality.minimum_cited_sources} are configured.",
                "Strengthen the synthesis using relevant verified catalogue entries.",
                IssueDisposition.AUTO_FIX,
            )
        )
    sections = {
        _normalize_heading(heading): body for heading, body in markdown_sections(manuscript).items()
    }
    related_work = _find_section(sections, "related work")
    minimum_related = min(3, max(1, context.config.quality.minimum_cited_sources))
    related_citations = cited_reference_ids(related_work or "")
    if related_work is not None and len(related_citations) < minimum_related:
        issues.append(
            _issue(
                "related_work_citation_coverage",
                Severity.HIGH,
                "Related Work",
                f"The Related Work section cites {len(related_citations)} unique source(s); "
                f"at least {minimum_related} are required for a minimally grounded synthesis.",
                "Synthesize additional relevant verified sources in the Related Work section.",
                IssueDisposition.AUTO_FIX,
            )
        )
    return issues


def _validate_numeric_claims(manuscript: str, evidence: list[EvidenceItem]) -> list[ReviewIssue]:
    issues: list[ReviewIssue] = []
    evidence_atoms = _evidence_numeric_atoms(evidence)
    for claim in _claim_units(manuscript):
        if cited_reference_ids(claim):
            continue
        for atom in _numeric_atoms(claim):
            if _canonical_numeric_atom(atom) in evidence_atoms:
                continue
            issues.append(
                _issue(
                    "unsupported_numeric_claim",
                    Severity.BLOCKING,
                    "Manuscript",
                    f"Numeric content is not traceable to user/computed evidence: {atom}.",
                    "Remove, qualify, or support the number with registered evidence.",
                    IssueDisposition.INTEGRITY_BLOCKER,
                )
            )
    return issues


def _validate_method_disclosure(
    sections: dict[str, str], paper_type: PaperType
) -> list[ReviewIssue]:
    heading = "review methodology" if paper_type == PaperType.REVIEW_ARTICLE else "methodology"
    body = _find_section(sections, heading)
    if body is None:
        return []
    issues: list[ReviewIssue] = []
    if paper_type == PaperType.REVIEW_ARTICLE:
        for term in ("search", "selection", "scope"):
            if term not in body.casefold():
                issues.append(
                    _issue(
                        f"review_method_missing_{term}",
                        Severity.HIGH,
                        "Review Methodology",
                        f"The review method does not clearly describe {term}.",
                        "Describe the reproducible search and selection boundary actually used.",
                        IssueDisposition.AUTO_FIX,
                    )
                )
    elif not any(term in body.casefold() for term in ("limitation", "constraint", "validity")):
        issues.append(
            _issue(
                "method_validity_boundary_missing",
                Severity.HIGH,
                "Methodology",
                "The method does not state its supported validity boundary.",
                "Add the supplied constraints and clearly distinguish unavailable details.",
                IssueDisposition.AUTO_FIX,
            )
        )
    return issues


def _validate_results_alignment(
    sections: dict[str, str], paper_type: PaperType
) -> list[ReviewIssue]:
    if paper_type == PaperType.REVIEW_ARTICLE:
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


def _find_section(sections: dict[str, str], required: str) -> str | None:
    alternatives = SECTION_ALIASES.get(required, {required})
    for heading, body in sections.items():
        if heading == required or heading in alternatives or required in heading:
            return body
    return None


def minimum_section_words(heading: str) -> int:
    if "keyword" in heading:
        return 3
    if any(
        term in heading
        for term in (
            "data availability",
            "conflict of interest",
            "funding",
            "author contribution",
            "ethics",
        )
    ):
        return 5
    if heading == "abstract":
        return 80
    return 30


def _normalize_heading(value: str) -> str:
    clean = re.sub(r"^\d+(?:\.\d+)*[.)]?\s*", "", value.casefold())
    return re.sub(r"[^a-z0-9]+", " ", clean).strip()


def _paragraphs(manuscript: str) -> list[str]:
    without_references = re.split(r"(?im)^#{1,3}\s+references\s*$", manuscript, maxsplit=1)[0]
    return [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", without_references)
        if paragraph.strip() and not paragraph.lstrip().startswith("#")
    ]


def _numeric_atoms(text: str) -> list[str]:
    cleaned = re.sub(r"\[@REF\d{3,}(?:\s*;\s*@REF\d{3,})*\]", "", text)
    atoms = re.findall(
        r"(?<![A-Za-z])(?:[+\-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
        r"(?:\s*[×x]\s*[+\-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)?"
        r"(?:\s*(?:%|ms|s|Hz|kHz|MHz|GHz|fps|px|µm|μm|km/h|cm|mm|km|m|°C|K|V|A|W|"
        r"kg|g|MB|GB|dB))?)",
        cleaned,
    )
    return [" ".join(atom.split()) for atom in atoms]


def _claim_units(manuscript: str) -> list[str]:
    units: list[str] = []
    for paragraph in _paragraphs(manuscript):
        units.extend(
            item.strip()
            for item in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", paragraph)
            if item.strip()
        )
    return units


def _evidence_numeric_atoms(evidence: list[EvidenceItem]) -> set[str]:
    return {
        _canonical_numeric_atom(atom) for item in evidence for atom in _numeric_atoms(item.content)
    }


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
