from __future__ import annotations

import httpx

from paperforge.config import LiteratureConfig
from paperforge.domain import ReferenceRecord
from paperforge.literature import CrossrefClient, LiteratureService, OpenAlexClient


def test_openalex_search_reconstructs_abstract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "https://openalex.org/W1",
                        "doi": "https://doi.org/10.1000/example",
                        "title": "Thermal Detection Study",
                        "publication_year": 2024,
                        "authorships": [{"author": {"display_name": "A. Researcher"}}],
                        "primary_location": {
                            "landing_page_url": "https://doi.org/10.1000/example",
                            "source": {"display_name": "Thermal Journal"},
                        },
                        "best_oa_location": {},
                        "open_access": {},
                        "cited_by_count": 12,
                        "biblio": {
                            "volume": "9",
                            "issue": "2",
                            "first_page": "101",
                            "last_page": "112",
                        },
                        "abstract_inverted_index": {
                            "Thermal": [0],
                            "detection": [1],
                            "works": [2],
                        },
                        "type": "article",
                        "is_retracted": False,
                    }
                ]
            },
            request=request,
        )

    config = LiteratureConfig(results_per_query=5)
    client = OpenAlexClient(config, transport=httpx.MockTransport(handler))
    references = client.search("thermal detection")
    client.close()
    assert references[0].abstract == "Thermal detection works"
    assert references[0].doi == "10.1000/example"
    assert references[0].volume == "9"
    assert references[0].pages == "101-112"
    assert references[0].verified is True


def test_crossref_verifies_title_and_doi() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {
                    "DOI": "10.1000/example",
                    "title": ["Thermal Detection Study"],
                    "author": [{"given": "A.", "family": "Researcher"}],
                    "published-online": {"date-parts": [[2024, 1, 1]]},
                    "container-title": ["Thermal Journal"],
                    "volume": "9",
                    "issue": "2",
                    "page": "101-112",
                    "publisher": "Verified Publisher",
                    "URL": "https://doi.org/10.1000/example",
                    "type": "journal-article",
                }
            },
            request=request,
        )

    config = LiteratureConfig()
    client = CrossrefClient(config, transport=httpx.MockTransport(handler))
    reference = client.verify_doi(
        "https://doi.org/10.1000/example",
        "Thermal Detection Study",
    )
    client.close()
    assert reference is not None
    assert reference.verified is True
    assert reference.year == 2024
    assert reference.issue == "2"
    assert reference.publisher == "Verified Publisher"
    assert reference.verification_sources == ["crossref"]


class StubOpenAlex:
    def search(self, query: str) -> list[ReferenceRecord]:
        return [
            ReferenceRecord(
                id="REF000",
                title=f"{query} source {index}",
                authors=["A. Author"],
                year=2024,
                doi=f"10.1000/{index}",
                openalex_id=f"https://openalex.org/W{index}",
                abstract="Indexed abstract.",
                publication_type="article",
                verified=True,
                verification_sources=["openalex"],
                metadata={"query_rank": index},
            )
            for index in range(1, 4)
        ]

    def close(self) -> None:
        return None


class StubCrossref:
    def verify_doi(self, doi: str, expected_title: str | None = None):
        return ReferenceRecord(
            id="REF000",
            title=expected_title or "Verified title",
            authors=["A. Author"],
            year=2024,
            doi=doi,
            publication_type="journal-article",
            verified=True,
            verification_sources=["crossref"],
        )

    def close(self) -> None:
        return None


def test_literature_service_deduplicates_and_assigns_stable_ids() -> None:
    config = LiteratureConfig(
        min_sources=3,
        target_sources=3,
        max_sources=3,
        results_per_query=5,
    )
    service = LiteratureService(config, StubOpenAlex(), StubCrossref())
    report = service.collect(["thermal", "thermal"], [])
    service.close()
    assert [reference.id for reference in report.references] == [
        "REF001",
        "REF002",
        "REF003",
    ]
    assert all(
        reference.verification_sources == ["openalex", "crossref"]
        for reference in report.references
    )


class QuerySpecificOpenAlex:
    def search(self, query: str) -> list[ReferenceRecord]:
        return [
            ReferenceRecord(
                id="REF000",
                title=f"{query} source {index}",
                authors=["A. Author"],
                year=2024,
                doi=f"10.1000/{query}.{index}",
                abstract="Indexed abstract.",
                publication_type="article",
                verified=True,
                verification_sources=["openalex"],
                metadata={
                    "query": query,
                    "matched_queries": [query],
                    "query_rank": index,
                },
            )
            for index in range(1, 5)
        ]

    def close(self) -> None:
        return None


def test_literature_selection_balances_distinct_queries() -> None:
    config = LiteratureConfig(
        min_sources=4,
        target_sources=4,
        max_sources=8,
        results_per_query=5,
    )
    service = LiteratureService(config, QuerySpecificOpenAlex(), StubCrossref())
    report = service.collect(["thermal", "wildfire"], [])
    service.close()
    titles = [reference.title for reference in report.references]
    assert sum(title.startswith("thermal") for title in titles) == 2
    assert sum(title.startswith("wildfire") for title in titles) == 2
