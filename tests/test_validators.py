from paperforge.domain import Claim, EvidenceItem, EvidenceKind, Severity
from paperforge.validators import (
    validate_claim_links,
    validate_engineering_manuscript,
    validate_manuscript_structure,
)


def test_numeric_claim_requires_evidence() -> None:
    report = validate_claim_links(
        [Claim(id="CL-ACC", text="Accuracy was 96.8%", section="Results", numeric=True)], []
    )
    assert report.findings[0].severity == Severity.HIGH


def test_unknown_and_unverified_evidence_are_rejected() -> None:
    claims = [Claim(id="CL-X", text="x", section="Results", evidence_ids=["EV-DATA", "EV-MISSING"])]
    evidence = [
        EvidenceItem(
            id="EV-DATA",
            kind=EvidenceKind.EXPERIMENTAL,
            title="Dataset",
            content="n=10",
            verified=False,
        )
    ]
    ids = {x.id for x in validate_claim_links(claims, evidence).findings}
    assert ids == {"CLAIM-CL-X-UNKNOWN", "CLAIM-CL-X-UNVERIFIED"}


def test_engineering_checks_detect_missing_reproducibility_controls() -> None:
    report = validate_engineering_manuscript("# Methodology\nA test was performed.")
    ids = {x.id for x in report.findings}
    assert "ENG-MISSING-CALIBRATION" in ids
    assert "ENG-MISSING-UNCERTAINTY" in ids
    assert "ENG-MISSING-REPRODUCIBILITY" in ids


def test_unresolved_required_marker_is_never_submission_ready() -> None:
    report = validate_engineering_manuscript(
        "# Methodology\nCalibration, uncertainty, reproducibility, and limitations. "
        "REQUIRED[insert verified sample size]"
    )
    assert {finding.id for finding in report.findings} == {"ENG-UNRESOLVED-REQUIRED-MARKERS"}


def test_structure_and_abstract_word_limit() -> None:
    text = "# Abstract\n" + "word " * 11 + "\n# Introduction\ntext"
    report = validate_manuscript_structure(text, 10)
    ids = {x.id for x in report.findings}
    assert "ABSTRACT-WORD-LIMIT" in ids
    assert "STRUCT-METHODOLOGY" in ids
