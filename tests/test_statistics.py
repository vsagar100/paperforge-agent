from paperforge.domain import EvidenceItem, EvidenceKind
from paperforge.statistics import StatisticsDeriver


def test_confusion_matrix_metrics_are_derived_without_a_model() -> None:
    evidence = [
        EvidenceItem(
            id="EV-DATA",
            kind=EvidenceKind.EXPERIMENTAL_DATA,
            title="Confusion matrix",
            content="TP=486, FN=14, FP=18, TN=482",
        )
    ]
    derived = StatisticsDeriver.derive(evidence)
    computed = next(item for item in derived if item.kind == EvidenceKind.COMPUTED)
    assert "accuracy: 0.968000" in computed.content
    assert "recall: 0.972000" in computed.content
    assert "specificity: 0.964000" in computed.content
    assert "95% Wilson interval" in computed.content
    assert computed.metadata["source_evidence_ids"] == ["EV-DATA"]


def test_metrics_are_not_invented_without_all_four_counts() -> None:
    evidence = [
        EvidenceItem(
            id="EV-DATA",
            kind=EvidenceKind.EXPERIMENTAL_DATA,
            title="Partial results",
            content="TP=486 and FN=14",
        )
    ]
    assert StatisticsDeriver.derive(evidence) == evidence
