"""RCSB Protein Data Bank: available structures, and the residue mapping.

Two endpoints matter here:

* the **search** API, to find structures for a UniProt accession
* the **polymer entity instance** API, which carries
  ``auth_to_entity_poly_seq_mapping`` — the array that makes author residue
  numbers usable as design indices (see ``residues.py``)

The CIF download URL is also built here, and it is handed to Boltz directly:
verified that ``files.rcsb.org/download/XXXX.cif`` is accepted as a design
target, so no file ever passes through us.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from biostruct.sources.http import CachedHTTPClient, SourceUnavailable
from biostruct.sources.residues import ResidueMap

SEARCH_URL = "https://search.rcsb.org/rcsbsearch/v2/query"
DATA_BASE = "https://data.rcsb.org/rest/v1/core"
FILES_BASE = "https://files.rcsb.org/download"


def cif_url(pdb_id: str) -> str:
    """The URL handed straight to the design engine."""
    return f"{FILES_BASE}/{pdb_id.upper()}.cif"


@dataclass(frozen=True)
class StructureRef:
    pdb_id: str
    title: str = ""
    method: str = ""
    resolution_a: float | None = None
    released: str = ""
    chain_ids: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def cif_url(self) -> str:
        return cif_url(self.pdb_id)

    @property
    def quality_note(self) -> str:
        """A one-line, honest summary for someone choosing between structures."""
        if self.resolution_a is None:
            return f"{self.method or 'unknown method'}, resolution not reported"
        quality = (
            "high resolution"
            if self.resolution_a <= 2.0
            else "moderate resolution"
            if self.resolution_a <= 2.8
            else "low resolution"
        )
        return f"{self.method or 'unknown method'} at {self.resolution_a} A ({quality})"


class RCSBClient:
    source = "rcsb"

    def __init__(self, http: CachedHTTPClient) -> None:
        self._http = http

    async def structures_for_uniprot(self, accession: str, *, limit: int = 25) -> list[str]:
        """PDB entry ids whose polymer entities reference this UniProt accession.

        Sorted by resolution ascending, so the best-resolved structures come
        first — which is what someone picking a design target wants.
        """
        query = {
            "query": {
                "type": "group",
                "logical_operator": "and",
                "nodes": [
                    {
                        "type": "terminal",
                        "service": "text",
                        "parameters": {
                            "attribute": (
                                "rcsb_polymer_entity_container_identifiers"
                                ".reference_sequence_identifiers.database_accession"
                            ),
                            "operator": "exact_match",
                            "value": accession.upper(),
                        },
                    },
                    {
                        "type": "terminal",
                        "service": "text",
                        "parameters": {
                            "attribute": (
                                "rcsb_polymer_entity_container_identifiers"
                                ".reference_sequence_identifiers.database_name"
                            ),
                            "operator": "exact_match",
                            "value": "UniProt",
                        },
                    },
                ],
            },
            "return_type": "entry",
            "request_options": {
                "paginate": {"start": 0, "rows": max(1, min(limit, 100))},
                "sort": [{"sort_by": "rcsb_entry_info.resolution_combined", "direction": "asc"}],
            },
        }
        try:
            payload = await self._http.post_json(SEARCH_URL, query)
        except SourceUnavailable as exc:
            # RCSB answers "no hits" with a 404, which is not an outage.
            if "404" in exc.detail:
                return []
            raise
        return [hit["identifier"].upper() for hit in payload.get("result_set") or []]

    async def get_structure(self, pdb_id: str) -> StructureRef:
        payload = await self._http.get_json(f"{DATA_BASE}/entry/{pdb_id.upper()}")
        info = payload.get("rcsb_entry_info") or {}
        resolutions = info.get("resolution_combined") or []
        methods = [m.get("method", "") for m in payload.get("exptl") or []]
        ids = payload.get("rcsb_entry_container_identifiers") or {}
        return StructureRef(
            pdb_id=pdb_id.upper(),
            title=(payload.get("struct") or {}).get("title", ""),
            method=methods[0] if methods else "",
            resolution_a=float(resolutions[0]) if resolutions else None,
            released=(payload.get("rcsb_accession_info") or {}).get("initial_release_date", ""),
            chain_ids=tuple(ids.get("polymer_entity_ids") or ()),
            raw=payload,
        )

    async def get_residue_map(self, pdb_id: str, chain_id: str) -> ResidueMap:
        """Build the author-number to index mapping for one chain.

        Needs two calls: the instance record carries the author numbering, and
        the entity record carries the one-letter sequence. Both are required —
        without the sequence we could not verify a mapping against expected
        residue identities, which is the only way to *prove* it is right.
        """
        instance = await self._http.get_json(
            f"{DATA_BASE}/polymer_entity_instance/{pdb_id.upper()}/{chain_id}"
        )
        ids = instance.get("rcsb_polymer_entity_instance_container_identifiers") or {}
        mapping = ids.get("auth_to_entity_poly_seq_mapping")
        entity_id = ids.get("entity_id")
        if not mapping or not entity_id:
            raise SourceUnavailable(
                self.source,
                f"{pdb_id} chain {chain_id}: no residue mapping published, so author "
                "residue numbers cannot be converted safely",
            )
        entity = await self._http.get_json(
            f"{DATA_BASE}/polymer_entity/{pdb_id.upper()}/{entity_id}"
        )
        sequence = (entity.get("entity_poly") or {}).get("pdbx_seq_one_letter_code_can", "")
        if not sequence:
            raise SourceUnavailable(
                self.source, f"{pdb_id} entity {entity_id}: no canonical sequence published"
            )
        return ResidueMap.from_rcsb(
            pdb_id=pdb_id,
            chain_id=chain_id,
            sequence=sequence,
            auth_to_entity_poly_seq_mapping=list(mapping),
        )

    async def polymer_chain_ids(self, pdb_id: str) -> list[str]:
        """Author chain ids for the entry's polymer entities."""
        payload = await self._http.get_json(f"{DATA_BASE}/entry/{pdb_id.upper()}")
        ids = payload.get("rcsb_entry_container_identifiers") or {}
        chains: list[str] = []
        for entity_id in ids.get("polymer_entity_ids") or []:
            entity = await self._http.get_json(
                f"{DATA_BASE}/polymer_entity/{pdb_id.upper()}/{entity_id}"
            )
            container = entity.get("rcsb_polymer_entity_container_identifiers") or {}
            chains.extend(str(c) for c in container.get("auth_asym_ids") or ())
        # Preserve order but drop duplicates.
        return list(dict.fromkeys(chains))
