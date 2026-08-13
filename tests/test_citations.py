from paperforge.citations import (
    build_bibtex,
    render_numbered_citations,
    unknown_reference_ids,
)
from paperforge.domain import ReferenceRecord


def references() -> list[ReferenceRecord]:
    return [
        ReferenceRecord(
            id="REF001",
            title="First verified source",
            authors=["A. Author"],
            year=2023,
            venue="Engineering Journal",
            volume="12",
            issue="3",
            pages="101-110",
            doi="10.1000/first",
            verified=True,
        ),
        ReferenceRecord(
            id="REF002",
            title="Second verified source",
            authors=["B. Author"],
            year=2024,
            venue="Engineering Journal",
            doi="10.1000/second",
            verified=True,
        ),
    ]


def test_citations_are_rendered_by_first_appearance() -> None:
    manuscript = (
        "# Study\n\n## Introduction\n\nSecond first [@REF002], then both [@REF001; @REF002].\n"
    )
    rendered, cited = render_numbered_citations(manuscript, references())
    assert "Second first [1], then both [2, 1]." in rendered
    assert [item.id for item in cited] == ["REF002", "REF001"]
    assert "## References" in rendered
    assert "vol. 12, no. 3, pp. 101-110" in rendered


def test_unknown_citation_and_bibtex_generation() -> None:
    assert unknown_reference_ids("Claim [@REF999].", references()) == ["REF999"]
    bibtex = build_bibtex(references())
    assert "@article{REF001" in bibtex
    assert "doi = {10.1000/first}" in bibtex
    assert "volume = {12}" in bibtex
    assert "pages = {101-110}" in bibtex
