from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from paperforge.domain import (
    ClaimLedger,
    ClaimRecord,
    EvidenceCoverage,
    EvidenceCoverageStatus,
    EvidenceItem,
    EvidenceKind,
    EvidenceRequirement,
    PaperType,
    RequirementLevel,
)


def build_claim_ledger(evidence: list[EvidenceItem]) -> ClaimLedger:
    """Split supplied study evidence into stable, exact provenance atoms."""
    claims: list[ClaimRecord] = []
    seen: set[tuple[str, str]] = set()
    allowed_kinds = {
        EvidenceKind.USER_FACT,
        EvidenceKind.EXPERIMENTAL_DATA,
        EvidenceKind.COMPUTED,
        EvidenceKind.FIGURE,
    }
    for item in evidence:
        if item.kind not in allowed_kinds:
            continue
        for text in _claim_units(item.content):
            clean = " ".join(text.split()).strip(" -")
            if len(clean) < 8 or clean.casefold().startswith("author-supplied factual responses"):
                continue
            key = (item.id, clean.casefold())
            if key in seen:
                continue
            seen.add(key)
            digest = hashlib.sha256(f"{item.id}|{clean}".encode()).hexdigest()[:12].upper()
            claims.append(
                ClaimRecord(
                    id=f"CLM-{digest}",
                    evidence_id=item.id,
                    text=clean,
                    kind=item.kind,
                    source_path=item.source_path,
                    locator=item.locator,
                    numeric_atoms=_numeric_atoms(clean),
                )
            )
    return ClaimLedger(claims=claims)


