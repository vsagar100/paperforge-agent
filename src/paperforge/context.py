"""Bounded task packets; canonical evidence and manuscripts remain untouched on disk."""

from __future__ import annotations

import json
import re

from paperforge.audit import source_text
from paperforge.schemas import InputItem, Section, Source
from paperforge.store import Store, fingerprint


def size(value) -> int:
    # Match Gateway.structured's serialization, including escaped OCR/control characters.
    return len(json.dumps(value, ensure_ascii=False))


def text_parts(text: str, budget: int) -> list[tuple[int, str]]:
    """Exact contiguous spans, bounded by serialized size; no characters are discarded."""
    if budget < 16:
        raise ValueError("Insufficient space for an evidence span")
    parts, start = [], 0
    while start < len(text):
        low, high = 1, min(len(text) - start, budget)
        while low < high:
            middle = (low + high + 1) // 2
            if size(text[start : start + middle]) <= budget:
                low = middle
            else:
                high = middle - 1
        end = start + low
        # Prefer sentence/word boundaries without losing whitespace or punctuation.
        if end < len(text):
            boundary = text.rfind(" ", start + low // 2, end)
            if boundary != -1:
                end = boundary + 1
        parts.append((start, text[start:end]))
        start = end
    return parts


def section_batches(
    sections: list[Section], budget: int, *, max_paragraph_budget: int | None = None
) -> list[list[dict]]:
    """Each full paragraph occurs once. A large section spans several review calls."""
    batches, batch = [], []
    max_paragraph_budget = max_paragraph_budget or budget
    for section in sections:
        for paragraph in section.paragraphs:
            value = paragraph.model_dump(mode="json")
            entry = {"title": section.title, "paragraphs": [value]}
            if size([entry]) > max_paragraph_budget:
                raise ValueError(
                    f"A single paragraph in {section.title!r} exceeds the review batch budget. "
                    "Split this paragraph into smaller paragraphs; saved work is preserved."
                )
            candidate = [dict(item, paragraphs=list(item["paragraphs"])) for item in batch]
            if candidate and candidate[-1]["title"] == section.title:
                candidate[-1]["paragraphs"].append(value)
            else:
                candidate.append(entry)
            if size(candidate) > budget and batch:
                batches.append(batch)
                batch = [entry]
            else:
                batch = candidate
    if batch:
        batches.append(batch)
    return batches


class ContextComposer:
    def __init__(self, store: Store, limit: int):
        self.store, self.limit = store, limit

    def compose(
        self,
        *,
        project: dict,
        sources: list[Source],
        assessments: list[dict],
        inputs: list[InputItem],
        results: list[dict],
        task: dict | None = None,
    ) -> dict:
        task = task or {}
        query = " ".join(
            [project["topic"], task.get("requested_section", ""), str(task.get("plan", {}))]
        )
        tokens = set(re.findall(r"[a-z]{3,}", query.lower()))
        catalog = [
            {"id": s.id, "title": s.title, "year": s.year, "access_level": s.access_level}
            for s in sources
        ]
        packet = {
            "project": project,
            **task,
            "source_catalog": catalog,
            "sources": [],
            "source_assessments": [],
            "inputs": [],
            "results": [],
        }
        # Reserve room for the selection manifest. Mandatory instructions are never clipped.
        allowance = self.limit - 512
        if size(packet) > allowance:
            packet["source_catalog"] = [{"id": s.id} for s in sources]
        if size(packet) > allowance:
            raise ValueError(
                "Task instructions or a single manuscript section exceed the configured context "
                "budget even before evidence is added. Split the section or shorten the plan; "
                "saved evidence and completed work are preserved."
            )
        atoms = []

        def add(bucket, identifier, value, text, locator, priority=0):
            terms = set(re.findall(r"[a-z]{3,}", text.lower()))
            atoms.append(
                {
                    "bucket": bucket,
                    "id": identifier,
                    "value": value,
                    "locator": locator,
                    "sha256": fingerprint(text),
                    "score": priority + len(terms & tokens),
                }
            )

        # Existing claims' exact quotes take precedence over retrieval heuristics.
        material_by_id = {s.id: source_text(s) for s in sources}
        material_by_id.update(
            {i.id: "\n".join(c.get("text", "") for c in i.chunks) for i in inputs}
        )
        result_by_id = {r["id"]: r for r in results}
        sections = task.get("sections", [])
        if task.get("previous_section"):
            sections = [*sections, task["previous_section"]]
        anchors = set()
        for section in sections:
            for paragraph in section["paragraphs"]:
                for identifier, quote in paragraph.get("supporting_quotes", {}).items():
                    if quote and quote in material_by_id.get(identifier, ""):
                        if (identifier, quote) not in anchors:
                            anchors.add((identifier, quote))
                            bucket = (
                                "sources" if identifier in {s.id for s in sources} else "inputs"
                            )
                            add(
                                bucket,
                                identifier,
                                {"id": identifier, "excerpts": [{"text": quote}]},
                                quote,
                                "validated existing claim quote",
                                100000,
                            )
                    elif identifier in result_by_id:
                        # The whole calculator record, never a model's asserted metric.
                        record = result_by_id[identifier]
                        add("results", identifier, record, str(record), "calculator", 100000)

        for result in results:
            add("results", result["id"], result, str(result), "calculator", 50000)
        for assessment in assessments:
            for field in ("method", "dataset_or_system", "finding", "reported_limitation"):
                fact = assessment.get(field)
                if fact:
                    add(
                        "source_assessments",
                        assessment["source_id"],
                        {
                            "source_id": assessment["source_id"],
                            "category": assessment["category"],
                            field: fact,
                        },
                        fact["summary"] + " " + fact["quote"],
                        field,
                        1000,
                    )
        for source in sources:
            for start, text in text_parts(source_text(source), 1600):
                add(
                    "sources",
                    source.id,
                    {
                        "id": source.id,
                        "access_level": source.access_level,
                        "excerpts": [{"text": text, "start": start}],
                    },
                    text,
                    {"start": start, "end": start + len(text)},
                )
        for item in inputs:
            for index, chunk in enumerate(item.chunks):
                for start, text in text_parts(chunk.get("text", ""), 1600):
                    add(
                        "inputs",
                        item.id,
                        {
                            "id": item.id,
                            "role": item.role,
                            "status": item.status,
                            "path": item.path,
                            "excerpts": [{**chunk, "text": text, "start": start}],
                            "warnings": item.warnings,
                        },
                        text,
                        {"chunk": index, "start": start, "end": start + len(text)},
                        2000 if item.role in {"author_note", "dataset"} else 0,
                    )
        # Round-robin by evidence owner avoids filling the packet from the first long PDF.
        groups = {}
        for atom in sorted(atoms, key=lambda a: -a["score"]):
            groups.setdefault(atom["id"], []).append(atom)
        ordered = []
        depth = 0
        while any(depth < len(group) for group in groups.values()):
            layer = [group[depth] for group in groups.values() if depth < len(group)]
            ordered.extend(sorted(layer, key=lambda a: -a["score"]))
            depth += 1
        # All pinned claim support and computed records precede optional retrieval units.
        ordered = sorted(ordered, key=lambda a: a["score"] < 50000)
        accepted, omitted, seen = [], [], set()
        packet_size = size(packet)
        for atom in ordered:
            signature = fingerprint([atom["bucket"], atom["value"]])
            if signature in seen:
                continue
            seen.add(signature)
            extra = size(atom["value"]) + (2 if packet[atom["bucket"]] else 0)
            if packet_size + extra <= allowance:
                packet[atom["bucket"]].append(atom["value"])
                packet_size += extra
                accepted.append(atom)
            else:
                omitted.append(atom)
        audit = {
            "limit_chars": self.limit,
            "selected": [
                {k: a[k] for k in ("bucket", "id", "locator", "sha256")} for a in accepted
            ],
            "omitted": [{k: a[k] for k in ("bucket", "id", "locator", "sha256")} for a in omitted],
            "omission_reason": "task relevance and bounded request budget",
        }
        audit_path = "audit/context/" + fingerprint([packet, audit]) + ".json"
        packet["context_selection"] = {
            "scope": "Selected evidence only; omitted material is not evidence of absence. Full originals remain in the project registers.",
            "selected_units": len(accepted),
            "omitted_units": len(omitted),
            "audit_file": audit_path,
            "source_catalog_is_evidence": False,
        }
        if size(packet) > self.limit:
            raise ValueError(
                "Selection manifest exceeds the reserved context space; saved evidence is unchanged"
            )
        audit["packet_chars"] = size(packet)
        self.store.write(audit_path, json.dumps(audit, ensure_ascii=False, indent=2))
        return packet
