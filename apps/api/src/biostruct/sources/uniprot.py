"""UniProt: protein sequences and biological annotation.

Public, no key. We prefer reviewed (Swiss-Prot) entries by default, because an
unfiltered search drowns a researcher in computationally predicted TrEMBL
entries that look identical in a list but carry far weaker evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from biostruct.sources.http import CachedHTTPClient

BASE = "https://rest.uniprot.org/uniprotkb"

FIELDS = ",".join(
    [
        "accession",
        "id",
        "protein_name",
        "gene_names",
        "organism_name",
        "organism_id",
        "length",
        "sequence",
        "cc_function",
        "cc_subcellular_location",
        "ft_act_site",
        "ft_binding",
        "xref_pdb",
        "reviewed",
        "protein_existence",
    ]
)


@dataclass(frozen=True)
class ActiveSite:
    """An annotated functional residue, in UniProt (1-based) numbering.

    Useful because it is a far better epitope suggestion than a guess, and it
    gives ``ResidueMap.verify_identities`` something real to check against.
    """

    position: int
    description: str


@dataclass(frozen=True)
class ProteinTarget:
    accession: str
    entry_name: str
    protein_name: str
    organism: str
    organism_id: int | None
    length: int
    sequence: str
    reviewed: bool
    function: str = ""
    gene_names: tuple[str, ...] = ()
    pdb_ids: tuple[str, ...] = ()
    active_sites: tuple[ActiveSite, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def evidence_level(self) -> str:
        """Plain words, because "Swiss-Prot" means nothing to most people."""
        return "reviewed (Swiss-Prot)" if self.reviewed else "unreviewed (TrEMBL)"

    @property
    def has_structure(self) -> bool:
        return bool(self.pdb_ids)


def _parse_entry(entry: dict[str, Any]) -> ProteinTarget:
    description = entry.get("proteinDescription") or {}
    recommended = description.get("recommendedName") or {}
    name = (recommended.get("fullName") or {}).get("value")
    if not name:
        submitted = description.get("submissionNames") or []
        if submitted:
            name = (submitted[0].get("fullName") or {}).get("value")
    organism = entry.get("organism") or {}
    sequence = (entry.get("sequence") or {}).get("value", "")

    pdb_ids: list[str] = []
    for xref in entry.get("uniProtKBCrossReferences") or []:
        if xref.get("database") == "PDB" and xref.get("id"):
            pdb_ids.append(str(xref["id"]).upper())

    function = ""
    for comment in entry.get("comments") or []:
        if comment.get("commentType") == "FUNCTION":
            texts = comment.get("texts") or []
            if texts:
                function = texts[0].get("value", "")
                break

    sites: list[ActiveSite] = []
    for feature in entry.get("features") or []:
        if feature.get("type") != "Active site":
            continue
        start = ((feature.get("location") or {}).get("start") or {}).get("value")
        if isinstance(start, int):
            sites.append(ActiveSite(position=start, description=feature.get("description", "")))

    genes = tuple(
        (g.get("geneName") or {}).get("value", "")
        for g in entry.get("genes") or []
        if (g.get("geneName") or {}).get("value")
    )

    return ProteinTarget(
        accession=entry.get("primaryAccession", ""),
        entry_name=entry.get("uniProtkbId", ""),
        protein_name=name or "(unnamed)",
        organism=organism.get("scientificName", ""),
        organism_id=organism.get("taxonId"),
        length=(entry.get("sequence") or {}).get("length", len(sequence)),
        sequence=sequence,
        reviewed=entry.get("entryType", "").startswith("UniProtKB reviewed"),
        function=function,
        gene_names=genes,
        pdb_ids=tuple(pdb_ids),
        active_sites=tuple(sites),
        raw=entry,
    )


class UniProtClient:
    source = "uniprot"

    def __init__(self, http: CachedHTTPClient) -> None:
        self._http = http

    async def search(
        self,
        query: str,
        *,
        organism_id: int | None = None,
        reviewed_only: bool = True,
        limit: int = 25,
    ) -> list[ProteinTarget]:
        clauses = [f"({query})"] if query else []
        if organism_id is not None:
            clauses.append(f"organism_id:{organism_id}")
        if reviewed_only:
            clauses.append("reviewed:true")
        payload = await self._http.get_json(
            f"{BASE}/search",
            params={
                "query": " AND ".join(clauses) or "*",
                "format": "json",
                "size": max(1, min(limit, 100)),
                "fields": FIELDS,
            },
        )
        return [_parse_entry(e) for e in payload.get("results") or []]

    async def get(self, accession: str) -> ProteinTarget:
        payload = await self._http.get_json(
            f"{BASE}/{accession.upper()}", params={"format": "json", "fields": FIELDS}
        )
        return _parse_entry(payload)
