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
