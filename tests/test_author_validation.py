from paperforge.author_validation import build_author_validation_package
from paperforge.domain import (
    AuthorValidationDecision,
    EvidenceItem,
    EvidenceKind,
    LiteratureNote,
    LiteratureSynthesis,
    PaperType,
    ReferenceRecord,
)
from paperforge.evidence import assess_evidence_coverage, build_claim_ledger


def _synthesis() -> LiteratureSynthesis:
    return LiteratureSynthesis(
        themes=["Acquisition and evaluation"],
        research_gap="Study protocols are reported inconsistently.",
        novelty_position="Use a bounded comparison.",
        source_notes=[
            LiteratureNote(
                reference_id="REF001",
                method="The source reports flight-grouped thermal data acquisition.",
                limitation="The protocol is specific to its own dataset.",
                relevance="Provides reporting context for acquisition and leakage control.",
            )
        ],
        synthesis_summary="The source informs reporting practice, not this study's facts.",
    )


def test_validation_is_topic_specific_research_linked_and_preserves_author_decisions() -> None:
    evidence = [
        EvidenceItem(
            id="EV-UAV",
            kind=EvidenceKind.USER_FACT,
            title="UAV study",
            content=(
                "A Raspberry Pi and thermal camera processed 1,000 labelled frames and achieved "
                "96.8% accuracy in a hardware experiment."
            ),
        )
    ]
    ledger = build_claim_ledger(evidence)
    coverage = assess_evidence_coverage(PaperType.ORIGINAL_RESEARCH, ledger)
    references = [
        ReferenceRecord(
            id="REF001",
            title="Flight-grouped acquisition for thermal UAV evaluation",
            abstract=(
                "The study reports flight-level data acquisition, annotation, and leakage-aware "
                "evaluation for thermal UAV observations."
            ),
            verified=True,
            verification_sources=["openalex"],
        )
    ]

    first = build_author_validation_package(
        topic="Edge-cloud UAV thermal fire detection",
        coverage=coverage,
        ledger=ledger,
        references=references,
        synthesis=_synthesis(),
    )

    acquisition = next(
        item for item in first.items if item.requirement_code == "acquisition_protocol"
    )
    assert "Edge-cloud UAV thermal fire detection" in acquisition.question
    assert acquisition.reference_ids == ["REF001"]
    assert (
        "not study evidence" in first.instructions.casefold()
        or "never proof" in first.instructions.casefold()
    )

    acquisition.decision = AuthorValidationDecision.CORRECTED
    acquisition.answer = "Six flights were conducted and observations were grouped by flight."
    second = build_author_validation_package(
        topic="Edge-cloud UAV thermal fire detection",
        coverage=coverage,
        ledger=ledger,
        references=references,
        synthesis=_synthesis(),
        existing=first,
    )
    preserved = next(
        item for item in second.items if item.requirement_code == "acquisition_protocol"
    )
    assert preserved.decision == AuthorValidationDecision.CORRECTED
    assert preserved.answer.startswith("Six flights")


def test_topic_only_review_does_not_ask_for_author_only_experimental_facts() -> None:
    ledger = build_claim_ledger([])
    coverage = assess_evidence_coverage(PaperType.REVIEW_ARTICLE, ledger)
    package = build_author_validation_package(
        topic="Thermal monitoring methods",
        coverage=coverage,
        ledger=ledger,
        references=[],
        synthesis=LiteratureSynthesis(
            themes=["Thermal monitoring"],
            research_gap="Reporting remains fragmented.",
            novelty_position="Synthesize the bounded literature.",
            synthesis_summary="A review synthesis.",
        ),
    )
    assert package.items == []
