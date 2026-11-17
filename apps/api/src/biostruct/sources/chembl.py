"""ChEMBL — where the candidate molecules come from.

A screen costs $0.0250 per molecule. That single fact decides the whole shape of
this module: its job is not "fetch molecules", it is **do not spend money on a
molecule that cannot possibly be a hit**. Everything below exists because the
live API will happily hand you something unscreenable, duplicated, or
scientifically wrong for the question being asked.

Observed against the live API on 2026-11-17 (no key, no auth, free):

* **Every number arrives as a string.** ``"standard_value": "21.0"``,
  ``"max_phase": "4.0"``, ``"alogp": "-1.10"``. Sorting those as text ranks
  ``"9.0"`` below ``"150.0"``, which would silently invert a potency ordering,
  so nothing leaves this module as a string that a human will compare.
* **``action_type`` is an object, not a string** — ``{"action_type":
  "INHIBITOR", "description": ..., "parent_type": ...}``. Flattened here.
* **A molecule can exist with no structure at all** (``CHEMBL3545239``,
  PF-05019702, has ``molecule_structures: null``). It cannot be screened; it is
  dropped with a reason rather than sent to a paid endpoint.
* **A requested id can vanish silently.** Asking for five ids returned four,
  with ``total_count: 4`` and no indication of which one was missing. So the
  caller is told which ids did not come back.
* **Similarity search returns salts and stereoisomers at 100%.** A 70% search
  around avibactam returned the sodium salt, two enantiomers and a diastereomer
  — five entries, one compound, $0.125 of screen for one answer. Hence
  ``parent_chembl_id`` and the salt-stripping key in ``library.py``.
* **``limit`` is silently clamped to 1000**, and a free-text search reports
  ``total_count: 10000`` whatever the real number is (a search-engine cap). The
  count is therefore not reported as a fact.
* **Free-text molecule search is poor** for a concept: "beta-lactamase
  inhibitor" returned mostly unnamed compounds. Targets are searched by name;
  molecules are reached *through* a target's measured activities, which is both
  more accurate and cheaper.

Reference: https://chembl.gitbook.io/chembl-interface-documentation/web-services/chembl-data-web-services
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from biostruct.sources.http import CachedHTTPClient, SourceUnavailable

log = logging.getLogger(__name__)

BASE = "https://www.ebi.ac.uk/chembl/api/data"
SOURCE = "chembl"

#: The API clamps ``limit`` to this silently. Clamping here instead means the
#: pager's arithmetic matches what the server actually did.
PAGE_SIZE_MAX = 1000

#: A free-text search never reports more than this, whatever the real total is.
#: Treated as "at least", never as a count.
SEARCH_TOTAL_CAP = 10_000

#: Assay endpoints that express **how tightly a molecule binds**, which is the
#: only kind of number that can rank a candidate inhibitor. Deliberately
#: excludes ``Kcat``, ``Km`` and ``Kcat/Km``: those describe a *substrate* being
#: chewed up by the enzyme, so a molecule known only by them is the opposite of
#: what we are looking for. Also excludes ``MIC`` and ``Inhibition``, which are
#: real data but are not comparable across assays.
POTENCY_TYPES = ("IC50", "Ki", "Kd", "EC50")

#: ``=`` is a measurement. ``<`` means "at least this good", which is still
#: evidence of binding. ``>`` means the assay *failed* to show binding at that
#: concentration — including it would put known non-binders in the library.
BINDING_RELATIONS = ("=", "<", "<=")


def _num(value: Any) -> float | None:
    """Parse one of ChEMBL's stringified numbers.

    This is small and load-bearing: it is the only reason a potency ordering in
    this product is numeric rather than lexicographic.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _action_type(value: Any) -> str:
    """``action_type`` is sometimes a dict and sometimes absent."""
    if isinstance(value, dict):
        return _text(value.get("action_type"))
    return _text(value)


