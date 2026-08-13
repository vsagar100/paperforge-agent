from __future__ import annotations

import re

from paperforge.domain import ReferenceRecord

CITATION_BLOCK = re.compile(r"\[(?P<body>@REF\d{3,}(?:\s*;\s*@REF\d{3,})*)\]")
CITATION_ID = re.compile(r"@(?P<id>REF\d{3,})")


def cited_reference_ids(manuscript: str) -> list[str]:
    return list(dict.fromkeys(match.group("id") for match in CITATION_ID.finditer(manuscript)))


def unknown_reference_ids(manuscript: str, references: list[ReferenceRecord]) -> list[str]:
    allowed = {reference.id for reference in references}
    return sorted(set(cited_reference_ids(manuscript)) - allowed)


def invalid_reference_markers(manuscript: str) -> list[str]:
    """Return REF markers that are not enclosed in canonical citation blocks."""
    remainder = CITATION_BLOCK.sub("", manuscript)
    return sorted(set(CITATION_ID.findall(remainder)))


def strip_reference_section(manuscript: str) -> str:
    match = re.search(r"(?im)^#{1,3}\s+references\s*$", manuscript)
    if not match:
        return manuscript.rstrip()
    return manuscript[: match.start()].rstrip()


def render_numbered_citations(
    manuscript: str, references: list[ReferenceRecord]
) -> tuple[str, list[ReferenceRecord]]:
    by_id = {reference.id: reference for reference in references}
    ordered_ids = [
        reference_id for reference_id in cited_reference_ids(manuscript) if reference_id in by_id
    ]
    numbering = {reference_id: index for index, reference_id in enumerate(ordered_ids, start=1)}

    def replace(match: re.Match[str]) -> str:
        ids = CITATION_ID.findall(match.group(0))
        numbers = [numbering[reference_id] for reference_id in ids if reference_id in numbering]
        return "[" + ", ".join(str(number) for number in numbers) + "]"

    body = CITATION_BLOCK.sub(replace, strip_reference_section(manuscript))
    cited = [by_id[reference_id] for reference_id in ordered_ids]
    references_text = "\n".join(
        f"{index}. {_format_ieee(reference)}" for index, reference in enumerate(cited, start=1)
    )
    if references_text:
        body += "\n\n## References\n\n" + references_text
    return body.rstrip() + "\n", cited


def build_bibtex(references: list[ReferenceRecord]) -> str:
    entries: list[str] = []
    for reference in references:
        authors = " and ".join(reference.authors) or "Unknown"
        fields = [
            f"  title = {{{_bib_escape(reference.title)}}}",
            f"  author = {{{_bib_escape(authors)}}}",
        ]
        if reference.year:
            fields.append(f"  year = {{{reference.year}}}")
        if reference.venue:
            fields.append(f"  journal = {{{_bib_escape(reference.venue)}}}")
        if reference.volume:
            fields.append(f"  volume = {{{_bib_escape(reference.volume)}}}")
        if reference.issue:
            fields.append(f"  number = {{{_bib_escape(reference.issue)}}}")
        if reference.pages:
            fields.append(f"  pages = {{{_bib_escape(reference.pages)}}}")
        if reference.publisher:
            fields.append(f"  publisher = {{{_bib_escape(reference.publisher)}}}")
        if reference.doi:
            fields.append(f"  doi = {{{reference.doi}}}")
        if reference.url:
            fields.append(f"  url = {{{_bib_escape(reference.url)}}}")
        entries.append(f"@article{{{reference.id},\n" + ",\n".join(fields) + "\n}")
    return "\n\n".join(entries) + ("\n" if entries else "")


def _format_ieee(reference: ReferenceRecord) -> str:
    authors = _format_authors(reference.authors)
    title = f"“{reference.title.rstrip('.')},”"
    venue = f" *{reference.venue}*," if reference.venue else ""
    volume = f" vol. {reference.volume}," if reference.volume else ""
    issue = f" no. {reference.issue}," if reference.issue else ""
    pages = f" pp. {reference.pages}," if reference.pages else ""
    year = f" {reference.year}." if reference.year else ""
    doi = f" doi: {reference.doi}." if reference.doi else ""
    url = f" {reference.url}." if not reference.doi and reference.url else ""
    return f"{authors}, {title}{venue}{volume}{issue}{pages}{year}{doi}{url}".replace(
        "..", "."
    ).strip()


def _format_authors(authors: list[str]) -> str:
    if not authors:
        return "Unknown author"
    if len(authors) > 6:
        return ", ".join(authors[:6]) + ", et al."
    if len(authors) == 1:
        return authors[0]
    return ", ".join(authors[:-1]) + ", and " + authors[-1]


def _bib_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
