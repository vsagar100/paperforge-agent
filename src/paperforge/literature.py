from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import quote

import httpx

from paperforge.config import LiteratureConfig
from paperforge.domain import EvidenceItem, ReferenceRecord, utc_now

DOI_PATTERN = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)


class LiteratureError(RuntimeError):
    pass


@dataclass(slots=True)
class DiscoveryReport:
    references: list[ReferenceRecord] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    discovered: int = 0
    deduplicated: int = 0
    verified: int = 0
    warnings: list[str] = field(default_factory=list)


class OpenAlexClient:
    def __init__(
        self,
        config: LiteratureConfig,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        email = os.getenv(config.contact_email_env, "").strip()
        user_agent = "PaperForge/1.0"
        if email:
            user_agent += f" (mailto:{email})"
        self.client = httpx.Client(
            base_url=config.endpoint.rstrip("/"),
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=60,
            transport=transport,
        )

    def search(self, query: str) -> list[ReferenceRecord]:
        params: dict[str, str | int] = {
            "search": query,
            "per-page": self.config.results_per_query,
            "select": (
                "id,doi,title,display_name,publication_year,authorships,primary_location,"
                "best_oa_location,open_access,cited_by_count,abstract_inverted_index,type,"
                "is_retracted,biblio"
            ),
        }
        if api_key := os.getenv(self.config.api_key_env, "").strip():
            params["api_key"] = api_key
        try:
            response = self.client.get("/works", params=params)
        except httpx.RequestError as exc:
            raise LiteratureError(f"OpenAlex connection failed: {exc}") from exc
        if not response.is_success:
            raise LiteratureError(
                f"OpenAlex search failed with HTTP {response.status_code}: "
                f"{' '.join(response.text.split())[:500]}"
            )
        try:
            results = response.json().get("results", [])
        except (ValueError, AttributeError) as exc:
            raise LiteratureError("OpenAlex returned invalid JSON") from exc
        references: list[ReferenceRecord] = []
        for rank, work in enumerate(results, start=1):
            if not isinstance(work, dict):
                continue
            reference = self._parse_work(work, query, rank)
            if reference is not None:
                references.append(reference)
        return references

    @staticmethod
    def _parse_work(work: dict[str, Any], query: str, rank: int) -> ReferenceRecord | None:
        title = str(work.get("title") or work.get("display_name") or "").strip()
        if not title or work.get("is_retracted"):
            return None
        authors = [
            str(authorship.get("author", {}).get("display_name", "")).strip()
            for authorship in work.get("authorships") or []
            if isinstance(authorship, dict)
            and str(authorship.get("author", {}).get("display_name", "")).strip()
        ]
        primary_location = work.get("primary_location") or {}
        source = primary_location.get("source") or {}
        best_oa = work.get("best_oa_location") or {}
        open_access = work.get("open_access") or {}
        abstract = _decode_abstract(work.get("abstract_inverted_index"))
        biblio = work.get("biblio") or {}
        first_page = str(biblio.get("first_page") or "").strip()
        last_page = str(biblio.get("last_page") or "").strip()
        pages = (
            f"{first_page}-{last_page}"
            if first_page and last_page and last_page != first_page
            else first_page or last_page or None
        )
        year = work.get("publication_year")
        valid_metadata = bool(authors and isinstance(year, int))
        return ReferenceRecord(
            id="REF000",
            title=title,
            authors=authors,
            year=year if isinstance(year, int) else None,
            venue=str(source.get("display_name") or "").strip() or None,
            volume=str(biblio.get("volume") or "").strip() or None,
            issue=str(biblio.get("issue") or "").strip() or None,
            pages=pages,
            doi=work.get("doi"),
            url=primary_location.get("landing_page_url") or work.get("doi") or work.get("id"),
            openalex_id=work.get("id"),
            abstract=abstract,
            cited_by_count=int(work.get("cited_by_count") or 0),
            publication_type=work.get("type"),
            open_access_url=(
                best_oa.get("pdf_url")
                or best_oa.get("landing_page_url")
                or open_access.get("oa_url")
            ),
            verified=valid_metadata,
            verification_sources=["openalex"] if valid_metadata else [],
            retracted=False,
            metadata={"query": query, "matched_queries": [query], "query_rank": rank},
        )

    def close(self) -> None:
        self.client.close()


class CrossrefClient:
    def __init__(
        self,
        config: LiteratureConfig,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        email = os.getenv(config.contact_email_env, "").strip()
        user_agent = "PaperForge/1.0"
        if email:
            user_agent += f" (mailto:{email})"
        self.client = httpx.Client(
            base_url=config.crossref_endpoint.rstrip("/"),
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=45,
            transport=transport,
        )

    def verify_doi(self, doi: str, expected_title: str | None = None) -> ReferenceRecord | None:
        clean_doi = _normalize_doi(doi)
        try:
            response = self.client.get(f"/works/{quote(clean_doi, safe='')}")
        except httpx.RequestError as exc:
            raise LiteratureError(f"Crossref connection failed: {exc}") from exc
        if response.status_code == 404:
            return None
        if not response.is_success:
            raise LiteratureError(
                f"Crossref DOI lookup failed with HTTP {response.status_code}: "
                f"{' '.join(response.text.split())[:500]}"
            )
        try:
            message = response.json()["message"]
        except (ValueError, KeyError, TypeError) as exc:
            raise LiteratureError("Crossref returned invalid DOI metadata") from exc
        titles = message.get("title") or []
        title = str(titles[0]).strip() if titles else ""
        if not title:
            return None
        if expected_title and _title_similarity(expected_title, title) < 0.72:
            return None
        authors = [
            " ".join(
                part
                for part in (
                    str(author.get("given") or "").strip(),
                    str(author.get("family") or "").strip(),
                )
                if part
            )
            for author in message.get("author") or []
        ]
        year = _crossref_year(message)
        containers = message.get("container-title") or []
        return ReferenceRecord(
            id="REF000",
            title=title,
            authors=[author for author in authors if author],
            year=year,
            venue=str(containers[0]).strip() if containers else None,
            volume=str(message.get("volume") or "").strip() or None,
            issue=str(message.get("issue") or "").strip() or None,
            pages=str(message.get("page") or message.get("article-number") or "").strip() or None,
            publisher=str(message.get("publisher") or "").strip() or None,
            doi=clean_doi,
            url=message.get("URL") or f"https://doi.org/{clean_doi}",
            abstract=_strip_jats(message.get("abstract")),
            publication_type=message.get("type"),
            cited_by_count=int(message.get("is-referenced-by-count") or 0),
            verified=True,
            verification_sources=["crossref"],
            metadata={"crossref_prefix": message.get("prefix")},
        )

    def close(self) -> None:
        self.client.close()


class LiteratureService:
    def __init__(
        self,
        config: LiteratureConfig,
        openalex: OpenAlexClient | None = None,
        crossref: CrossrefClient | None = None,
    ) -> None:
        self.config = config
        self.openalex = openalex or OpenAlexClient(config)
        self.crossref = crossref or CrossrefClient(config)
        self._crossref_available = True

    def collect(self, queries: list[str], evidence: list[EvidenceItem]) -> DiscoveryReport:
        report = DiscoveryReport(
            queries=list(dict.fromkeys(query.strip() for query in queries if query.strip()))
        )
        candidates: list[ReferenceRecord] = []
        if self.config.enabled:
            for query in report.queries:
                try:
                    candidates.extend(self.openalex.search(query))
                except LiteratureError as exc:
                    report.warnings.append(str(exc))
        candidates.extend(self._references_from_local_dois(evidence, report))
        allowed_types = set(self.config.include_types) | {"journal-article"}
        candidates = [
            item
            for item in candidates
            if not item.publication_type or item.publication_type in allowed_types
        ]
        report.discovered = len(candidates)
        deduplicated = self._deduplicate(candidates)
        report.deduplicated = len(deduplicated)
        ranked = sorted(deduplicated, key=self._rank_score, reverse=True)[: self.config.max_sources]
        selected = self._diverse_select(ranked, report.queries, self.config.target_sources)
        if self.config.verify_dois_with_crossref:
            selected = [self._crossref_enrich(item, report) for item in selected]
        selected = [item for item in selected if not item.retracted]
        for index, item in enumerate(selected, start=1):
            item.id = f"REF{index:03d}"
            item.relevance_score = self._rank_score(item)
        report.references = selected
        report.verified = sum(item.verified for item in selected)
        abstract_fraction = (
            sum(bool(item.abstract) for item in selected) / len(selected) if selected else 0.0
        )
        if abstract_fraction < self.config.require_abstract_fraction:
            report.warnings.append(
                f"Only {abstract_fraction:.0%} of selected sources have abstracts; "
                "source-level synthesis will explicitly mark unavailable details."
            )
        if len(selected) < self.config.min_sources:
            report.warnings.append(
                f"Only {len(selected)} usable scholarly sources were found; "
                f"the configured minimum is {self.config.min_sources}."
            )
        return report

    def _references_from_local_dois(
        self, evidence: list[EvidenceItem], report: DiscoveryReport
    ) -> list[ReferenceRecord]:
        dois = {
            _normalize_doi(match.group())
            for item in evidence
            for match in DOI_PATTERN.finditer(item.content)
        }
        references: list[ReferenceRecord] = []
        for doi in sorted(dois):
            try:
                if item := self.crossref.verify_doi(doi):
                    item.metadata["discovered_from_user_evidence"] = True
                    references.append(item)
            except LiteratureError as exc:
                report.warnings.append(str(exc))
                break
        return references

    def _crossref_enrich(
        self, reference: ReferenceRecord, report: DiscoveryReport
    ) -> ReferenceRecord:
        if not reference.doi or not self._crossref_available:
            return reference
        try:
            verified = self.crossref.verify_doi(reference.doi, reference.title)
        except LiteratureError as exc:
            report.warnings.append(str(exc))
            self._crossref_available = False
            return reference
        if verified is None:
            return reference
        reference.title = verified.title
        reference.authors = verified.authors or reference.authors
        reference.year = verified.year or reference.year
        reference.venue = verified.venue or reference.venue
        reference.volume = verified.volume or reference.volume
        reference.issue = verified.issue or reference.issue
        reference.pages = verified.pages or reference.pages
        reference.publisher = verified.publisher or reference.publisher
        reference.url = verified.url or reference.url
        reference.abstract = reference.abstract or verified.abstract
        reference.cited_by_count = max(reference.cited_by_count, verified.cited_by_count)
        reference.verified = True
        reference.verification_sources = list(
            dict.fromkeys(reference.verification_sources + ["crossref"])
        )
        return reference

    @staticmethod
    def _deduplicate(items: list[ReferenceRecord]) -> list[ReferenceRecord]:
        selected: dict[str, ReferenceRecord] = {}
        for item in items:
            if item.retracted:
                continue
            key = LiteratureService._reference_key(item)
            existing = selected.get(key)
            if existing is None:
                selected[key] = item
                continue
            matched_queries = list(
                dict.fromkeys(
                    [
                        *(existing.metadata.get("matched_queries") or []),
                        *(item.metadata.get("matched_queries") or []),
                    ]
                )
            )
            best_rank = min(
                int(existing.metadata.get("query_rank") or 100),
                int(item.metadata.get("query_rank") or 100),
            )
            if LiteratureService._rank_score(item) > LiteratureService._rank_score(existing):
                selected[key] = item
            selected[key].metadata["matched_queries"] = matched_queries
            selected[key].metadata["query_rank"] = best_rank
        return list(selected.values())

    @staticmethod
    def _diverse_select(
        ranked: list[ReferenceRecord], queries: list[str], target: int
    ) -> list[ReferenceRecord]:
        selected: list[ReferenceRecord] = []
        selected_keys: set[str] = set()
        query_groups = {
            query: [
                item
                for item in ranked
                if query in (item.metadata.get("matched_queries") or [item.metadata.get("query")])
            ]
            for query in queries
        }
        while len(selected) < target:
            added = False
            for query in queries:
                while query_groups[query]:
                    candidate = query_groups[query].pop(0)
                    key = LiteratureService._reference_key(candidate)
                    if key in selected_keys:
                        continue
                    selected.append(candidate)
                    selected_keys.add(key)
                    added = True
                    break
                if len(selected) >= target:
                    break
            if not added:
                break
        for candidate in ranked:
            if len(selected) >= target:
                break
            key = LiteratureService._reference_key(candidate)
            if key not in selected_keys:
                selected.append(candidate)
                selected_keys.add(key)
        return selected

    @staticmethod
    def _reference_key(item: ReferenceRecord) -> str:
        if item.doi:
            return f"doi:{item.doi}"
        if item.openalex_id:
            return f"oa:{item.openalex_id}"
        return f"title:{_normalize_title(item.title)}"

    @staticmethod
    def _rank_score(item: ReferenceRecord) -> float:
        rank = int(item.metadata.get("query_rank") or 100)
        relevance = max(0.0, 1.0 - (rank - 1) / 100)
        citation_component = min(0.35, math.log1p(item.cited_by_count) / 30)
        abstract_bonus = 0.12 if item.abstract else 0.0
        verification_bonus = 0.12 if item.verified else 0.0
        recency_bonus = 0.0
        if item.year:
            age = max(0, utc_now().year - item.year)
            recency_bonus = max(0.0, 0.12 - min(age, 12) * 0.01)
        return relevance + citation_component + abstract_bonus + verification_bonus + recency_bonus

    def close(self) -> None:
        self.openalex.close()
        self.crossref.close()


def _decode_abstract(index: Any) -> str | None:
    if not isinstance(index, dict) or not index:
        return None
    positioned: list[tuple[int, str]] = []
    for word, positions in index.items():
        if not isinstance(positions, list):
            continue
        positioned.extend((int(position), str(word)) for position in positions)
    if not positioned:
        return None
    return " ".join(word for _, word in sorted(positioned))


def _normalize_doi(value: str) -> str:
    clean = value.strip().lower()
    clean = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", clean)
    return clean.rstrip(".,; ")


def _normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _title_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, _normalize_title(left), _normalize_title(right)).ratio()


def _crossref_year(message: dict[str, Any]) -> int | None:
    for date_field in ("published-print", "published-online", "issued", "created"):
        date = message.get(date_field) or {}
        parts = date.get("date-parts") or []
        if parts and parts[0] and isinstance(parts[0][0], int):
            return parts[0][0]
    return None


def _strip_jats(value: Any) -> str | None:
    if not value:
        return None
    text = re.sub(r"<[^>]+>", " ", str(value))
    return " ".join(text.split()) or None
