from paperforge.domain import EvidenceItem, EvidenceKind, PaperType
from paperforge.evidence import assess_evidence_coverage, build_claim_ledger


def test_incomplete_original_study_exposes_reproducibility_gaps() -> None:
    evidence = [
        EvidenceItem(
            id="EV-STUDY",
            kind=EvidenceKind.USER_FACT,
            title="Study responses",
            content=(
                "A Raspberry Pi and thermal camera were used. The pipeline applies temperature "
                "thresholding and temporal confirmation. A dataset of 1,000 labelled frames was "
                "evaluated and achieved 96.8% accuracy."
            ),
        )
    ]
    coverage = assess_evidence_coverage(
        PaperType.ORIGINAL_RESEARCH,
        build_claim_ledger(evidence),
    )
    blockers = {item.code for item in coverage.draft_blockers}
    assert "algorithm_parameters" in blockers
    assert "ground_truth_protocol" in blockers
    assert "evaluation_independence" in blockers
    assert "statistical_support" in blockers
    assert "calibration" in blockers


def test_complete_protocol_can_pass_the_pre_draft_evidence_gate() -> None:
    evidence = [
        EvidenceItem(
            id="EV-COMPLETE",
            kind=EvidenceKind.EXPERIMENTAL_DATA,
            title="Complete experimental protocol",
            content=(
                "The apparatus used a Raspberry Pi processor and a radiometric thermal camera. "
                "The detection pipeline used a temperature threshold set to 65 °C, a minimum pixel "
                "count of 12, and temporal confirmation over 3 consecutive frames. The experiment "
                "comprised 12 flights at Site A at an altitude of 20 m. Two independent experts "
                "created ground-truth labels using a written annotation protocol and adjudicated "
                "disagreements. Thresholds were fixed before an independent test set grouped by "
                "flight was evaluated. Raw outcomes were TP=486, TN=482, FP=18, and FN=14, and 95% "
                "confidence intervals were calculated. The camera calibration used a blackbody "
                "reference and recorded emissivity. The system achieved 96.8% accuracy."
            ),
        )
    ]
    coverage = assess_evidence_coverage(
        PaperType.ORIGINAL_RESEARCH,
        build_claim_ledger(evidence),
    )
    assert coverage.draft_blockers == []


def test_literature_search_manifest_never_becomes_study_claim_evidence() -> None:
    ledger = build_claim_ledger(
        [
            EvidenceItem(
                id="EV-COMPUTED-LITERATURE-SEARCH",
                kind=EvidenceKind.COMPUTED,
                title="Literature search manifest",
                content=(
                    "Discovered records: 200. Selected records: 30. Externally indexed records: 30."
                ),
                metadata={
                    "generated": True,
                    "calculator": "literature_search_manifest",
                },
            )
        ]
    )
    assert ledger.claims == []


def test_requirement_details_are_tailored_beyond_the_uav_thermal_domain() -> None:
    ledger = build_claim_ledger(
        [
            EvidenceItem(
                id="EV-MATERIAL",
                kind=EvidenceKind.EXPERIMENTAL_DATA,
                title="Materials experiment",
                content=(
                    "A universal testing machine evaluated labelled concrete specimens, and the "
                    "reported results include compressive strength."
                ),
            )
        ]
    )
    coverage = assess_evidence_coverage(
        PaperType.ORIGINAL_RESEARCH,
        ledger,
        topic="Machine-learning prediction of concrete compressive strength",
    )
    requirements = {item.code: item for item in coverage.requirements}
    assert "temperature threshold" not in requirements["algorithm_parameters"].requested_detail
    assert "flights" not in requirements["acquisition_protocol"].requested_detail
    assert "UAV" not in requirements["permissions_safety"].requested_detail
    assert "instrument or sensor" in requirements["calibration"].requested_detail
