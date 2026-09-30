from __future__ import annotations

import json
import re

from paperforge.schemas import InputItem, ReviewIssue, Section, Source


def issue(
    section: str, description: str, category: str = "evidence", author: bool = False
) -> ReviewIssue:
    return ReviewIssue(
        section=section,
        description=description,
        required_change="Correct the claim using traceable evidence, or disclose the missing evidence explicitly",
        category=category,
        needs_author=author,
    )


def source_text(source: Source) -> str:
    return source.abstract + "\n" + "\n".join(p["text"] for p in source.passages)


def audit_sections(
    sections: list[Section], sources: list[Source], inputs: list[InputItem], results: list[dict]
) -> list[ReviewIssue]:
    texts = {
        s.id: source_text(s) for s in sources if s.metadata_verified and source_text(s).strip()
    }
    evidence = {
        item.id: "\n".join(chunk["text"] for chunk in item.chunks) for item in inputs if item.chunks
    }
    evidence.update({result["id"]: json.dumps(result, sort_keys=True) for result in results})
    findings = []
    for section in sections:
        for index, paragraph in enumerate(section.paragraphs, 1):
            label = f"{section.title}, paragraph {index}"
            if re.search(r"\[\d+(?:\s*[-–,]\s*\d+)*\]", paragraph.text):
                findings.append(
                    issue(
                        section.title,
                        f"{label}: numeric citations must be assigned by the assembler",
                        "citation",
                    )
                )
            if unknown := set(paragraph.source_ids) - texts.keys():
                findings.append(
                    issue(
                        section.title,
                        f"{label}: inaccessible/unknown source IDs {sorted(unknown)}",
                        "citation",
                    )
                )
            if unknown := set(paragraph.evidence_ids) - evidence.keys():
                findings.append(
                    issue(section.title, f"{label}: unknown evidence IDs {sorted(unknown)}")
                )
            if paragraph.kind == "literature" and not paragraph.source_ids:
                findings.append(
                    issue(
                        section.title,
                        f"{label}: literature claim lacks a supporting accessible source",
                        "citation",
                    )
                )
            if paragraph.kind == "study" and not paragraph.evidence_ids:
                findings.append(
                    issue(
                        section.title,
                        f"{label}: study finding lacks input/result provenance",
                        author=True,
                    )
                )
            if paragraph.kind == "interpretation" and not (
                paragraph.source_ids or paragraph.evidence_ids
            ):
                findings.append(
                    issue(section.title, f"{label}: interpretation lacks an evidence anchor")
                )
            if paragraph.kind in {"literature", "study"}:
                required = (
                    paragraph.source_ids
                    if paragraph.kind == "literature"
                    else paragraph.evidence_ids
                )
                for identifier in required:
                    quote = paragraph.supporting_quotes.get(identifier, "").strip()
                    accessible = texts.get(identifier, evidence.get(identifier, ""))
                    if not quote or quote not in accessible:
                        findings.append(
                            issue(
                                section.title,
                                f"{label}: exact supporting excerpt missing for {identifier}",
                            )
                        )
            # Quote existence is necessary but not sufficient for entailment; reviewer checks that.
            if paragraph.kind == "study":
                material = " ".join(evidence.get(eid, "") for eid in paragraph.evidence_ids)
                numbers = [
                    float(number)
                    for number in re.findall(r"(?<![A-Za-z])\b\d+(?:\.\d+)?\b", material)
                ]
                for number in re.findall(r"(\d+(?:\.\d+)?)\s*%", paragraph.text):
                    value = float(number)
                    if not any(
                        abs(value - n) <= 0.005 or abs(value / 100 - n) <= 0.00005 for n in numbers
                    ):
                        findings.append(
                            issue(
                                section.title,
                                f"{label}: unsupported percentage {number}%",
                                author=True,
                            )
                        )
            if section.title in {"Abstract", "Conclusion"} and paragraph.source_ids:
                findings.append(
                    issue(section.title, f"{label}: remove citations from this section", "format")
                )
            if paragraph.kind in {"proposal", "disclosure"} and re.search(
                r"\b(achieved|outperformed|measured|demonstrated superiority)\b",
                paragraph.text,
                re.I,
            ):
                if not re.search(r"\b(not|no|unavailable|pending)\b", paragraph.text, re.I):
                    findings.append(
                        issue(
                            section.title,
                            f"{label}: proposal/disclosure presented as achieved outcome",
                            author=True,
                        )
                    )
    return findings


def unresolved_placeholders(sections: list[Section]) -> list[str]:
    return sorted(
        {
            match
            for section in sections
            for paragraph in section.paragraphs
            for match in re.findall(
                r"\[(?:INSERT|RESULT|AUTHOR|REFERENCE|EXPERIMENT)[^\]]*\]", paragraph.text, re.I
            )
        }
    )
