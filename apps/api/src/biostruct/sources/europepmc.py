"""Europe PMC — the literature the research agent is allowed to cite.

Chosen over PubMed for the agent's *research* turns because it is biomedical,
free, needs no key, carries open-access full text, and — the part that matters
here — can answer the one question a citation verifier must ask:

    does this identifier exist?

``EXT_ID:<id> AND SRC:<source>`` returns exactly one record for a real paper
and **zero** for one that was invented. Verified against the live API on
2026-11-17. That is the primitive the guardrail in ``agent/citations.py`` is
built on, and it is why a fabricated reference is structurally impossible in
this product rather than merely discouraged.

PubMed (``pubmed.py``) stays as the evidence source attached to a *target*, so
that existing panels are unchanged.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from biostruct.sources.http import CachedHTTPClient, SourceUnavailable

log = logging.getLogger(__name__)

BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"
SOURCE = "europepmc"

#: Europe PMC caps page size; asking for more is silently clamped, so clamp
#: here where it can be reasoned about.
PAGE_SIZE_MAX = 100

#: ``source`` values we accept back. MED is PubMed/MEDLINE, PMC is PubMed
#: Central, PPR is a preprint. A preprint is still citable but is *not* peer
#: reviewed, and the interface has to say so.
PREPRINT_SOURCE = "PPR"

_TAGS = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    """Titles arrive with HTML entities and inline markup.

    Observed: ``Carbapenemases in Carbapenem-Resistant &lt;i&gt;Klebsiella&lt;/i&gt;``.
    Rendered raw, that is what a researcher would read in a citation list.
    """
    return _TAGS.sub("", html.unescape(text or "")).strip()


@dataclass(frozen=True, slots=True)
class Paper:
    """One citable record.

    ``source`` and ``external_id`` together are the identity, because Europe
    PMC ids are only unique within a source. They are also exactly what the
    verifier re-resolves.
    """

    source: str
    external_id: str
    title: str
    authors: str = ""
    journal: str = ""
    year: str = ""
    doi: str = ""
    pmid: str = ""
    pmcid: str = ""
    abstract: str = ""
    is_open_access: bool = False
    cited_by: int = 0
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def key(self) -> str:
        """Stable handle the agent uses to cite this paper: ``MED:12345678``."""
        return f"{self.source}:{self.external_id}"

    @property
    def is_preprint(self) -> bool:
        return self.source == PREPRINT_SOURCE

    @property
    def url(self) -> str:
        return f"https://europepmc.org/article/{self.source}/{self.external_id}"

    def citation(self) -> str:
        """A human-readable reference line."""
        bits = [self.authors, self.title, self.journal, self.year]
        return ". ".join(b for b in bits if b)


def _parse(row: dict[str, Any]) -> Paper | None:
    source = str(row.get("source") or "").strip()
    external_id = str(row.get("id") or "").strip()
    if not source or not external_id:
        # Without both halves of the identity there is nothing to verify
        # later, so the record is useless to us even if it looks fine.
        return None
    journal = ""
    info = row.get("journalInfo")
    if isinstance(info, dict):
        journal_meta = info.get("journal")
        if isinstance(journal_meta, dict):
            journal = str(journal_meta.get("title") or "")
    return Paper(
        source=source,
        external_id=external_id,
        title=_clean(str(row.get("title") or "")),
        authors=_clean(str(row.get("authorString") or "")),
        journal=_clean(journal or str(row.get("journalTitle") or "")),
        year=str(row.get("pubYear") or ""),
        doi=str(row.get("doi") or ""),
        pmid=str(row.get("pmid") or ""),
        pmcid=str(row.get("pmcid") or ""),
        abstract=_clean(str(row.get("abstractText") or "")),
        # The API sends "Y"/"N" strings, not booleans.
        is_open_access=str(row.get("isOpenAccess") or "").upper() == "Y",
        cited_by=int(row.get("citedByCount") or 0),
        raw=row,
    )


class EuropePMCClient:
    source = SOURCE

    def __init__(self, http: CachedHTTPClient) -> None:
        self._http = http

    async def _search(self, query: str, *, limit: int) -> list[Paper]:
        payload = await self._http.get_json(
            f"{BASE}/search",
            params={
                "query": query,
                "format": "json",
                "resultType": "core",
                "pageSize": max(1, min(limit, PAGE_SIZE_MAX)),
                "cursorMark": "*",
            },
        )
        if not isinstance(payload, dict):
            raise SourceUnavailable(SOURCE, "unexpected response shape")
        rows = (payload.get("resultList") or {}).get("result") or []
        papers = [p for row in rows if isinstance(row, dict) and (p := _parse(row))]
        return papers

    async def search(self, query: str, *, limit: int = 10) -> list[Paper]:
        """Free-text search over the biomedical literature."""
        if not query.strip():
            return []
        return await self._search(query, limit=limit)

    async def resolve(self, source: str, external_id: str) -> Paper | None:
        """Does this exact record exist?

        **This is the guardrail's primitive.** A real identifier returns one
        record; an invented one returns none. Both are a 200, so the caller
        must branch on the result rather than on the status code.

        Only alphanumeric ids are looked up: anything else cannot be a Europe
        PMC identifier, and passing it through would splice caller-controlled
        text into a query string.
        """
        source = source.strip().upper()
        external_id = external_id.strip()
        if not source.isalpha() or not external_id.isalnum():
            return None
        found = await self._search(f"EXT_ID:{external_id} AND SRC:{source}", limit=1)
        for paper in found:
            if paper.source == source and paper.external_id == external_id:
                return paper
        return None

    async def search_for_resistance(
        self, enzyme: str, organism: str = "", *, limit: int = 10
    ) -> list[Paper]:
        """Literature on a specific resistance mechanism.

        Quoted so a multi-word enzyme name is one phrase rather than a bag of
        words — ``KPC carbapenemase`` should not match a paper that says
        "carbapenemase" and happens to mention KPC elsewhere.
        """
        parts = [f'"{enzyme}"'] if enzyme.strip() else []
        if organism.strip():
            parts.append(f'"{organism}"')
        if not parts:
            return []
        return await self.search(" AND ".join(parts), limit=limit)
