from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml

from paperforge.domain import (
    AuthorValidationDecision,
    AuthorValidationItem,
    AuthorValidationPackage,
    ClaimLedger,
    EvidenceCoverage,
    EvidenceCoverageStatus,
    EvidenceItem,
    EvidenceKind,
    LiteratureSynthesis,
    PaperType,
    ReferenceRecord,
    RequirementLevel,
)

VALIDATION_RELATIVE_PATH = "author-actions/validation.yaml"

_STOPWORDS = {
    "a",
    "all",
    "and",
    "as",
    "be",
    "by",
    "for",
    "from",
    "give",
    "in",
    "is",
    "of",
    "on",
    "or",
    "report",
    "reported",
    "state",
    "study",
    "the",
    "to",
    "used",
    "with",
}

_RELEVANCE = {
    "system_specification": (
        "Readers must be able to identify the implemented platform and distinguish tested "
        "components from a conceptual architecture."
    ),
    "algorithm_parameters": (
        "Decision rules and parameter values determine reproducibility and may materially change "
        "detection behaviour."
    ),
    "acquisition_protocol": (
        "Sampling conditions define the independent experimental units, operating envelope, and "
        "generalisability of the reported results."
    ),
    "ground_truth_protocol": (
        "Performance metrics are interpretable only when the reference labels and their quality "
        "controls are explicit."
    ),
    "evaluation_independence": (
        "A defensible evaluation must separate parameter selection from testing and prevent closely "
        "related observations from leaking across data partitions."
    ),
    "statistical_support": (
        "Raw outcomes and uncertainty show the precision of the reported metrics and avoid treating "
        "correlated frames as independent evidence."
    ),
    "reported_results": (
        "The main findings require an exact denominator, metric definition, and test-set boundary."
    ),
    "calibration": (
        "Instrument-derived measurements depend on sensor validity, environmental assumptions, "
        "traceability, and any required synchronisation."
    ),
    "external_comparison": (
        "Prior work can contextualise the contribution, but percentages from different datasets "
        "or protocols cannot be ranked as if they were directly comparable."
    ),
    "data_code_availability": (
        "A precise availability statement lets reviewers assess reproducibility without inventing "
        "a public repository or access route."
    ),
    "permissions_safety": (
        "Experimental activities must accurately disclose the approvals, permissions, and safety "
        "controls applicable to the reported work."
    ),
    "submission_declarations": (
        "Funding, conflicts, contributor roles, and AI-use statements are author-controlled "
        "declarations that cannot be inferred from literature."
    ),
}


