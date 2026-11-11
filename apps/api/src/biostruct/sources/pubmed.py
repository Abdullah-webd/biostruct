"""PubMed literature, via NCBI E-utilities.

NCBI's rate limit is the binding constraint: **3 requests/second without an API
key**, 10 with one, and they ask that clients identify themselves with `tool`
and `email` parameters. Exceeding it gets you blocked rather than throttled, so
the token bucket is set below the limit and results are cached for a day.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from biostruct.sources.http import CachedHTTPClient

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TOOL = "biostruct"


@dataclass(frozen=True)
class Reference:
    pmid: str
    title: str
    journal: str = ""
    pubdate: str = ""
    doi: str = ""
    authors: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def url(self) -> str:
        return f"https://pubmed.ncbi.nlm.nih.gov/{self.pmid}/"

    @property
    def citation(self) -> str:
        """A short human citation, for a report's reference list."""
        lead = self.authors[0] if self.authors else ""
        et_al = " et al." if len(self.authors) > 1 else ""
        bits = [b for b in (f"{lead}{et_al}" if lead else "", self.journal, self.pubdate) if b]
        return f"{self.title} ({'; '.join(bits)})" if bits else self.title


def _parse_summary(pmid: str, doc: dict[str, Any]) -> Reference:
    doi = ""
    for article_id in doc.get("articleids") or []:
        if article_id.get("idtype") == "doi":
            doi = str(article_id.get("value", ""))
            break
    authors = tuple(str(a.get("name", "")) for a in doc.get("authors") or [] if a.get("name"))
    return Reference(
        pmid=pmid,
        title=str(doc.get("title", "")).rstrip("."),
        journal=str(doc.get("fulljournalname") or doc.get("source") or ""),
        pubdate=str(doc.get("pubdate", "")),
        doi=doi,
        authors=authors,
        raw=doc,
    )


class PubMedClient:
    source = "pubmed"

    def __init__(self, http: CachedHTTPClient, *, email: str = "", api_key: str = "") -> None:
        self._http = http
        self._email = email
        self._api_key = api_key

    def _common(self) -> dict[str, str]:
        params = {"tool": TOOL, "retmode": "json"}
        # NCBI asks for a contact address. It is the operator's, configured by
        # the deployment, never a platform user's.
        if self._email:
            params["email"] = self._email
        if self._api_key:
            params["api_key"] = self._api_key
        return params

    async def search(self, term: str, *, limit: int = 10) -> list[Reference]:
        found = await self._http.get_json(
            f"{BASE}/esearch.fcgi",
            params={
                **self._common(),
                "db": "pubmed",
                "term": term,
                "retmax": max(1, min(limit, 50)),
                "sort": "relevance",
            },
        )
        pmids = ((found.get("esearchresult") or {}).get("idlist")) or []
        if not pmids:
            return []
        summaries = await self._http.get_json(
            f"{BASE}/esummary.fcgi",
            params={**self._common(), "db": "pubmed", "id": ",".join(pmids)},
        )
        result = summaries.get("result") or {}
        # Preserve relevance order from esearch; esummary's dict order is not it.
        return [_parse_summary(p, result[p]) for p in pmids if p in result]

    async def search_for_target(
        self, protein_name: str, organism: str, *, limit: int = 10
    ) -> list[Reference]:
        """Literature for a specific protein in a specific organism."""
        parts = [f'"{protein_name}"[Title/Abstract]']
        if organism:
            parts.append(f'"{organism}"[Title/Abstract]')
        return await self.search(" AND ".join(parts), limit=limit)
