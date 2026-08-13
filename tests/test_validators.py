from paperforge.domain import (
    EvidenceItem,
    EvidenceKind,
    IssueDisposition,
    PaperType,
    ReferenceRecord,
    ResearchPlan,
    ReviewIssue,
    Severity,
)
from paperforge.validators import IssuePolicy, ValidationContext, validate_revision


def context(project_config) -> ValidationContext:
    return ValidationContext(
        config=project_config,
        plan=ResearchPlan(
            working_title="UAV thermal fire detection",
            paper_type=PaperType.ORIGINAL_RESEARCH,
            research_question="Can the system detect fire?",
            objectives=["Evaluate the supplied system."],
            contribution="Evidence-bounded integration.",
            scope="Controlled hardware experiments using the implemented UAV system.",
            keywords=["UAV", "thermal", "fire"],
            search_queries=["UAV thermal fire detection", "edge thermal fire"],
            required_sections=[
                "Abstract",
                "Introduction",
                "Related Work",
                "Methodology",
                "Results",
                "Discussion",
                "Limitations",
                "Conclusion",
            ],
            rationale="Original methods and results are supplied.",
        ),
        evidence=[
            EvidenceItem(
                id="EV-UAV",
                kind=EvidenceKind.USER_FACT,
                title="UAV evidence",
                content="The supplied accuracy was 96.80% for 1,000 labelled frames.",
            )
        ],
        references=[
            ReferenceRecord(
                id="REF001",
                title="Verified source",
                authors=["A. Author"],
                year=2024,
                doi="10.1000/test",
                verified=True,
                verification_sources=["crossref"],
            )
        ],
    )


def issue(code: str, description: str) -> ReviewIssue:
    return ReviewIssue(
        id=code,
        code=code,
        severity=Severity.BLOCKING,
        section="Methodology",
        description=description,
        required_change=description,
        requires_new_evidence=True,
    )


def test_scope_policy_downgrades_optional_demands_but_not_empty_related_work(
    project_config,
) -> None:
    policy = IssuePolicy(context(project_config))
    normalized = policy.normalize_all(
        [
            issue("FIND-REQUIRED-CALIBRATION", "Calibration is not reported."),
            issue("FIND-REQUIRED-UNCERTAINTY", "Uncertainty is not reported."),
            issue("FIND-REQUIRED-BASELINE", "No external baseline is provided."),
            issue("FIND-REQUIRED-REGULATORY", "Regulatory detail is not reported."),
            issue("FIND-REQUIRED-SIMULATION", "Simulation was not evaluated."),
            issue("FIND-REQUIRED-RELATEDWORK", "The Related Work section is empty."),
        ]
    )
    by_code = {item.code: item for item in normalized}
    assert by_code["calibration_not_reported"].disposition == IssueDisposition.AUTHOR_ACTION
    assert by_code["uncertainty_not_reported"].severity == Severity.MEDIUM
    assert by_code["external_baseline_not_in_scope"].disposition == IssueDisposition.RECOMMENDATION
    assert by_code["regulatory_detail_not_in_scope"].severity == Severity.LOW
    assert by_code["simulation_not_in_scope"].severity == Severity.LOW
    assert by_code["related_work_empty"].disposition == IssueDisposition.INTEGRITY_BLOCKER


def test_optional_topic_is_required_only_by_explicit_scope_or_journal_rule(
    project_config,
) -> None:
    validation_context = context(project_config)
    validation_context.plan.scope += " Flight regulations are a deployment constraint."
    regulatory = IssuePolicy(validation_context).normalize(
        issue("FIND-REQUIRED-REGULATORY", "Regulatory detail is not reported.")
    )
    assert regulatory.disposition == IssueDisposition.RECOMMENDATION

    validation_context.plan.required_sections.append("Regulatory Compliance")
    required = IssuePolicy(validation_context).normalize(
        issue("FIND-REQUIRED-REGULATORY", "Regulatory detail is not reported.")
    )
    assert required.code == "regulatory_required_by_scope"
    assert required.disposition == IssueDisposition.AUTHOR_ACTION


def test_revision_guard_rejects_unknown_citation_and_new_number(project_config) -> None:
    validation_context = context(project_config)
    previous = "# Study\n\n## Results\n\nThe supplied accuracy was 96.80%."
    candidate = (
        previous
        + "\n\nAn external claim appears here [@REF999]."
        + "\n\nA new unsupported value is 99.9%."
    )
    issues = validate_revision(previous, candidate, validation_context)
    assert {item.code for item in issues} >= {
        "unknown_citation",
        "unsupported_numeric_claim",
    }
    assert all(item.disposition == IssueDisposition.INTEGRITY_BLOCKER for item in issues)


def test_revision_guard_normalizes_numeric_precision_and_checks_mixed_paragraph(
    project_config,
) -> None:
    validation_context = context(project_config)
    previous = "# Study\n\n## Results\n\nThe supplied accuracy was 96.80%."
    equivalent = previous.replace("96.80%", "96.8%")
    assert not {
        item.code for item in validate_revision(previous, equivalent, validation_context)
    } & {"unsupported_numeric_claim"}

    mixed = (
        previous
        + "\n\nPrior work reported a bounded result [@REF001]. "
        + "Our unsupported accuracy was 99.9%."
    )
    assert "unsupported_numeric_claim" in {
        item.code for item in validate_revision(previous, mixed, validation_context)
    }


def test_revision_guard_rejects_malformed_known_citation(project_config) -> None:
    validation_context = context(project_config)
    previous = "# Study\n\n## Results\n\nThe supplied accuracy was 96.80%."
    candidate = previous + "\n\nA source is cited using malformed syntax [see @REF001]."
    issues = validate_revision(previous, candidate, validation_context)
    malformed = next(item for item in issues if item.code == "invalid_citation_syntax")
    assert malformed.disposition == IssueDisposition.INTEGRITY_BLOCKER


def test_unverified_citation_remains_integrity_blocker(project_config) -> None:
    validation_context = context(project_config)
    unverified = validation_context.references[0].model_copy(
        update={"verified": False, "verification_sources": []}
    )
    validation_context.references = [unverified]
    from paperforge.validators import validate_manuscript

    manuscript = (
        "# Study\n\n## Related Work\n\nA sufficiently developed scholarly synthesis cites "
        "the catalogue while preserving its bounded technical interpretation and methodological "
        "context [@REF001]."
    )
    issues = validate_manuscript(
        manuscript,
        validation_context,
        review_type="evidence_review",
    )
    citation_issue = next(item for item in issues if item.code == "unverified_citation")
    assert citation_issue.disposition == IssueDisposition.INTEGRITY_BLOCKER