def build_author_validation_package(
    *,
    topic: str,
    coverage: EvidenceCoverage,
    ledger: ClaimLedger,
    references: list[ReferenceRecord],
    synthesis: LiteratureSynthesis,
    existing: AuthorValidationPackage | None = None,
) -> AuthorValidationPackage:
    """Create one research-informed validation set while preserving author decisions."""
    existing_by_code = {item.requirement_code: item for item in existing.items} if existing else {}
    claims_by_id = {claim.id: claim for claim in ledger.claims}
    notes_by_id = {note.reference_id: note for note in synthesis.source_notes}
    items: list[AuthorValidationItem] = []
    included: set[str] = set()

    for requirement in coverage.requirements:
        if requirement.status == EvidenceCoverageStatus.SUPPORTED:
            continue
        # A topic-only review can derive its scope during planning. Author validation is reserved
        # for facts PaperForge cannot safely obtain from the literature.
        if coverage.paper_type == PaperType.REVIEW_ARTICLE:
            continue
        previous = existing_by_code.get(requirement.code)
        selected = _relevant_references(requirement, references, synthesis)
        known_facts = _known_facts(requirement, claims_by_id)
        literature_context = [
            _literature_note(reference, notes_by_id.get(reference.id)) for reference in selected
        ]
        item = AuthorValidationItem(
            id=_validation_id(requirement.code),
            requirement_code=requirement.code,
            label=requirement.label,
            level=requirement.level,
            evidence_status=requirement.status,
            why_relevant=_RELEVANCE.get(
                requirement.code,
                "This detail affects the interpretation or reproducibility of the reported work.",
            ),
            question=(
                f"For the reported study on ‘{topic}’, what author-verified information is "
                f"available for ‘{requirement.label}’? "
                f"{requirement.requested_detail or requirement.label}"
            ),
            known_facts=known_facts,
            literature_context=literature_context,
            reference_ids=[reference.id for reference in selected],
            manuscript_treatment=_manuscript_treatment(requirement.level),
            decision=previous.decision if previous else AuthorValidationDecision.PENDING,
            answer=previous.answer if previous else "",
        )
        items.append(item)
        included.add(requirement.code)

    # Never discard a completed author response merely because it made the requirement pass on a
    # later run. It remains part of the auditable, user-controlled evidence trail.
    if existing:
        requirements_by_code = {item.code: item for item in coverage.requirements}
        for item in existing.items:
            if item.requirement_code not in included and item.resolved:
                requirement = requirements_by_code.get(item.requirement_code)
                if requirement:
                    selected = _relevant_references(requirement, references, synthesis)
                    items.append(
                        item.model_copy(
                            update={
                                "evidence_status": requirement.status,
                                "known_facts": _known_facts(requirement, claims_by_id),
                                "literature_context": [
                                    _literature_note(reference, notes_by_id.get(reference.id))
                                    for reference in selected
                                ],
                                "reference_ids": [reference.id for reference in selected],
                            }
                        )
                    )
                else:
                    items.append(item)

    return AuthorValidationPackage(
        topic=topic,
        paper_type=coverage.paper_type,
        instructions=(
            "PaperForge does not pause for these items and continues through manuscript drafting, "
            "review, and export. Review this file once after the run. Change only `decision` and "
            "`answer`: use "
            "`provided` or `corrected` with a factual answer, `not_available` when the study record "
            "does not contain the detail, or `not_applicable` with a short explanation. Leave "
            "`pending` if you do not want to decide yet. Rerun PaperForge only when you want those "
            "author decisions incorporated. Literature context is reporting guidance, never proof "
            "that this study used a method."
        ),
        items=items,
    )


def load_author_validation(path: Path) -> AuthorValidationPackage:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid author validation YAML: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("author-actions/validation.yaml must contain a YAML mapping")
    return AuthorValidationPackage.model_validate(payload)


def dump_author_validation(package: AuthorValidationPackage) -> str:
    return yaml.safe_dump(
        package.model_dump(mode="json"),
        sort_keys=False,
        allow_unicode=True,
        width=120,
    )


def author_validation_markdown(package: AuthorValidationPackage) -> str:
    pending = package.pending_items
    lines = [
        "# Author Validation",
        "",
        package.instructions,
        "",
        f"- Topic: {package.topic}",
        f"- Paper type: `{package.paper_type.value}`",
        f"- Validation items: {len(package.items)}",
        f"- Pending decisions: {len(pending)}",
        "",
    ]
    if not package.items:
        lines.append(
            "No study-specific validation question is required by the current evidence map."
        )
        return "\n".join(lines) + "\n"
    for item in package.items:
        lines.extend(
            [
                f"## {item.id}: {item.label}",
                "",
                f"- Decision: `{item.decision.value}`",
                f"- Evidence coverage: `{item.evidence_status.value}`",
                f"- Relevance: {item.why_relevant}",
                f"- Validate: {item.question}",
                f"- Manuscript treatment: {item.manuscript_treatment}",
            ]
        )
        if item.answer:
            lines.append(f"- Author answer: {item.answer}")
        lines.extend(["", "### Facts already supplied", ""])
        lines.extend([f"- {fact}" for fact in item.known_facts] or ["- None located."])
        lines.extend(["", "### Literature context (not study evidence)", ""])
        lines.extend(
            [f"- {context}" for context in item.literature_context]
            or ["- No sufficiently relevant verified source context was located."]
        )
        lines.append("")
    return "\n".join(lines)


