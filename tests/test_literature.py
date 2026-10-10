import httpx
import pytest

from paperforge.literature import Literature, LiteratureFailure


def test_openalex_deduplicates_and_reconstructs_abstract(store):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "W1",
                        "title": "Test-only source",
                        "publication_year": 2025,
                        "doi": "https://doi.org/10.0000/test",
                        "abstract_inverted_index": {"method": [1], "A": [0]},
                        "primary_location": {"source": {"display_name": "Test journal"}},
                        "authorships": [],
                    },
                    {"id": "W2", "title": "Retracted test", "is_retracted": True},
                ]
            },
        )

    service = Literature(store, httpx.MockTransport(handler))
    try:
        records = service.search("thermal test", 1, 5)
        assert records[0].abstract == "A method"
        assert records[0].metadata_verified
        assert records[0].access_level == "abstract"
        assert service.search("thermal test", 1, 5) == records
        assert len(requests) == 1
    finally:
        service.close()


def test_crossref_fallback_does_not_claim_full_text(store):
    def handler(request):
        if "openalex" in request.url.host:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={
                "message": {
                    "items": [
                        {
                            "DOI": "10.0000/test",
                            "title": ["Test-only record"],
                            "type": "journal-article",
                            "published": {"date-parts": [[2025]]},
                            "container-title": ["Test journal"],
                            "author": [{"given": "A", "family": "Author"}],
                        }
                    ]
                }
            },
        )

    service = Literature(store, httpx.MockTransport(handler))
    try:
        record = service.search("test", 1, 5)[0]
        assert record.access_level == "metadata"
        assert not record.abstract
        assert record.authors == ["A Author"]
    finally:
        service.close()


def test_no_provider_metadata_fails_without_inventing_sources(store):
    service = Literature(store, httpx.MockTransport(lambda _: httpx.Response(503)))
    try:
        with pytest.raises(LiteratureFailure, match="No verified"):
            service.search("test", 1, 5)
    finally:
        service.close()
