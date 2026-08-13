from __future__ import annotations

import json
import re

from paperforge.providers.base import ModelProvider, ModelRequest, ModelResponse

BEGIN_MARKER = "<!-- PAPERFORGE:BEGIN -->"
END_MARKER = "<!-- PAPERFORGE:END -->"


class MockProvider(ModelProvider):
    """Deterministic provider for CLI smoke tests; it never claims publishable output."""

    def generate(self, request: ModelRequest) -> ModelResponse:
        operation = str(request.metadata.get("operation") or "")
        context = _context(request.prompt)
        if operation == "plan":
            paper_type = str(context.get("policy_resolved_paper_type") or "review_article")
            required = (
                [
                    "Abstract",
                    "Keywords",
                    "Introduction",
                    "Review Methodology",
                    "Related Work",
                    "Thematic Synthesis",
                    "Discussion",
                    "Limitations",
                    "Conclusion",
                    "Data Availability",
                    "Conflict of Interest",
                ]
                if paper_type == "review_article"
                else [
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
            )
            payload = {
                "working_title": "Evidence-Bounded Engineering Study",
                "paper_type": paper_type,
                "research_question": "How can the stated engineering topic be evaluated?",
                "objectives": ["Synthesize the supplied evidence without fabrication."],
                "contribution": "A bounded demonstration of the PaperForge workflow.",
                "scope": "Demonstration using only supplied evidence.",
                "keywords": ["engineering", "evidence", "workflow"],
                "search_queries": ["engineering evidence workflow", "engineering research review"],
                "required_sections": required,
                "rationale": "The deterministic mock follows the configured paper-type policy.",
            }
            return _json_response(payload)
        if operation == "synthesis":
            references = context.get("verified_references") or []
            payload = {
                "themes": ["Evidence integrity"],
                "research_gap": "The demonstration does not assert a real research gap.",
                "novelty_position": "No novelty claim is made by the mock provider.",
                "source_notes": [
                    {
                        "reference_id": item["id"],
                        "method": "Not reported in the available metadata.",
                        "dataset_or_material": "Not reported in the available metadata.",
                        "key_finding": "Not reported in the available metadata.",
                        "limitation": "Not reported in the available metadata.",
                        "relevance": "Available to the deterministic test workflow.",
                    }
                    for item in references
                ],
                "synthesis_summary": "The mock provider does not perform scholarly synthesis.",
            }
            return _json_response(payload)
        if operation == "outline":
            plan = context.get("research_plan") or {}
            evidence_ids = context.get("allowed_evidence_ids") or []
            reference_ids = context.get("allowed_reference_ids") or []
            payload = {
                "title": plan.get("working_title") or "Evidence-Bounded Engineering Study",
                "sections": [
                    {
                        "heading": heading,
                        "purpose": f"Provide the evidence-bounded {heading} section.",
                        "evidence_ids": evidence_ids,
                        "reference_ids": reference_ids,
                        "target_words": 120,
                    }
                    for heading in plan.get("required_sections") or []
                ],
            }
            return _json_response(payload)
        if operation == "review":
            return _json_response(
                {
                    "review_type": request.metadata.get("stage") or "review",
                    "summary": "Deterministic mock review completed.",
                    "dimension_scores": {"integrity": 1.0, "clarity": 1.0},
                    "issues": [],
                    "recommendation": "proceed",
                }
            )
        if operation == "draft":
            sections = context.get("requested_sections") or []
            reference_ids = context.get("allowed_reference_ids") or []
            citation = f" [@{reference_ids[0]}]" if reference_ids else ""
            chunks = []
            for section in sections:
                heading = section.get("heading") or "Section"
                if heading.casefold() == "keywords":
                    body = (
                        "engineering; evidence integrity; reproducible workflow; academic writing"
                    )
                else:
                    body = (
                        "This deterministic paragraph exists only to exercise the workflow and "
                        "does not assert an experiment, result, or external scholarly finding. "
                        "Authentic content must come from user evidence and verified sources. "
                        "The section preserves technical caution, transparent limitations, and "
                        "clear provenance throughout the generated manuscript." + citation
                    )
                chunks.append(f"## {heading}\n\n{body}")
            return _text_response("\n\n".join(chunks))
        if operation == "revise":
            manuscript = str(context.get("manuscript") or "")
            return _text_response(manuscript)
        return _json_response({"message": "mock response"})

    def healthcheck(self) -> tuple[bool, str]:
        return True, "Deterministic mock provider is ready"


def _context(prompt: str) -> dict:
    marker = "CONTEXT:\n"
    if marker not in prompt:
        return {}
    candidate = prompt.split(marker, 1)[1].lstrip()
    try:
        value, _ = json.JSONDecoder().raw_decode(candidate)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _json_response(payload: dict) -> ModelResponse:
    return ModelResponse(
        content=json.dumps(payload),
        model="deterministic-mock",
        provider="mock",
    )


def _text_response(content: str) -> ModelResponse:
    clean = re.sub(r"(?im)^#{1,3}\s+references\s*$.*\Z", "", content).strip()
    return ModelResponse(
        content=f"{BEGIN_MARKER}\n{clean}\n{END_MARKER}",
        model="deterministic-mock",
        provider="mock",
    )
