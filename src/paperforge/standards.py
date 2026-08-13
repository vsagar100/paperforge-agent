from __future__ import annotations

import re
from datetime import date

from paperforge.config import AppConfig
from paperforge.domain import PaperType, PublicationProfile, ResearchProfile

DJES_GUIDELINES = "https://djes.info/index.php/djes/AuthorGuidelines"
IEEE_REVIEW = (
    "https://journals.ieeeauthorcenter.ieee.org/submit-your-article-for-peer-review/"
    "become-an-ieee-reviewer/"
)
IEEE_REPRODUCIBILITY = (
    "https://journals.ieeeauthorcenter.ieee.org/create-your-ieee-journal-article/"
    "research-reproducibility/"
)
SCOPUS_SELECTION = "https://www.elsevier.com/products/scopus/content/content-policy-and-selection"
WOS_SELECTION = (
    "https://clarivate.com/academia-government/scientific-and-academic-research/"
    "research-discovery-and-referencing/web-of-science/web-of-science-core-collection/"
    "editorial-selection-process/journal-evaluation-process-selection-criteria/"
)


def build_publication_profile(
    research: ResearchProfile,
    config: AppConfig,
    paper_type: PaperType,
) -> PublicationProfile:
    """Resolve checked journal rules without pretending indexing databases review manuscripts."""
    journal = (research.target_journal or config.journal.name or "").strip()
    if _is_djes(journal):
        return _djes_profile(journal, paper_type)
    return _generic_engineering_profile(journal or None, config, paper_type)


def section_minimum(profile: PublicationProfile, heading: str) -> int:
    normalized = normalize_heading(heading)
    if normalized in profile.section_min_words:
        return profile.section_min_words[normalized]
    if any(
        token in normalized
        for token in (
            "data availability",
            "conflict of interest",
            "funding",
            "author contribution",
            "declaration of ai",
            "ethics",
        )
    ):
        return 12
    return 180


def normalize_heading(value: str) -> str:
    clean = re.sub(r"^\d+(?:\.\d+)*[.)]?\s*", "", value.casefold())
    return re.sub(r"[^a-z0-9]+", " ", clean).strip()


def _is_djes(journal: str) -> bool:
    normalized = normalize_heading(journal)
    return normalized == "djes" or "diyala journal of engineering sciences" in normalized


def _djes_profile(journal: str, paper_type: PaperType) -> PublicationProfile:
    declarations = [
        "Data Availability",
        "Conflict of Interest",
        "Funding",
        "Author Contributions",
        "Declaration of AI Use",
    ]
    if paper_type == PaperType.REVIEW_ARTICLE:
        return PublicationProfile(
            profile_id="djes-review-2026-08",
            journal_name=journal or "Diyala Journal of Engineering Sciences",
            article_type="review_article",
            source_label="DJES Author Guidelines, checked 2026-08-13",
            source_urls=[DJES_GUIDELINES, IEEE_REVIEW, IEEE_REPRODUCIBILITY],
            checked_on="2026-08-13",
            target_rules_verified=True,
            word_min=8000,
            word_target=9000,
            word_max=12000,
            abstract_min_words=200,
            abstract_max_words=250,
            keyword_min=4,
            keyword_max=6,
            reference_min=50,
            reference_max=100,
            table_max=5,
            figure_max=10,
            section_order=[
                "Abstract",
                "Keywords",
                "Introduction",
                "Review Methodology",
                "Related Work",
                "Thematic Synthesis",
                "Discussion",
                "Research Gaps and Future Directions",
                "Limitations",
                "Conclusion",
                *declarations,
            ],
            section_min_words={
                "abstract": 200,
                "keywords": 4,
                "introduction": 700,
                "review methodology": 650,
                "related work": 1200,
                "thematic synthesis": 2500,
                "discussion": 1200,
                "research gaps and future directions": 500,
                "limitations": 300,
                "conclusion": 300,
            },
            required_declarations=declarations,
            formatting_rules=_djes_formatting_rules(),
            review_dimensions=_review_dimensions(),
            indexing_context=_indexing_context(),
        )
    return PublicationProfile(
        profile_id="djes-research-2026-08",
        journal_name=journal or "Diyala Journal of Engineering Sciences",
        article_type="research_article",
        source_label="DJES Author Guidelines, checked 2026-08-13",
        source_urls=[DJES_GUIDELINES, IEEE_REVIEW, IEEE_REPRODUCIBILITY],
        checked_on="2026-08-13",
        target_rules_verified=True,
        word_min=4000,
        word_target=5200,
        word_max=7000,
        abstract_min_words=200,
        abstract_max_words=250,
        keyword_min=4,
        keyword_max=6,
        reference_min=25,
        reference_max=40,
        table_max=5,
        figure_max=10,
        section_order=[
            "Abstract",
            "Keywords",
            "Introduction",
            "Related Work",
            "Materials and Methods",
            "Results",
            "Discussion",
            "Limitations",
            "Conclusion",
            *declarations,
        ],
        section_min_words={
            "abstract": 200,
            "keywords": 4,
            "introduction": 550,
            "related work": 650,
            "materials and methods": 1050,
            "methodology": 1050,
            "results": 600,
            "discussion": 750,
            "limitations": 220,
            "conclusion": 220,
        },
        required_declarations=declarations,
        formatting_rules=_djes_formatting_rules(),
        review_dimensions=_review_dimensions(),
        indexing_context=_indexing_context(),
    )


