from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
import yaml

from paperforge.config import AppConfig, load_config
from paperforge.domain import ReferenceRecord, ResearchProfile
from paperforge.literature import DiscoveryReport
from paperforge.llm import BEGIN_MARKER, END_MARKER
from paperforge.providers.base import ModelProvider, ModelRequest, ModelResponse
from paperforge.storage import ProjectStore


@pytest.fixture
def default_config_path() -> Path:
    return Path(__file__).parents[1] / "config" / "default.yaml"


@pytest.fixture
def project_store(tmp_path: Path, default_config_path: Path) -> ProjectStore:
    store = ProjectStore(tmp_path / "paper")
    store.initialize(
        ResearchProfile(topic="Thermal monitoring for autonomous engineering systems"),
        default_config_path,
    )
    payload = yaml.safe_load(store.config_path.read_text(encoding="utf-8"))
    payload["quality"]["minimum_verified_sources"] = 3
    payload["quality"]["minimum_cited_sources"] = 3
    payload["quality"]["minimum_manuscript_words"] = 300
    payload["quality"]["minimum_tables_for_original_research"] = 0
    payload["quality"]["require_section_depth"] = False
    payload["literature"]["min_sources"] = 3
    payload["literature"]["target_sources"] = 6
    payload["literature"]["max_sources"] = 8
    payload["workflow"]["max_review_cycles"] = 1
    store.config_path.write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )
    return store


@pytest.fixture
def project_config(project_store: ProjectStore) -> AppConfig:
    return load_config(project_store.config_path)


class FakeLiteratureService:
    def __init__(self, count: int = 6) -> None:
        self.count = count
        self.calls = 0

    def collect(self, queries, evidence) -> DiscoveryReport:
        del evidence
        self.calls += 1
        references = [
            ReferenceRecord(
                id=f"REF{index:03d}",
                title=f"Verified thermal engineering source {index}",
                authors=[f"Author {index}", "Researcher B"],
                year=2020 + index % 5,
                venue="Journal of Verified Engineering",
                doi=f"10.1000/test.{index}",
                url=f"https://doi.org/10.1000/test.{index}",
                openalex_id=f"https://openalex.org/W{index}",
                abstract=(
                    "This indexed abstract describes an engineering method, evaluation boundary, "
                    "principal finding, and limitation for evidence-grounded synthesis."
                ),
                cited_by_count=10 * index,
                publication_type="article",
                verified=True,
                verification_sources=["openalex", "crossref"],
            )
            for index in range(1, self.count + 1)
        ]
        return DiscoveryReport(
            references=references,
            queries=list(queries),
            discovered=len(references),
            deduplicated=len(references),
            verified=len(references),
        )

    def close(self) -> None:
        return None


class ScriptedProvider(ModelProvider):
    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()

    def generate(self, request: ModelRequest) -> ModelResponse:
        operation = str(request.metadata.get("operation") or "")
        self.calls[operation] += 1
        context = _context(request.prompt)
        if operation == "plan":
            paper_type = str(context["policy_resolved_paper_type"])
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
                ]
            )
            return _json(
                {
                    "working_title": "Evidence-Grounded Thermal Engineering Study",
                    "paper_type": paper_type,
                    "research_question": "How can thermal evidence support reliable engineering decisions?",
                    "objectives": [
                        "Develop an evidence-grounded technical synthesis.",
                        "Evaluate the supported validity boundary.",
                    ],
                    "contribution": "A reproducible and bounded engineering analysis.",
                    "scope": "Thermal engineering evidence and its documented deployment constraints.",
                    "keywords": [
                        "thermal sensing",
                        "engineering",
                        "evidence",
                        "validation",
                    ],
                    "search_queries": [
                        "thermal sensing engineering validation",
                        "thermal monitoring deployment constraints",
                    ],
                    "required_sections": required,
                    "rationale": "The selected type follows the supplied evidence policy.",
                }
            )
        if operation == "synthesis":
            references = context["verified_references"]
            return _json(
                {
                    "themes": ["Detection methods", "Validation", "Deployment constraints"],
                    "research_gap": (
                        "Prior studies report useful methods, but evidence about integrated "
                        "deployment constraints remains fragmented."
                    ),
                    "novelty_position": (
                        "The manuscript positions its contribution as an evidence-bounded "
                        "integration rather than an unsupported claim of uniqueness."
                    ),
                    "source_notes": [
                        {
                            "reference_id": item["id"],
                            "method": "Engineering evaluation described in the indexed abstract.",
                            "dataset_or_material": "Reported experimental material.",
                            "key_finding": "The source reports a bounded technical finding.",
                            "limitation": "Generalisability is limited by its stated conditions.",
                            "relevance": "Supports the manuscript theme and gap.",
                        }
                        for item in references
                    ],
                    "synthesis_summary": (
                        "The sources collectively support method, validation, and deployment themes."
                    ),
                }
            )
        if operation == "outline":
            plan = context["research_plan"]
            evidence_ids = context["allowed_evidence_ids"]
            reference_ids = context["allowed_reference_ids"]
            return _json(
                {
                    "title": plan["working_title"],
                    "sections": [
                        {
                            "heading": heading,
                            "purpose": f"Develop the evidence-grounded {heading} section.",
                            "evidence_ids": evidence_ids,
                            "reference_ids": reference_ids,
                            "target_words": 140,
                        }
                        for heading in plan["required_sections"]
                    ],
                }
            )
        if operation == "draft":
            return _json({"sections": _draft_sections(context)})
        if operation == "review":
            return self._review_response(request, context)
        if operation == "revise":
            return _json({"sections": _revision(context)})
        raise AssertionError(f"Unexpected model operation: {operation}")

    def _review_response(self, request: ModelRequest, context: dict) -> ModelResponse:
        del context
        return _json(
            {
                "review_type": request.metadata["stage"],
                "summary": "The manuscript satisfies the bounded scripted review.",
                "dimension_scores": {
                    "evidence": 1.0,
                    "methodology": 1.0,
                    "clarity": 1.0,
                },
                "issues": [],
                "recommendation": "proceed",
            }
        )

    def healthcheck(self) -> tuple[bool, str]:
        return True, "scripted provider ready"