@dataclass(frozen=True, slots=True)
class ChemblMolecule:
    """One compound, with the parts that decide whether it is worth screening."""

    chembl_id: str
    name: str = ""
    smiles: str = ""
    parent_id: str = ""
    molecule_type: str = ""
    #: 4 means an approved drug. Arrives as ``"4.0"``; stored as a number.
    max_phase: float | None = None
    molecular_weight: float | None = None
    alogp: float | None = None
    #: ChEMBL's own drug-likeness score, 0-1. Independent of Boltz's ADME.
    qed: float | None = None
    ro5_violations: int | None = None
    similarity: float | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_screenable(self) -> bool:
        """Can this be sent to a paid screen at all?

        Without a SMILES string there is nothing to submit. Antibodies and
        other biotherapeutics also land here, correctly: a small-molecule screen
        is the wrong instrument for them.
        """
        return bool(self.smiles.strip())

    @property
    def is_approved_drug(self) -> bool:
        return self.max_phase is not None and self.max_phase >= 4

    @property
    def url(self) -> str:
        return f"https://www.ebi.ac.uk/chembl/compound_report_card/{self.chembl_id}/"

    @property
    def label(self) -> str:
        """What a researcher should see. Most ChEMBL entries have no name."""
        return self.name or self.chembl_id


@dataclass(frozen=True, slots=True)
class ChemblTarget:
    """A protein ChEMBL has assay data for.

    This is the bridge into the molecule data: searching targets by name works
    well (``"KPC-2"`` finds ``CHEMBL5437``), whereas searching molecules by
    concept does not.
    """

    target_chembl_id: str
    name: str = ""
    organism: str = ""
    target_type: str = ""
    accessions: tuple[str, ...] = ()

    @property
    def is_single_protein(self) -> bool:
        return self.target_type == "SINGLE PROTEIN"


@dataclass(frozen=True, slots=True)
class Activity:
    """One measured result: this molecule, against this target, this number.

    ``value_nm`` is populated only for a comparable potency measurement in
    nanomolar. Anything else keeps its raw type and value for display but is not
    rankable — see ``POTENCY_TYPES``.
    """

    molecule_chembl_id: str
    smiles: str = ""
    name: str = ""
    standard_type: str = ""
    value_nm: float | None = None
    relation: str = ""
    units: str = ""
    pchembl: float | None = None
    assay_type: str = ""
    action_type: str = ""
    parent_molecule_id: str = ""
    document_year: int | None = None

    @property
    def is_potency(self) -> bool:
        return self.standard_type in POTENCY_TYPES

    @property
    def shows_binding(self) -> bool:
        """Is this evidence that the molecule binds, rather than that it does not?"""
        return (
            self.is_potency
            and self.value_nm is not None
            and self.units == "nM"
            and self.relation in BINDING_RELATIONS
        )

    @property
    def is_substrate_measurement(self) -> bool:
        """``Kcat``/``Km`` mean the enzyme destroys this molecule.

        Worth surfacing rather than hiding: a researcher who sees a known
        substrate in a result list should know why it is not a candidate.
        """
        return self.standard_type in ("Kcat", "Km", "Kcat/Km")

    def potency_note(self) -> str:
        """A human-readable line, with units, never a bare number."""
        if self.value_nm is None:
            return f"{self.standard_type} reported without a comparable value"
        relation = "" if self.relation == "=" else f"{self.relation} "
        return f"{self.standard_type} {relation}{self.value_nm:g} nM"


def parse_molecule(row: dict[str, Any], *, similarity: Any = None) -> ChemblMolecule | None:
    chembl_id = _text(row.get("molecule_chembl_id"))
    if not chembl_id:
        return None
    structures = row.get("molecule_structures")
    smiles = _text(structures.get("canonical_smiles")) if isinstance(structures, dict) else ""
    hierarchy = row.get("molecule_hierarchy")
    parent = (
        _text(hierarchy.get("parent_chembl_id")) if isinstance(hierarchy, dict) else ""
    ) or chembl_id
    props = row.get("molecule_properties")
    props = props if isinstance(props, dict) else {}
    violations = _num(props.get("num_ro5_violations"))
    return ChemblMolecule(
        chembl_id=chembl_id,
        name=_text(row.get("pref_name")),
        smiles=smiles,
        parent_id=parent,
        molecule_type=_text(row.get("molecule_type")),
        max_phase=_num(row.get("max_phase")),
        molecular_weight=_num(props.get("full_mwt")) or _num(props.get("mw_freebase")),
        alogp=_num(props.get("alogp")),
        qed=_num(props.get("qed_weighted")),
        ro5_violations=int(violations) if violations is not None else None,
        similarity=_num(similarity if similarity is not None else row.get("similarity")),
        raw=row,
    )