def assess_evidence_coverage(
    paper_type: PaperType,
    ledger: ClaimLedger,
) -> EvidenceCoverage:
    if paper_type == PaperType.REVIEW_ARTICLE:
        return EvidenceCoverage(
            paper_type=paper_type,
            requirements=[
                _requirement(
                    "review_scope",
                    "Review scope and question",
                    RequirementLevel.RECOMMENDED,
                    ledger,
                    [r"\b(scope|research question|objective|aim)\b"],
                    "State the review question, boundaries, population or technology, and intended use.",
                )
            ],
        )

    all_text = " ".join(claim.text for claim in ledger.claims).casefold()
    thermal_threshold_measurement = "thermal" in all_text and bool(
        re.search(r"temperature\s+threshold|thermal\s+threshold", all_text)
    )
    requirements = [
        _requirement(
            "system_specification",
            "System/material specification",
            RequirementLevel.DRAFT_BLOCKING,
            ledger,
            [r"\b(controller|processor|sensor|camera|module|material|apparatus|instrument)\b"],
            "Provide manufacturer/model, role, interfaces, and the configuration actually tested.",
        ),
        _requirement(
            "algorithm_parameters",
            "Reproducible algorithm and decision parameters",
            RequirementLevel.DRAFT_BLOCKING,
            ledger,
            [
                r"\b(algorithm|pipeline|threshold|filter|segmentation|classification)\b",
                r"\b(value|set to|parameter|criterion|minimum|maximum|consecutive frames?|"
                r"window (?:of|size)|kernel size|pixel count)\b|\d",
            ],
            (
                "Give the exact temperature threshold, hotspot-area rule, contrast calculation, "
                "connectivity rule, temporal-confirmation count/window, preprocessing, and all "
                "parameter values used in the reported experiment."
            ),
            same_claim=True,
        ),
        _requirement(
            "acquisition_protocol",
            "Experimental acquisition protocol",
            RequirementLevel.DRAFT_BLOCKING,
            ledger,
            [
                r"\b(experiment|trial|flight|collection|acquisition|captured|recorded)\b",
                r"(?:\d+\s*(?:flight|run|site|location|day|m\b|metre|meter))|"
                r"(?:(?:altitude|distance|duration)\s*(?:of|=|:)?\s*\d)|"
                r"(?:(?:site|location|date|period)\s*(?:was|were|=|:)\s*[A-Za-z0-9])",
            ],
            (
                "Report the number of flights/runs, sites, dates or period, altitude and distance, "
                "fire source and non-fire controls, environmental conditions, frame extraction, "
                "and measures preventing near-duplicate leakage."
            ),
            same_claim=True,
        ),
        _requirement(
            "ground_truth_protocol",
            "Ground-truth and annotation protocol",
            RequirementLevel.DRAFT_BLOCKING,
            ledger,
            [
                r"\b(label|labelled|labeled|annotation|ground[- ]truth)\b",
                r"\b(annotator|expert|observer|agreement|adjudicat|criterion|protocol|independent)\b",
            ],
            (
                "State who created the labels, the operational fire/non-fire definition, whether "
                "labelers were independent or blinded, how disagreements were resolved, and any "
                "agreement check."
            ),
            same_claim=True,
        ),
        _requirement(
            "evaluation_independence",
            "Evaluation independence and leakage control",
            RequirementLevel.DRAFT_BLOCKING,
            ledger,
            [
                r"\b(held[- ]out|test set|cross[- ]validation|split|independent test|leave[- ]one|"
                r"thresholds? (?:fixed|selected)|no training|rule[- ]based)\b"
            ],
            (
                "Explain how parameters were chosen separately from evaluation data, how frames "
                "were grouped by flight/site before any split, and exactly which observations "
                "produced the reported metrics."
            ),
        ),
        _requirement(
            "statistical_support",
            "Raw outcomes and uncertainty",
            RequirementLevel.DRAFT_BLOCKING,
            ledger,
            [
                r"\b(TP|TN|FP|FN|true positive|true negative|false positive|false negative|"
                r"confidence interval|bootstrap|standard deviation|standard error|uncertainty)\b"
            ],
            (
                "Provide the confusion-matrix counts and the uncertainty procedure (for example, "
                "confidence intervals grouped at the independent flight/site level)."
            ),
        ),
        _requirement(
            "reported_results",
            "Primary results",
            RequirementLevel.DRAFT_BLOCKING,
            ledger,
            [r"\b(accuracy|precision|recall|specificity|f1|mcc|result|achieved)\b.*\d"],
            "Provide the primary outcomes with units/definitions and identify the exact test set.",
        ),
        _requirement(
            "calibration",
            "Sensor calibration and measurement validity",
            (
                RequirementLevel.DRAFT_BLOCKING
                if thermal_threshold_measurement
                else RequirementLevel.SUBMISSION_BLOCKING
            ),
            ledger,
            [
                r"\b(calibrat|blackbody|emissivity|temperature correction|radiometric accuracy|"
                r"measurement uncertainty)\b"
            ],
            (
                "Report the thermal-camera calibration/verification, emissivity and reflected-"
                "temperature assumptions, warm-up and environmental controls, and GNSS/time "
                "synchronization checks; otherwise explain why a calibrated temperature is not used."
            ),
        ),
        _requirement(
            "external_comparison",
            "Baseline or prior-work comparison strategy",
            RequirementLevel.RECOMMENDED,
            ledger,
            [r"\b(baseline|benchmark|comparison|compared with|not yet included)\b"],
            (
                "Add a fair baseline on the same data when feasible, or explicitly limit claims and "
                "compare protocols qualitatively without ranking incomparable percentages."
            ),
        ),
        _requirement(
            "data_code_availability",
            "Data, code, and reproducibility availability",
            RequirementLevel.SUBMISSION_BLOCKING,
            ledger,
            [
                r"\b(data|dataset|code|repository|software)\b.*\b(available|availability|access|request|not available)\b"
            ],
            "Give verified repository links/identifiers or an accurate reason and access route.",
        ),
        _requirement(
            "permissions_safety",
            "Flight, fire, site, ethics, and safety permissions",
            RequirementLevel.SUBMISSION_BLOCKING,
            ledger,
            [r"\b(permission|permit|approval|regulation|authorized|safety protocol|ethics)\b"],
            (
                "Identify applicable UAV/site/fire permissions and safety controls, or provide the "
                "journal-appropriate not-applicable explanation."
            ),
        ),
        _requirement(
            "submission_declarations",
            "Funding, conflicts, authorship, and AI-use declarations",
            RequirementLevel.SUBMISSION_BLOCKING,
            ledger,
            [
                r"\b(funding|grant|conflict of interest|competing interest|author contribution|AI use)\b"
            ],
            (
                "Supply author-approved funding, conflict-of-interest, contributor-role, and AI-use "
                "statements. PaperForge will never infer these declarations."
            ),
        ),
    ]
    return EvidenceCoverage(paper_type=paper_type, requirements=requirements)