class UAVRegressionProvider(ScriptedProvider):
    def _review_response(self, request: ModelRequest, context: dict) -> ModelResponse:
        if request.metadata["stage"] != "final_review":
            return super()._review_response(request, context)
        issues = [
            (
                "FIND-REQUIRED-CALIBRATION",
                "Calibration procedures for the thermal camera and UAV platform are not reported.",
            ),
            (
                "FIND-REQUIRED-UNCERTAINTY",
                "Uncertainty quantification of detection metrics is not reported.",
            ),
            (
                "FIND-REQUIRED-REPRODUCIBILITY",
                "Reproducibility details such as code and data availability are not reported.",
            ),
            (
                "FIND-REQUIRED-BASELINE",
                "No formal comparison with external baseline fire-detection methods is provided.",
            ),
            (
                "FIND-REQUIRED-RELATEDWORK",
                "The Related Work section is empty.",
            ),
            (
                "FIND-REQUIRED-REGULATORY",
                "Regulatory compliance details are not reported.",
            ),
            (
                "FIND-REQUIRED-SIMULATION",
                "The system has not been evaluated in simulation environments.",
            ),
        ]
        return _json(
            {
                "review_type": "final_review",
                "summary": "Strict regression review.",
                "dimension_scores": {"quality": 0.0},
                "issues": [
                    {
                        "id": f"MODEL-{index:03d}",
                        "code": code,
                        "severity": "blocking",
                        "section": "Related Work" if "RELATEDWORK" in code else "Methodology",
                        "description": description,
                        "required_change": description,
                        "requires_new_evidence": "RELATEDWORK" not in code,
                    }
                    for index, (code, description) in enumerate(issues, start=1)
                ],
                "recommendation": "reject",
            }
        )


class GuardRetryProvider(ScriptedProvider):
    def generate(self, request: ModelRequest) -> ModelResponse:
        operation = str(request.metadata.get("operation") or "")
        if (
            operation == "revise"
            and request.metadata.get("stage") == "evidence_review"
            and request.metadata.get("revision_attempt") == 1
        ):
            self.calls[operation] += 1
            requested = _context(request.prompt)["requested_sections"]
            return _json(
                {
                    "sections": [
                        {
                            "heading": item["heading"],
                            "body": "This unsafe replacement is deliberately incomplete.",
                            "claim_ids": [],
                            "evidence_ids": [],
                            "reference_ids": [],
                        }
                        for item in requested
                    ]
                }
            )
        return super().generate(request)

    def _review_response(self, request: ModelRequest, context: dict) -> ModelResponse:
        if request.metadata["stage"] == "evidence_review" and request.metadata["review_cycle"] == 1:
            return _json(
                {
                    "review_type": "evidence_review",
                    "summary": "One safely fixable clarity issue was found.",
                    "dimension_scores": {"evidence": 0.8},
                    "issues": [
                        {
                            "id": "MODEL-RETRY-001",
                            "code": "evidence_clarity",
                            "severity": "high",
                            "section": "Introduction",
                            "description": "Clarify the evidence boundary.",
                            "required_change": "Clarify the evidence boundary.",
                            "requires_new_evidence": False,
                        }
                    ],
                    "recommendation": "revise",
                }
            )
        return super()._review_response(request, context)


def _context(prompt: str) -> dict:
    value = prompt.split("CONTEXT:\n", 1)[1].lstrip()
    parsed, _ = json.JSONDecoder().raw_decode(value)
    return parsed


def _json(payload: dict) -> ModelResponse:
    return ModelResponse(
        content=json.dumps(payload),
        model="scripted-test-model",
        provider="test",
    )


def _text(content: str) -> ModelResponse:
    return ModelResponse(
        content=f"{BEGIN_MARKER}\n{content.strip()}\n{END_MARKER}",
        model="scripted-test-model",
        provider="test",
    )