def parse_activity(row: dict[str, Any]) -> Activity | None:
    molecule_id = _text(row.get("molecule_chembl_id"))
    if not molecule_id:
        return None
    units = _text(row.get("standard_units"))
    standard_type = _text(row.get("standard_type"))
    value = _num(row.get("standard_value"))
    year = _num(row.get("document_year"))
    return Activity(
        molecule_chembl_id=molecule_id,
        smiles=_text(row.get("canonical_smiles")),
        name=_text(row.get("molecule_pref_name")),
        standard_type=standard_type,
        # Only a nanomolar potency becomes a comparable number. A percentage or
        # a per-second rate in this field would be meaningless as "value_nm".
        value_nm=value if (units == "nM" and standard_type in POTENCY_TYPES) else None,
        relation=_text(row.get("standard_relation")),
        units=units,
        pchembl=_num(row.get("pchembl_value")),
        assay_type=_text(row.get("assay_type")),
        action_type=_action_type(row.get("action_type")),
        parent_molecule_id=_text(row.get("parent_molecule_chembl_id")) or molecule_id,
        document_year=int(year) if year is not None else None,
    )


class ChemblClient:
    source = SOURCE

    def __init__(self, http: CachedHTTPClient) -> None:
        self._http = http

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        payload = await self._http.get_json(f"{BASE}/{path}", params=params)
        if not isinstance(payload, dict):
            raise SourceUnavailable(SOURCE, "unexpected response shape")
        return payload

    # ----------------------------------------------------------------- molecules

    async def molecule(self, chembl_id: str) -> ChemblMolecule | None:
        """One compound by id, or ``None`` if ChEMBL does not have it.

        A missing id is a 404 with an empty body, which the shared client turns
        into ``SourceUnavailable``. That is right for a *source* being down and
        wrong here, so it is translated back to a plain absence.
        """
        chembl_id = chembl_id.strip().upper()
        if not chembl_id.startswith("CHEMBL") or not chembl_id[6:].isdigit():
            return None
        try:
            payload = await self._get(f"molecule/{chembl_id}.json", {})
        except SourceUnavailable as exc:
            if "404" in exc.detail:
                return None
            raise
        return parse_molecule(payload)

    async def molecules(self, chembl_ids: list[str]) -> tuple[list[ChemblMolecule], list[str]]:
        """Several compounds in one request.

        Returns ``(found, missing)``. The second half matters: the API returns
        fewer rows than asked for without saying which are absent, so a curated
        library that lost an entry would otherwise just be quietly smaller.
        """
        wanted = [i.strip().upper() for i in chembl_ids if i.strip()]
        if not wanted:
            return [], []
        found: list[ChemblMolecule] = []
        # The id filter goes in one query string, so very long lists are chunked
        # rather than sent as a URL the server will reject.
        for start in range(0, len(wanted), 50):
            chunk = wanted[start : start + 50]
            payload = await self._get(
                "molecule.json",
                {"molecule_chembl_id__in": ",".join(chunk), "limit": len(chunk)},
            )
            for row in payload.get("molecules") or []:
                if isinstance(row, dict) and (molecule := parse_molecule(row)):
                    found.append(molecule)
        seen = {m.chembl_id for m in found}
        return found, [i for i in wanted if i not in seen]

    async def search_molecules(self, query: str, *, limit: int = 25) -> list[ChemblMolecule]:
        """Free-text molecule search.

        Kept for completeness and used sparingly: relevance is weak for concepts
        and the reported total is capped, so this is a last resort behind
        ``actives_against``.
        """
        if not query.strip():
            return []
        payload = await self._get(
            "molecule/search.json",
            {"q": query.strip(), "limit": max(1, min(limit, PAGE_SIZE_MAX))},
        )
        return [
            m
            for row in payload.get("molecules") or []
            if isinstance(row, dict) and (m := parse_molecule(row))
        ]

    async def similar_to(
        self, smiles: str, *, threshold: int = 70, limit: int = 50
    ) -> list[ChemblMolecule]:
        """Compounds structurally like this one.

        The threshold is a percentage and the API rejects anything under 40.
        Results include salts and stereoisomers of the query itself at 100%;
        de-duplication is the caller's job because only the caller knows whether
        a distinct salt matters for its purpose.
        """
        smiles = smiles.strip()
        if not smiles:
            return []
        threshold = max(40, min(threshold, 100))
        payload = await self._get(
            f"similarity/{smiles}/{threshold}.json",
            {"limit": max(1, min(limit, PAGE_SIZE_MAX))},
        )
        return [
            m
            for row in payload.get("molecules") or []
            if isinstance(row, dict) and (m := parse_molecule(row))
        ]

    # ------------------------------------------------------------------- targets

    async def find_targets(
        self, query: str, *, organism: str = "", proteins_only: bool = False, limit: int = 10
    ) -> list[ChemblTarget]:
        """Targets by name.

        **Relevance is unreliable and the first hit is often wrong.** Searching
        ``"KPC-2"`` live on 2026-11-17 returned two KPC enzymes followed by
        *Thromboxane-A synthase*, an unrelated human protein. So nothing in the
        product may take ``[0]`` and screen against it: either the researcher
        confirms the target or ``organism``/``proteins_only`` narrows it, and the
        agent states which target it chose.
        """
        if not query.strip():
            return []
        payload = await self._get(
            "target/search.json", {"q": query.strip(), "limit": max(1, min(limit, 100))}
        )
        out: list[ChemblTarget] = []
        for row in payload.get("targets") or []:
            if not isinstance(row, dict):
                continue
            target_id = _text(row.get("target_chembl_id"))
            if not target_id:
                continue
            accessions: list[str] = []
            for component in row.get("target_components") or []:
                if isinstance(component, dict) and (acc := _text(component.get("accession"))):
                    accessions.append(acc)
            target = ChemblTarget(
                target_chembl_id=target_id,
                name=_text(row.get("pref_name")),
                organism=_text(row.get("organism")),
                target_type=_text(row.get("target_type")),
                accessions=tuple(accessions),
            )
            if proteins_only and not target.is_single_protein:
                continue
            if organism and organism.lower() not in target.organism.lower():
                continue
            out.append(target)
        return out

    # ---------------------------------------------------------------- activities

    async def activities(self, target_chembl_id: str, *, limit: int = 200) -> list[Activity]:
        """Everything ChEMBL has measured against this target.

        Unfiltered on purpose — the caller decides what counts, and a substrate
        measurement is worth *seeing* even though it must not be screened.
        """
        target_chembl_id = target_chembl_id.strip().upper()
        if not target_chembl_id:
            return []
        payload = await self._get(
            "activity.json",
            {
                "target_chembl_id": target_chembl_id,
                "limit": max(1, min(limit, PAGE_SIZE_MAX)),
            },
        )
        return [
            a
            for row in payload.get("activities") or []
            if isinstance(row, dict) and (a := parse_activity(row))
        ]

    async def actives_against(self, target_chembl_id: str, *, limit: int = 200) -> list[Activity]:
        """Only molecules with evidence that they **bind** this target.

        One row per molecule, keeping its strongest measurement, so a compound
        assayed eleven times does not take eleven screening slots. Sorted most
        potent first — numerically, which is only possible because ``_num``
        parsed the strings.
        """
        best: dict[str, Activity] = {}
        for activity in await self.activities(target_chembl_id, limit=limit):
            if not activity.shows_binding or not activity.smiles:
                continue
            key = activity.parent_molecule_id or activity.molecule_chembl_id
            incumbent = best.get(key)
            if incumbent is None or (
                activity.value_nm is not None
                and incumbent.value_nm is not None
                and activity.value_nm < incumbent.value_nm
            ):
                best[key] = activity
        return sorted(best.values(), key=lambda a: a.value_nm if a.value_nm is not None else 1e18)


__all__ = [
    "BASE",
    "BINDING_RELATIONS",
    "POTENCY_TYPES",
    "SEARCH_TOTAL_CAP",
    "Activity",
    "ChemblClient",
    "ChemblMolecule",
    "ChemblTarget",
    "parse_activity",
    "parse_molecule",
]
