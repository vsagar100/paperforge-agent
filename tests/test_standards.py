from paperforge.domain import PaperType, ResearchPlan, ResearchProfile
from paperforge.standards import build_publication_profile
from paperforge.validators import ValidationContext, validate_manuscript


def test_djes_profile_encodes_checked_research_contract(project_config) -> None:
    profile = build_publication_profile(
        ResearchProfile(
            topic="Thermal UAV fire detection for controlled field experiments",
            target_journal="Diyala Journal of Engineering Sciences (DJES)",
        ),
        project_config,
        PaperType.ORIGINAL_RESEARCH,
    )

    assert profile.profile_id == "djes-research-2026-08"
    assert profile.target_rules_verified is True
    assert (profile.word_min, profile.word_max) == (4000, 7000)
    assert (profile.abstract_min_words, profile.abstract_max_words) == (200, 250)
    assert (profile.reference_min, profile.reference_max) == (25, 40)
    assert profile.section_order.index("Results") < profile.section_order.index("Discussion")


def test_generic_profile_never_presents_indexing_criteria_as_manuscript_rules(
    project_config,
) -> None:
    profile = build_publication_profile(
        ResearchProfile(
            topic="Thermal engineering monitoring with a bounded evidence base",
            target_journal="Unconfigured Engineering Journal",
        ),
        project_config,
        PaperType.REVIEW_ARTICLE,
    )

    assert profile.target_rules_verified is False
    assert "not manuscript-acceptance checklists" in profile.indexing_context
    assert any("Scopus" in url or "scopus" in url for url in profile.source_urls)


def test_unchecked_target_journal_remains_an_author_action(
    project_config,
) -> None:
    profile = build_publication_profile(
        ResearchProfile(
            topic="Thermal engineering monitoring with a bounded evidence base",
            target_journal="Unconfigured Engineering Journal",
        ),
        project_config,
        PaperType.ORIGINAL_RESEARCH,
    )
    validation = ValidationContext(
        config=project_config,
        plan=ResearchPlan(
            working_title="Bounded thermal engineering study",
            paper_type=PaperType.ORIGINAL_RESEARCH,
            research_question="How does the supplied system perform?",
            objectives=["Evaluate the supplied system."],
            contribution="An evidence-bounded engineering evaluation.",
            scope="The supplied controlled experiment.",
            keywords=["thermal", "engineering", "monitoring"],
            search_queries=[
                "thermal engineering monitoring",
                "thermal sensing experimental validation",
            ],
            required_sections=[
                "Abstract",
                "Introduction",
                "Methodology",
                "Results",
                "Conclusion",
            ],
            rationale="The test isolates publication-contract validation.",
        ),
        evidence=[],
        references=[],
        publication_profile=profile,
    )
    issues = validate_manuscript(
        "# Study\n\n## Abstract\n\nA bounded abstract.\n",
        validation,
        review_type="journal_review",
    )

    target = next(item for item in issues if item.code == "target_journal_rules_unverified")
    assert target.requires_new_evidence is True
    assert target.disposition.value == "author_action"