def _draft_sections(context: dict) -> list[dict]:
    references = list(
        dict.fromkeys(
            reference
            for section in context["requested_sections"]
            for reference in section.get("reference_ids", [])
        )
    )
    citation = (
        "[" + "; ".join(f"@{reference}" for reference in references) + "]" if references else ""
    )
    sections: list[str] = []
    for section in context["requested_sections"]:
        heading = section["heading"]
        normalized = heading.casefold()
        if normalized == "keywords":
            body = "thermal sensing; engineering validation; evidence integrity; edge deployment"
        elif normalized == "abstract":
            body = (
                "This study addresses evidence-grounded thermal engineering analysis for reliable "
                "technical decision making. It uses a bounded workflow that connects the stated "
                "research objective, available engineering evidence, verified scholarly context, "
                "methodological limits, and cautious interpretation. The analysis separates supplied "
                "study facts from published knowledge and preserves uncertainty where supporting "
                "details are unavailable. Results are presented only within the documented evidence "
                "boundary. The resulting manuscript provides a reproducible structure, transparent "
                "limitations, and a technically defensible basis for subsequent author and journal "
                "review without manufacturing experimental claims or bibliographic records. It "
                "also makes the scope, provenance, and unresolved author responsibilities visible "
                "before submission. This separation supports careful peer assessment and prevents "
                "missing protocol details from being disguised as completed scientific work. The "
                "workflow further records the literature boundary, checks every citation identifier, "
                "and rejects unsupported numerical content before assembly. Section-level review then "
                "examines reproducibility, result interpretation, discussion quality, journal rules, "
                "and final consistency while preserving unaffected prose. The outcome is explicitly "
                "classified as a candidate or as requiring author action."
            )
        elif normalized == "review methodology":
            body = (
                "The review used the declared scholarly search queries, selected indexed engineering "
                "records from the configured catalogue, and retained sources with traceable metadata. "
                "Selection focused on topical relevance, available abstracts, verification status, "
                "and the declared scope. Duplicate and retracted records were excluded. The method "
                "does not claim systematic-review coverage beyond the recorded search manifest. "
                f"This boundary supports reproducible interpretation {citation}."
            )
        elif normalized == "methodology":
            body = (
                "The supplied evidence defines the implemented engineering system, experimental "
                "workflow, labelled observations, and ground-truth comparison. Calibration procedures "
                "were not reported in the supplied evidence, and formal uncertainty quantification "
                "was not available; both are stated limitations. Reproducibility is bounded by the "
                "documented components, processing sequence, validation approach, and data-availability "
                "statement. No simulation or unimplemented method is presented as completed work."
            )
        elif normalized in {"related work", "thematic synthesis"}:
            body = (
                "Verified studies collectively describe thermal detection, engineering validation, "
                "and deployment constraints. Their findings are synthesized by technical theme rather "
                "than listed source by source. Differences in sensors, evaluation conditions, and "
                "reporting boundaries prevent unsupported direct performance ranking. The literature "
                "therefore provides context for the stated contribution while preserving comparability "
                f"limits and source-specific uncertainty {citation}."
            )
        elif normalized == "results":
            body = (
                "The results section reports only the performance evidence supplied by the author and "
                "does not introduce additional measurements. Interpretation is limited to the stated "
                "dataset, ground-truth procedure, hardware experiment, and deployment conditions. A "
                "formal external baseline comparison was not included, so the findings demonstrate "
                "internal validation rather than universal superiority. This limitation is retained "
                "throughout the discussion and conclusion."
            )
        elif normalized in {
            "funding",
            "conflict of interest",
            "data availability",
            "author contributions",
        }:
            body = (
                "The author must verify this declaration against the intended submission. No external "
                "funding, repository release, contributor role, or conflict is asserted beyond the "
                "information supplied in the project evidence."
            )
        else:
            body = (
                "This section develops the stated objective through evidence-grounded technical "
                "reasoning. It distinguishes study-specific facts from published context, preserves "
                "the documented validity boundary, and avoids unsupported claims of universality or "
                "superiority. Limitations are incorporated into the interpretation so that the "
                "contribution remains aligned with the available methods, results, and deployment "
                f"conditions {citation}."
            )
        sections.append(
            {
                "heading": heading,
                "body": body,
                "claim_ids": section.get("claim_ids", []),
                "evidence_ids": section.get("evidence_ids", []),
                "reference_ids": section.get("reference_ids", []),
            }
        )
    return sections


def _revision(context: dict) -> list[dict]:
    revised: list[dict] = []
    for section in context["requested_sections"]:
        body = section["current_body"]
        if section["heading"].casefold() == "limitations":
            body += (
                "\n\nCalibration and formal uncertainty procedures were not available in the "
                "supplied evidence. External baseline, simulation, and regulatory evaluations "
                "remain outside the reported study scope and are not presented as completed work."
            )
        revised.append(
            {
                "heading": section["heading"],
                "body": body,
                "claim_ids": section.get("claim_ids", []),
                "evidence_ids": section.get("evidence_ids", []),
                "reference_ids": section.get("reference_ids", []),
            }
        )
    return revised