def validation_evidence(package: AuthorValidationPackage) -> EvidenceItem | None:
    statements: list[str] = []
    for item in package.items:
        if (
            item.decision
            in {
                AuthorValidationDecision.PROVIDED,
                AuthorValidationDecision.CORRECTED,
            }
            and item.answer
        ):
            statements.append(f"{item.id} ({item.label}): {item.answer}")
        elif item.decision == AuthorValidationDecision.NOT_AVAILABLE:
            statements.append(
                f"{item.id} ({item.label}): The author confirmed that this detail is not "
                "available in the study records."
            )
        elif item.decision == AuthorValidationDecision.NOT_APPLICABLE:
            explanation = f" {item.answer}" if item.answer else ""
            statements.append(
                f"{item.id} ({item.label}): The author confirmed that this requirement is not "
                f"applicable to the reported study.{explanation}"
            )
    if not statements:
        return None
    material = "\n\n".join(statements)
    checksum = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return EvidenceItem(
        id="EV-AUTHOR-VALIDATION",
        kind=EvidenceKind.USER_FACT,
        title="Author validation decisions",
        content=material,
        source_path=VALIDATION_RELATIVE_PATH,
        locator="resolved decision and answer fields",
        checksum=checksum,
        verified=True,
        metadata={
            "auto_ingested": True,
            "author_validation": True,
            "verification": "author_edited_validation_yaml",
            "resolved_items": len(statements),
        },
    )


def validation_user_material_from_payload(payload: Any) -> str:
    """Return only user-controlled decisions/answers for input invalidation."""
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return ""
    material: list[dict[str, str]] = []
    for raw in payload["items"]:
        if not isinstance(raw, dict):
            continue
        identifier = str(raw.get("id") or raw.get("requirement_code") or "").strip()
        decision = str(raw.get("decision") or "pending").strip().casefold()
        answer = str(raw.get("answer") or "").strip()
        if decision == AuthorValidationDecision.PENDING and not answer:
            continue
        material.append({"id": identifier, "decision": decision, "answer": answer})
    if not material:
        return ""
    return json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _validation_id(code: str) -> str:
    clean = re.sub(r"[^A-Z0-9]+", "-", code.upper()).strip("-")
    return f"VAL-{clean}"


def _known_facts(requirement, claims_by_id) -> list[str]:
    query_tokens = _tokens(f"{requirement.label} {requirement.requested_detail or ''}")
    candidates = [
        claims_by_id[claim_id]
        for claim_id in requirement.matched_claim_ids
        if claim_id in claims_by_id
    ]
    ranked = sorted(
        ((len(query_tokens & _tokens(claim.text)), claim) for claim in candidates),
        key=lambda item: item[0],
        reverse=True,
    )
    return [claim.text for score, claim in ranked if score > 0][:6]


def _manuscript_treatment(level: RequirementLevel) -> str:
    if level == RequirementLevel.RECOMMENDED:
        return (
            "PaperForge will use the literature for cautious context and will not imply that this "
            "optional analysis was performed."
        )
    return (
        "Until the author confirms this item, PaperForge will omit unsupported specifics or state "
        "that the detail was not documented; it will not infer the experiment from literature."
    )


def _relevant_references(requirement, references, synthesis) -> list[ReferenceRecord]:
    verified = [
        reference for reference in references if reference.verified and not reference.retracted
    ]
    if not verified:
        return []
    notes = {note.reference_id: note for note in synthesis.source_notes}
    query_tokens = _tokens(f"{requirement.label} {requirement.requested_detail or ''}")
    ranked: list[tuple[int, float, int, ReferenceRecord]] = []
    for reference in verified:
        note = notes.get(reference.id)
        note_text = " ".join(
            [
                note.method if note else "",
                note.dataset_or_material if note else "",
                note.key_finding if note else "",
                note.limitation if note else "",
                note.relevance if note else "",
            ]
        )
        source_tokens = _tokens(f"{reference.title} {reference.abstract or ''} {note_text}")
        overlap = len(query_tokens & source_tokens)
        ranked.append(
            (overlap, float(reference.relevance_score), reference.cited_by_count, reference)
        )
    ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    positive = [item[3] for item in ranked if item[0] > 0]
    return (positive or [item[3] for item in ranked])[:3]


def _literature_note(reference: ReferenceRecord, note) -> str:
    fragments = [f"{reference.id} — {reference.title}"]
    if note:
        if note.method != "Not reported in the available metadata.":
            fragments.append(f"method context: {note.method}")
        if note.limitation != "Not reported in the available metadata.":
            fragments.append(f"reported limitation: {note.limitation}")
    if len(fragments) == 1:
        fragments.append("verified metadata available; detailed method context was not reported")
    return "; ".join(fragments)


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z][a-z0-9-]{2,}", value.casefold())
        if token not in _STOPWORDS
    }