def author_questions_markdown(coverage: EvidenceCoverage) -> str:
    lines = [
        "# Evidence Required Before a Defensible Manuscript",
        "",
        (
            "PaperForge found original-study evidence, but the items below are not yet fully "
            "supported. Add each answer once under `answers:` in `inputs/responses.yaml`, then rerun "
            "with `paperforge run <project>`. Do not guess; use the protocol and records actually used."
        ),
        "",
    ]
    unresolved = [
        item for item in coverage.requirements if item.status != EvidenceCoverageStatus.SUPPORTED
    ]
    if not unresolved:
        lines.append("No unresolved evidence requirement was located.")
        return "\n".join(lines) + "\n"
    for index, item in enumerate(unresolved, start=1):
        lines.extend(
            [
                f"## EVID-{index:03d}: {item.label}",
                "",
                f"- Level: `{item.level.value}`",
                f"- Coverage: `{item.status.value}`",
                f"- Why flagged: {item.explanation}",
                f"- Add: {item.requested_detail}",
                "",
            ]
        )
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class _Match:
    status: EvidenceCoverageStatus
    claim_ids: list[str]


def _requirement(
    code: str,
    label: str,
    level: RequirementLevel,
    ledger: ClaimLedger,
    patterns: list[str],
    requested_detail: str,
    *,
    same_claim: bool = False,
) -> EvidenceRequirement:
    match = _match_claims(ledger, patterns, same_claim=same_claim)
    explanations = {
        EvidenceCoverageStatus.SUPPORTED: "Direct supporting text was located in registered evidence.",
        EvidenceCoverageStatus.PARTIAL: "Some relevant text was located, but the reproducibility detail is incomplete.",
        EvidenceCoverageStatus.NOT_LOCATED: "No direct supporting text was located in registered evidence.",
    }
    return EvidenceRequirement(
        code=code,
        label=label,
        level=level,
        status=match.status,
        explanation=explanations[match.status],
        matched_claim_ids=match.claim_ids,
        requested_detail=requested_detail,
    )


def _match_claims(
    ledger: ClaimLedger,
    patterns: list[str],
    *,
    same_claim: bool,
) -> _Match:
    compiled = [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
    per_pattern: list[list[str]] = []
    for pattern in compiled:
        per_pattern.append([claim.id for claim in ledger.claims if pattern.search(claim.text)])
    if not per_pattern or not any(per_pattern):
        return _Match(EvidenceCoverageStatus.NOT_LOCATED, [])
    matched = list(dict.fromkeys(claim_id for group in per_pattern for claim_id in group))
    if same_claim:
        common = set(per_pattern[0])
        for group in per_pattern[1:]:
            common &= set(group)
        if common:
            return _Match(EvidenceCoverageStatus.SUPPORTED, sorted(common))
        return _Match(EvidenceCoverageStatus.PARTIAL, matched)
    if all(per_pattern):
        return _Match(EvidenceCoverageStatus.SUPPORTED, matched)
    return _Match(EvidenceCoverageStatus.PARTIAL, matched)


def _claim_units(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n")
    blocks = [block.strip() for block in re.split(r"\n\s*\n", normalized) if block.strip()]
    units: list[str] = []
    for block in blocks:
        prefix = ""
        match = re.match(r"^(Q-[A-Za-z0-9_-]+):\s*(.*)$", block, re.DOTALL)
        if match:
            prefix = match.group(1) + ": "
            block = match.group(2)
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", " ".join(block.split()))
        for index, sentence in enumerate(sentences):
            units.append((prefix if index == 0 else "") + sentence)
    return units


def _numeric_atoms(text: str) -> list[str]:
    return [
        " ".join(atom.split())
        for atom in re.findall(
            r"(?<![A-Za-z])(?:[+\-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
            r"(?:\s*[×x]\s*[+\-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)?"
            r"(?:\s*(?:%|ms|s|Hz|fps|px|µm|μm|km/h|cm|mm|km|m|°C|K|V|A|W|kg|g|MB|GB|dB))?)",
            text,
        )
    ]
