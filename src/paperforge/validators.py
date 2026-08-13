from __future__ import annotations

import re
from dataclasses import dataclass

from paperforge.domain import Claim, EvidenceItem, Finding, Severity


@dataclass(slots=True)
class ValidationReport:
    findings: list[Finding]

    @property
    def score(self) -> float:
        weights = {
            Severity.LOW: 0.03,
            Severity.MEDIUM: 0.08,
            Severity.HIGH: 0.2,
            Severity.BLOCKING: 0.4,
        }
        return max(0.0, 1.0 - sum(weights[x.severity] for x in self.findings if not x.resolved))


def validate_claim_links(claims: list[Claim], evidence: list[EvidenceItem]) -> ValidationReport:
    evidence_by_id = {item.id: item for item in evidence}
    findings: list[Finding] = []
    for claim in claims:
        unknown = [item for item in claim.evidence_ids if item not in evidence_by_id]
        if unknown:
            findings.append(
                Finding(
                    id=f"CLAIM-{claim.id}-UNKNOWN",
                    stage="citation_audit",
                    severity=Severity.BLOCKING,
                    section=claim.section,
                    problem=f"Claim references unknown evidence IDs: {', '.join(unknown)}",
                    required_action="Register and verify the evidence or remove the unsupported claim.",
                )
            )
        if claim.numeric and not claim.evidence_ids:
            findings.append(
                Finding(
                    id=f"CLAIM-{claim.id}-NUMERIC",
                    stage="citation_audit",
                    severity=Severity.HIGH,
                    section=claim.section,
                    problem="Numeric claim has no linked experimental or computed evidence.",
                    required_action="Link a verified dataset location or deterministic calculation.",
                )
            )
        unverified = [
            x for x in claim.evidence_ids if x in evidence_by_id and not evidence_by_id[x].verified
        ]
        if unverified:
            findings.append(
                Finding(
                    id=f"CLAIM-{claim.id}-UNVERIFIED",
                    stage="citation_audit",
                    severity=Severity.HIGH,
                    section=claim.section,
                    problem=f"Claim relies on unverified evidence: {', '.join(unverified)}",
                    required_action="Verify source identity, locator, and content before publication.",
                )
            )
    return ValidationReport(findings)


def validate_engineering_manuscript(text: str) -> ValidationReport:
    findings: list[Finding] = []
    lowered = text.lower()
    required_markers = sorted(set(re.findall(r"REQUIRED\[([^\]]+)\]", text, flags=re.IGNORECASE)))
    if required_markers:
        findings.append(
            Finding(
                id="ENG-UNRESOLVED-REQUIRED-MARKERS",
                stage="engineering_integrity",
                severity=Severity.HIGH,
                problem=f"The manuscript contains {len(required_markers)} unresolved REQUIRED marker(s).",
                required_action="Resolve each marker using authentic project evidence before submission.",
            )
        )
    required_method_terms = {
        "calibration": "Describe instrument/sensor calibration and traceability.",
        "uncertainty": "Quantify measurement uncertainty or justify why it is not applicable.",
        "reproducibility": "Provide enough parameters and procedure for reproduction.",
        "limitations": "State engineering limitations and validity boundaries.",
    }
    for term, action in required_method_terms.items():
        if term not in lowered:
            findings.append(
                Finding(
                    id=f"ENG-MISSING-{term.upper()}",
                    stage="engineering_integrity",
                    severity=Severity.MEDIUM,
                    section="Methodology",
                    problem=f"No explicit {term} treatment was detected.",
                    required_action=action,
                )
            )

    if re.search(r"\b\d+(?:\.\d+)?\s*(?:C|F)\b", text) and "°" not in text:
        findings.append(
            Finding(
                id="ENG-TEMPERATURE-UNIT",
                stage="engineering_integrity",
                severity=Severity.LOW,
                problem="Temperature values may use ambiguous C/F notation.",
                required_action="Use °C or K consistently and define the measurement conditions.",
                auto_fixable=True,
            )
        )

    equations = re.findall(r"\$\$(.*?)\$\$|\$(.*?)\$", text, flags=re.DOTALL)
    if equations and not any(word in lowered for word in ("where", "denotes", "is defined as")):
        findings.append(
            Finding(
                id="ENG-EQUATION-SYMBOLS",
                stage="engineering_integrity",
                severity=Severity.MEDIUM,
                problem="Equations were found without an apparent definition of variables.",
                required_action="Define every symbol, unit, assumption, and applicable range.",
            )
        )
    return ValidationReport(findings)


def validate_manuscript_structure(text: str, abstract_max_words: int) -> ValidationReport:
    findings: list[Finding] = []
    headings = {x.strip().lower() for x in re.findall(r"^#{1,3}\s+(.+)$", text, re.MULTILINE)}
    for section in (
        "abstract",
        "introduction",
        "methodology",
        "results",
        "discussion",
        "conclusion",
        "references",
    ):
        if not any(section in heading for heading in headings):
            findings.append(
                Finding(
                    id=f"STRUCT-{section.upper()}",
                    stage="manuscript_consistency",
                    severity=Severity.HIGH,
                    section=section,
                    problem=f"Required section '{section}' is missing.",
                    required_action=f"Create an evidence-grounded {section} section.",
                )
            )
    match = re.search(r"(?ims)^#{1,3}\s+abstract\s*$\n(.*?)(?=^#{1,3}\s+)", text)
    if match:
        count = len(re.findall(r"\b[\w'-]+\b", match.group(1)))
        if count > abstract_max_words:
            findings.append(
                Finding(
                    id="ABSTRACT-WORD-LIMIT",
                    stage="abstract_review",
                    severity=Severity.HIGH,
                    section="Abstract",
                    problem=f"Abstract has {count} words; limit is {abstract_max_words}.",
                    required_action="Shorten without removing verified method, main result, or conclusion.",
                    auto_fixable=True,
                )
            )
    if re.search(r"\[(?:citation needed|source required)\]", text, flags=re.IGNORECASE):
        findings.append(
            Finding(
                id="CITATION-PLACEHOLDER",
                stage="citation_audit",
                severity=Severity.HIGH,
                section="Manuscript",
                problem="The manuscript contains unresolved citation placeholders.",
                required_action="Link each claim to a verified supplied source or remove the claim.",
            )
        )
    return ValidationReport(findings)
