from __future__ import annotations

import hashlib
import os
import re
from datetime import UTC, datetime
from urllib.parse import quote

import httpx

from paperforge.schemas import Source
from paperforge.store import Store, fingerprint


class LiteratureFailure(RuntimeError):
    pass


def clean_abstract(text: str) -> str:
    # JATS metadata is flattened for evidence context, never executed/rendered.
    return re.sub(r"<[^>]+>", " ", text).strip()


def identifier(doi: str | None, title: str) -> str:
    return "SRC-" + hashlib.sha256((doi or title).casefold().encode()).hexdigest()[:12]


class Literature:
    def __init__(self, store: Store, transport: httpx.BaseTransport | None = None):
        self.store = store
        self.client = httpx.Client(transport=transport, timeout=30, follow_redirects=False)

    def close(self):
        self.client.close()

    def _get(self, url: str, params: dict | None = None) -> dict:
        cache_key = "literature:" + fingerprint([url, params, datetime.now(UTC).date().isoformat()])
        if cached := self.store.cached_checkpoint(cache_key):
            return cached
        try:
            response = self.client.get(url, params=params)
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError("Expected scholarly metadata object")
        except (httpx.HTTPError, ValueError) as exc:
            raise LiteratureFailure(
                "Scholarly metadata request failed; retry this stage or provide verified source metadata"
            ) from exc
        self.store.checkpoint(cache_key, body)
        return body

    def crossref(self, doi: str) -> Source:
        doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi.strip(), flags=re.I)
        record = self._get("https://api.crossref.org/works/" + quote(doi, safe=""))["message"]
        return self._crossref_record(record)

    @staticmethod
    def _crossref_record(record: dict) -> Source:
        title = " ".join(record.get("title", []))
        if not title or not record.get("DOI"):
            raise LiteratureFailure("Crossref record lacks required title/DOI")
        parts = (record.get("published") or record.get("issued") or {}).get("date-parts", [])
        year = parts[0][0] if parts and parts[0] else None
        doi = record["DOI"].lower()
        abstract = clean_abstract(record.get("abstract", ""))
        return Source(
            id=identifier(doi, title),
            title=title,
            authors=[
                " ".join(filter(None, [a.get("given"), a.get("family")]))
                for a in record.get("author", [])
            ],
            year=year,
            doi=doi,
            url="https://doi.org/" + doi,
            venue=" ".join(record.get("container-title", [])),
            abstract=abstract,
            volume=record.get("volume"),
            issue=record.get("issue"),
            pages=record.get("page"),
            metadata_verified=True,
            verification="Crossref registered metadata; indexing/claim support not implied",
            access_level="abstract" if abstract else "metadata",
        )

    def search(self, topic: str, target: int, years: int) -> list[Source]:
        sources: dict[str, Source] = {}
        # Prefer OpenAlex abstracts. Crossref is a metadata fallback, not fabricated full text.
        openalex_params = {
            "search": topic,
            "per-page": min(target, 100),
            "filter": "is_retracted:false,type:article",
        }
        if key := os.getenv("OPENALEX_API_KEY"):
            openalex_params["api_key"] = key
        errors = []
        try:
            response = self._get("https://api.openalex.org/works", openalex_params)
            for work in response.get("results", []):
                if work.get("is_retracted") or not work.get("title"):
                    continue
                doi = (work.get("doi") or "").removeprefix("https://doi.org/").lower() or None
                index = work.get("abstract_inverted_index") or {}
                positions = {position: word for word, ids in index.items() for position in ids}
                abstract = " ".join(positions[p] for p in sorted(positions))
                title = work["title"]
                source = Source(
                    id=identifier(doi, title),
                    title=title,
                    authors=[
                        a.get("author", {}).get("display_name", "")
                        for a in work.get("authorships", [])
                    ],
                    year=work.get("publication_year"),
                    doi=doi,
                    url=doi and "https://doi.org/" + doi or work["id"],
                    venue=((work.get("primary_location") or {}).get("source") or {}).get(
                        "display_name", ""
                    ),
                    abstract=abstract,
                    metadata_verified=True,
                    verification="OpenAlex indexed metadata; journal indexing and claim entailment require separate checks",
                    access_level="abstract" if abstract else "metadata",
                )
                sources[source.id] = source
        except LiteratureFailure as exc:
            errors.append(str(exc))
        if len(sources) < target:
            params = {
                "query.bibliographic": topic,
                "rows": min(target, 100),
                "filter": "type:journal-article",
            }
            if email := os.getenv("PAPERFORGE_CONTACT_EMAIL"):
                params["mailto"] = email
            try:
                body = self._get("https://api.crossref.org/works", params)
                for record in body.get("message", {}).get("items", []):
                    if record.get("type") != "journal-article" or record.get("relation", {}).get(
                        "is-retracted-by"
                    ):
                        continue
                    source = self._crossref_record(record)
                    old = sources.get(source.id)
                    if not old or (not old.abstract and source.abstract):
                        sources[source.id] = source
            except LiteratureFailure as exc:
                errors.append(str(exc))
        if not sources:
            raise LiteratureFailure("No verified metadata retrieved. " + "; ".join(errors))
        current = datetime.now(UTC).year
        records = sorted(
            sources.values(),
            key=lambda s: (
                bool(s.abstract),
                bool(s.year and s.year >= current - years + 1),
                s.year or 0,
            ),
            reverse=True,
        )[:target]
        self.store.event(
            "literature_search",
            {
                "query": topic,
                "retrieved": len(records),
                "year_window": years,
                "warnings": errors,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )
        return records

    def dataset_candidates(self, terms: list[str]) -> list[dict]:
        """Discover candidate datasets, not automatically assume they match the experiment."""
        candidates = []
        for term in terms[:3]:
            # Crossref dataset records provide persistent IDs; licensing/fitness still needs validation.
            body = self._get(
                "https://api.crossref.org/works",
                {"query": term, "filter": "type:dataset", "rows": 5},
            )
            for record in body.get("message", {}).get("items", []):
                candidates.append(
                    {
                        "title": " ".join(record.get("title", [])),
                        "doi": record.get("DOI"),
                        "url": record.get("URL"),
                        "licenses": record.get("license", []),
                        "status": "candidate_only",
                        "required_validation": "Access, license, variables, population, split independence and suitability",
                    }
                )
        return candidates