def _generic_engineering_profile(
    journal: str | None,
    config: AppConfig,
    paper_type: PaperType,
) -> PublicationProfile:
    is_review = paper_type == PaperType.REVIEW_ARTICLE
    target = max(config.paper.target_word_count, 7000 if is_review else 5000)
    word_min = config.quality.minimum_manuscript_words
    sections = (
        [
            "Abstract",
            "Keywords",
            "Introduction",
            "Review Methodology",
            "Related Work",
            "Thematic Synthesis",
            "Discussion",
            "Research Gaps and Future Directions",
            "Limitations",
            "Conclusion",
        ]
        if is_review
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
    declarations = list(dict.fromkeys(config.journal.required_declarations))
    return PublicationProfile(
        profile_id=f"generic-engineering-{date.today().isoformat()}",
        journal_name=journal,
        article_type="review_article" if is_review else config.journal.manuscript_type,
        source_label="Generic engineering journal profile; target-journal rules require verification",
        source_urls=[IEEE_REVIEW, IEEE_REPRODUCIBILITY, SCOPUS_SELECTION, WOS_SELECTION],
        checked_on=date.today().isoformat(),
        target_rules_verified=False,
        word_min=word_min,
        word_target=target,
        word_max=12000 if is_review else 8000,
        abstract_min_words=150,
        abstract_max_words=config.journal.abstract_max_words,
        keyword_min=4,
        keyword_max=8,
        reference_min=max(
            config.quality.minimum_verified_sources,
            config.quality.minimum_cited_sources,
        ),
        reference_max=None,
        table_max=None,
        figure_max=None,
        section_order=[*sections, *declarations],
        section_min_words={
            "abstract": 150,
            "keywords": 4,
            "introduction": 500,
            "review methodology": 500,
            "related work": 650 if not is_review else 900,
            "thematic synthesis": 1800,
            "methodology": 950,
            "results": 550,
            "discussion": 700,
            "research gaps and future directions": 350,
            "limitations": 200,
            "conclusion": 200,
        },
        required_declarations=declarations,
        formatting_rules=[
            "Verify the current target-journal template before submission.",
            "Use editable tables and equations and publication-quality figure files.",
            "Prepare a separate title page when the target journal uses anonymous review.",
        ],
        review_dimensions=_review_dimensions(),
        indexing_context=_indexing_context(),
    )


def _djes_formatting_rules() -> list[str]:
    return [
        "Use the current DJES Microsoft Word template and A4 page size.",
        "Submit an anonymized main manuscript plus separate title page and cover letter.",
        "Use numbered sections and IEEE-style references in first-appearance order.",
        "Keep tables editable and supply figures at a minimum of 300 dpi.",
        "Keep equations editable and number them when referenced.",
        "Verify declarations, ethical permissions, and AI-use disclosure before submission.",
    ]


def _review_dimensions() -> list[str]:
    return [
        "Contribution and novelty are explicit and proportionate to the evidence.",
        "The literature review is complete, current, relevant, and balanced.",
        "Methods and experimental design are sound, detailed, and reproducible.",
        "Results are appropriately analyzed, clearly presented, and traceable to evidence.",
        "Discussion answers the research question and distinguishes findings from inference.",
        "Conclusions follow from results and introduce no new claims.",
        "References, organization, style, declarations, tables, and figures meet journal rules.",
    ]


def _indexing_context() -> str:
    return (
        "Scopus and Web of Science/SCIE selection criteria evaluate journals and their editorial "
        "quality; they are not manuscript-acceptance checklists. PaperForge therefore evaluates "
        "the manuscript against the current target-journal instructions, reproducibility and "
        "research-integrity gates, and peer-review dimensions."
    )
