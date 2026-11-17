"""Assembling the molecule library — deciding what we spend screening money on.

A screen costs $0.0250 per molecule, so a library is a budget decision dressed
up as a data-fetching problem. Three things have to be true of every library
this module produces, and each of them is a rule enforced in code rather than a
hope about the data:

1. **A positive control is always present.** A known inhibitor of this enzyme
   class goes in every library. If the screen cannot rank a compound that is
   *known* to bind near the top, the run is not telling us anything, and the
   researcher learns that from the same page as the results. The controls are
   shipped in this file, not fetched, so a ChEMBL outage can never silently
   remove the one molecule that validates the run.

2. **The control is chosen per enzyme class.** Avibactam is an excellent
   class A control and a **poor** inhibitor of class B metallo-β-lactamases
   such as NDM — using it there would make a working screen look broken
   (see ``docs/research.md`` §4 and CARD https://card.mcmaster.ca/ontology/36727).
   So ``CURATED`` records which classes each compound actually covers.

3. **Nothing unscreenable or duplicated is paid for.** ChEMBL will hand back
   compounds with no structure, and a similarity search around avibactam
   returns its sodium salt and three stereoisomers at 100% — five slots,
   $0.125, one answer. Every exclusion is reported with a reason instead of
   being dropped quietly, because a library that is quietly smaller than the
   researcher asked for is a library they cannot reason about.

Class assignments below follow the Ambler classification: A and C are serine
hydrolases (KPC, CTX-M, TEM, SHV / AmpC, CMY), B are zinc metallo-enzymes
(NDM, VIM, IMP), D are the OXA serine enzymes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Literal

from biostruct.sources.chembl import Activity, ChemblClient, ChemblMolecule
from biostruct.sources.http import SourceUnavailable

log = logging.getLogger(__name__)

#: Ambler class of the enzyme being screened against. This is the one piece of
#: information that decides which positive control is valid.
TargetClass = Literal["A", "B", "C", "D"]

#: Measured per molecule against the live API on 2026-11-17. Kept here so a
#: library can price itself before anything is submitted.
SCREEN_USD_PER_MOLECULE = Decimal("0.0250")

#: Defaults chosen in money, not in round numbers: 48 molecules is $1.20, which
#: is a cost a researcher can agree to without thinking hard about it.
LIBRARY_SIZE_DEFAULT = 48

#: The ceiling, matching the design-run cap. 2000 molecules is $50.
LIBRARY_SIZE_MAX = 2000

Status = Literal["approved", "trial", "research"]


@dataclass(frozen=True, slots=True)
class CuratedInhibitor:
    """A known β-lactamase inhibitor we ship with the product.

    ``inhibits`` is the honest part. Several of these are excellent against one
    class and useless against another, and a control that does not bind is
    worse than no control: it makes a working instrument look broken.
    """

    chembl_id: str
    name: str
    smiles: str
    inhibits: frozenset[str]
    status: Status
    note: str = ""

    @property
    def label(self) -> str:
        return self.name


#: Verified against the live ChEMBL API on 2026-11-17: every id resolves and
#: every SMILES is the canonical string ChEMBL returns for it. ``test_library``
#: pins the ids so a typo cannot ship a control that is not the compound named.
CURATED: tuple[CuratedInhibitor, ...] = (
    CuratedInhibitor(
        chembl_id="CHEMBL1689063",
        name="Avibactam",
        smiles="NC(=O)[C@@H]1CC[C@@H]2CN1C(=O)N2OS(=O)(=O)O",
        inhibits=frozenset({"A", "C", "D"}),
        status="approved",
        note=(
            "Diazabicyclooctane. The reference control for class A carbapenemases "
            "such as KPC, where ChEMBL records Ki 11 nM against KPC-2. Does not "
            "usefully inhibit class B metallo-enzymes."
        ),
    ),
    CuratedInhibitor(
        chembl_id="CHEMBL3112741",
        name="Relebactam",
        smiles="O=C(NC1CCNCC1)[C@@H]1CC[C@@H]2CN1C(=O)N2OS(=O)(=O)O",
        inhibits=frozenset({"A", "C"}),
        status="approved",
        note="Diazabicyclooctane, paired clinically with imipenem.",
    ),
    CuratedInhibitor(
        chembl_id="CHEMBL4298137",
        name="Durlobactam",
        smiles="CC1=C[C@@H]2CN(C(=O)N2OS(=O)(=O)O)[C@@H]1C(N)=O",
        inhibits=frozenset({"A", "C", "D"}),
        status="approved",
        note="The strongest shipped control for class D OXA enzymes.",
    ),
    CuratedInhibitor(
        chembl_id="CHEMBL3317857",
        name="Vaborbactam",
        smiles="O=C(O)C[C@@H]1CC[C@H](NC(=O)Cc2cccs2)B(O)O1",
        inhibits=frozenset({"A", "C"}),
        status="approved",
        note="Cyclic boronate. Potent against KPC; weak against class D.",
    ),
    CuratedInhibitor(
        chembl_id="CHEMBL404",
        name="Tazobactam",
        smiles="C[C@]1(Cn2ccnn2)[C@H](C(=O)O)N2C(=O)C[C@H]2S1(=O)=O",
        inhibits=frozenset({"A"}),
        status="approved",
        note="First-generation penicillanic sulfone; no carbapenemase coverage.",
    ),
    CuratedInhibitor(
        chembl_id="CHEMBL777",
        name="Clavulanic acid",
        smiles="O=C(O)[C@H]1/C(=C/CO)O[C@@H]2CC(=O)N21",
        inhibits=frozenset({"A"}),
        status="approved",
        note="The original mechanism-based inhibitor, in clinical use since 1984.",
    ),
    CuratedInhibitor(
        chembl_id="CHEMBL403",
        name="Sulbactam",
        smiles="CC1(C)[C@H](C(=O)O)N2C(=O)C[C@H]2S1(=O)=O",
        inhibits=frozenset({"A"}),
        status="approved",
        note="Penicillanic sulfone with intrinsic activity against Acinetobacter.",
    ),
    CuratedInhibitor(
        chembl_id="CHEMBL4594290",
        name="Taniborbactam",
        smiles="NCCNC1CCC(CC(=O)N[C@H]2Cc3cccc(C(=O)O)c3OB2O)CC1",
        inhibits=frozenset({"A", "B", "C", "D"}),
        status="trial",
        note=(
            "Bicyclic boronate, in clinical trials rather than approved. The only "
            "shipped compound with meaningful class B (NDM, VIM) coverage, which "
            "is why it is the class B control despite not being a licensed drug."
        ),
    ),
    CuratedInhibitor(
        chembl_id="CHEMBL4533605",
        name="Zidebactam",
        smiles="O=C(NNC(=O)[C@@H]1CC[C@@H]2CN1C(=O)N2OS(=O)(=O)O)[C@@H]1CCCNC1",
        inhibits=frozenset({"A", "C"}),
        status="trial",
        note="Diazabicyclooctane with its own antibacterial activity.",
    ),
    CuratedInhibitor(
        chembl_id="CHEMBL1560",
        name="Captopril",
        smiles="C[C@H](CS)C(=O)N1CCC[C@H]1C(=O)O",
        inhibits=frozenset({"B"}),
        status="research",
        note=(
            "A thiol that chelates the active-site zinc. Approved as an ACE "
            "inhibitor, never as an antibacterial: it is a laboratory control "
            "for metallo-β-lactamases, not a candidate therapy."
        ),
    ),
)

#: Which compound is used as *the* control for each class, in preference order.
#: Class B is deliberately short, because that is the true state of the field:
#: no metallo-β-lactamase inhibitor is approved anywhere.
CONTROL_ORDER: dict[str, tuple[str, ...]] = {
    "A": ("CHEMBL1689063", "CHEMBL3317857", "CHEMBL3112741"),
    "B": ("CHEMBL4594290", "CHEMBL1560"),
    "C": ("CHEMBL3112741", "CHEMBL1689063", "CHEMBL4298137"),
    "D": ("CHEMBL4298137", "CHEMBL1689063"),
}

BY_ID: dict[str, CuratedInhibitor] = {c.chembl_id: c for c in CURATED}

Provenance = Literal["curated", "chembl-activity", "chembl-similarity", "caller"]


class DropReason:
    """Why a molecule did not make the library. Shown to the researcher."""

    NO_STRUCTURE = "ChEMBL has no chemical structure for this entry, so it cannot be screened"
    DUPLICATE = "the same compound is already in the library under another identifier"
    SALT_OF_ENTRY = "a salt or stereoisomer of a compound already in the library"
    SUBSTRATE_ONLY = (
        "the only measurements against this target describe the enzyme destroying "
        "this molecule, which makes it a substrate rather than a candidate inhibitor"
    )
    OVER_BUDGET = "the library was already at the size that was agreed"


@dataclass(frozen=True, slots=True)
class LibraryEntry:
    """One molecule that will be submitted and paid for."""

    smiles: str
    external_id: str
    name: str
    provenance: Provenance
    note: str = ""
    is_control: bool = False
    #: A measured potency against this exact target, when ChEMBL had one. This
    #: is the number a hit's rank is judged against afterwards.
    known_potency_nm: float | None = None

    @property
    def label(self) -> str:
        return self.name or self.external_id


@dataclass(frozen=True, slots=True)
class Dropped:
    identifier: str
    name: str
    reason: str


@dataclass(frozen=True, slots=True)
class Library:
    """A priced, explicable set of molecules, ready for a screen proposal."""

    target_class: str
    entries: tuple[LibraryEntry, ...] = ()
    dropped: tuple[Dropped, ...] = ()
    #: Sources that failed while assembling. A library built from controls alone
    #: is still a usable library, but the researcher must be told why it is small.
    unavailable: tuple[str, ...] = ()

    @property
    def size(self) -> int:
        return len(self.entries)

    @property
    def controls(self) -> tuple[LibraryEntry, ...]:
        return tuple(e for e in self.entries if e.is_control)

    @property
    def has_control(self) -> bool:
        """The acceptance criterion for this milestone, as a property.

        Nothing in the product may submit a screen whose library fails this.
        """
        return bool(self.controls)

    @property
    def estimated_cost_usd(self) -> Decimal:
        return SCREEN_USD_PER_MOLECULE * self.size

    @property
    def molecules_smiles(self) -> tuple[str, ...]:
        return tuple(e.smiles for e in self.entries)

    def summary(self) -> str:
        """One honest paragraph for the confirmation card, before money is spent."""
        if not self.entries:
            return "No molecules could be assembled for this target."
        controls = ", ".join(c.label for c in self.controls) or "none"
        parts = [
            f"{self.size} molecules, about ${self.estimated_cost_usd:.2f} to screen.",
            f"Positive control: {controls}.",
        ]
        measured = sum(1 for e in self.entries if e.known_potency_nm is not None)
        if measured:
            verb = "has" if measured == 1 else "have"
            parts.append(f"{measured} {verb} a measured potency against this target already.")
        if self.dropped:
            n = len(self.dropped)
            if n == 1:
                parts.append("1 candidate was excluded, with a reason given.")
            else:
                parts.append(f"{n} candidates were excluded, each with a reason.")
        if self.unavailable:
            parts.append(
                f"Could not reach {', '.join(self.unavailable)}, so this library is "
                "smaller than it would otherwise be."
            )
        return " ".join(parts)


def salt_free_key(smiles: str) -> str:
    """A de-duplication key that collapses salts onto their parent.

    SMILES uses ``.`` to separate disconnected components, so a sodium salt is
    ``<drug>.[Na+]``. Keeping the longest component is a **heuristic, not a
    desalting algorithm** — it has no notion of which fragment is the active
    one, and it does not normalise tautomers or charges. It is here to stop the
    specific waste we measured (a similarity search returning one compound five
    times), and ``parent_chembl_id`` does the authoritative work when ChEMBL
    supplies it.
    """
    cleaned = smiles.strip()
    if not cleaned:
        return ""
    if "." in cleaned:
        cleaned = max(cleaned.split("."), key=len)
    return cleaned


def curated_for(target_class: str) -> tuple[CuratedInhibitor, ...]:
    """Everything we ship that actually inhibits this class."""
    return tuple(c for c in CURATED if target_class in c.inhibits)


def control_for(target_class: str) -> CuratedInhibitor | None:
    """The compound whose job is to prove the screen works.

    Falls back to any shipped inhibitor of the class if the preferred one is
    somehow missing, and returns ``None`` only for a class we have nothing for —
    which the caller must surface rather than screen blind.
    """
    for chembl_id in CONTROL_ORDER.get(target_class, ()):
        if (control := BY_ID.get(chembl_id)) is not None:
            return control
    options = curated_for(target_class)
    return options[0] if options else None


@dataclass
class _Accumulator:
    """Collects entries while enforcing uniqueness and the size cap."""

    max_size: int
    entries: list[LibraryEntry] = field(default_factory=list)
    dropped: list[Dropped] = field(default_factory=list)
    _ids: set[str] = field(default_factory=set)
    _keys: set[str] = field(default_factory=set)

    @property
    def full(self) -> bool:
        return len(self.entries) >= self.max_size

    def holds(self, external_id: str) -> bool:
        return external_id in self._ids

    def _absorb(self, duplicate: LibraryEntry, key: str) -> None:
        """Keep a rejected duplicate's measured potency.

        The control goes in first, from the shipped set, which has no assay
        number attached. ChEMBL then reports that the very same compound has a
        measured Ki against this exact target — and that number is the most
        useful fact in the library, because it says where the control *ought* to
        rank. Dropping it with the duplicate row loses it, so it is merged onto
        the entry already held.
        """
        if duplicate.known_potency_nm is None:
            return
        for index, existing in enumerate(self.entries):
            if existing.known_potency_nm is not None:
                continue
            if (
                existing.external_id == duplicate.external_id
                or salt_free_key(existing.smiles) == key
            ):
                self.entries[index] = replace(
                    existing,
                    known_potency_nm=duplicate.known_potency_nm,
                    note=f"{existing.note} {duplicate.note}".strip(),
                )
                return

    def add(self, entry: LibraryEntry, *, force: bool = False) -> bool:
        """``force`` is for controls, which bypass the cap by design.

        A library trimmed to exactly the agreed size minus its control would
        satisfy the budget and defeat the point.
        """
        if not entry.smiles.strip():
            self.dropped.append(Dropped(entry.external_id, entry.name, DropReason.NO_STRUCTURE))
            return False
        key = salt_free_key(entry.smiles)
        if entry.external_id and entry.external_id in self._ids:
            self._absorb(entry, key)
            self.dropped.append(Dropped(entry.external_id, entry.name, DropReason.DUPLICATE))
            return False
        if key in self._keys:
            self._absorb(entry, key)
            self.dropped.append(Dropped(entry.external_id, entry.name, DropReason.SALT_OF_ENTRY))
            return False
        if self.full and not force:
            self.dropped.append(Dropped(entry.external_id, entry.name, DropReason.OVER_BUDGET))
            return False
        self.entries.append(entry)
        if entry.external_id:
            self._ids.add(entry.external_id)
        self._keys.add(key)
        return True


def _entry_from_activity(activity: Activity) -> LibraryEntry:
    note = f"Measured against this target: {activity.potency_note()}"
    if activity.action_type:
        note = f"{note}. ChEMBL labels it a {activity.action_type.lower()}"
    return LibraryEntry(
        smiles=activity.smiles,
        external_id=activity.parent_molecule_id or activity.molecule_chembl_id,
        name=activity.name,
        provenance="chembl-activity",
        note=f"{note}.",
        known_potency_nm=activity.value_nm,
    )


def _entry_from_molecule(molecule: ChemblMolecule, *, similarity: float | None) -> LibraryEntry:
    if similarity is not None:
        note = f"{similarity:g}% structurally similar to the control compound."
    else:
        note = "Retrieved from ChEMBL."
    if molecule.is_approved_drug:
        note = f"{note} Already an approved drug."
    return LibraryEntry(
        smiles=molecule.smiles,
        external_id=molecule.parent_id or molecule.chembl_id,
        name=molecule.name,
        provenance="chembl-similarity",
        note=note,
    )


class LibraryBuilder:
    """Builds a screening library for one target.

    The order of assembly is the order of confidence: the control first, then
    compounds with a *measured* result against this exact target, then shipped
    inhibitors of the class, then structural neighbours of the control. If the
    budget runs out it runs out at the least informative end.
    """

    def __init__(self, chembl: ChemblClient | None = None) -> None:
        self._chembl = chembl

    async def assemble(
        self,
        *,
        target_class: str,
        chembl_target_id: str = "",
        max_size: int = LIBRARY_SIZE_DEFAULT,
        extra_smiles: tuple[tuple[str, str], ...] = (),
        expand_with_similar: bool = False,
    ) -> Library:
        if max_size < 1:
            raise ValueError("a library needs at least one molecule")
        if max_size > LIBRARY_SIZE_MAX:
            raise ValueError(
                f"we cap a screening library at {LIBRARY_SIZE_MAX} molecules "
                f"(about ${LIBRARY_SIZE_MAX * SCREEN_USD_PER_MOLECULE:.0f} per run)"
            )
        target_class = target_class.strip().upper()

        acc = _Accumulator(max_size=max_size)
        unavailable: list[str] = []

        control = control_for(target_class)
        if control is not None:
            acc.add(
                LibraryEntry(
                    smiles=control.smiles,
                    external_id=control.chembl_id,
                    name=control.name,
                    provenance="curated",
                    note=f"Positive control. {control.note}",
                    is_control=True,
                ),
                force=True,
            )
        else:
            log.warning("no shipped control for enzyme class %r", target_class)

        for smiles, label in extra_smiles:
            acc.add(
                LibraryEntry(
                    smiles=smiles,
                    external_id="",
                    name=label,
                    provenance="caller",
                    note="Supplied with the request.",
                )
            )

        if chembl_target_id and self._chembl is not None:
            try:
                for activity in await self._chembl.actives_against(chembl_target_id):
                    if acc.full:
                        break
                    acc.add(_entry_from_activity(activity))
            except SourceUnavailable as exc:
                log.warning("chembl activities unavailable: %s", exc)
                unavailable.append("ChEMBL")

        for inhibitor in curated_for(target_class):
            if acc.full:
                break
            if acc.holds(inhibitor.chembl_id):
                continue
            acc.add(
                LibraryEntry(
                    smiles=inhibitor.smiles,
                    external_id=inhibitor.chembl_id,
                    name=inhibitor.name,
                    provenance="curated",
                    note=inhibitor.note,
                )
            )

        chembl = self._chembl
        if expand_with_similar and control is not None and chembl is not None and not acc.full:
            try:
                neighbours = await chembl.similar_to(control.smiles, threshold=70)
            except SourceUnavailable as exc:
                log.warning("chembl similarity unavailable: %s", exc)
                if "ChEMBL" not in unavailable:
                    unavailable.append("ChEMBL")
                neighbours = []
            for molecule in neighbours:
                if acc.full:
                    break
                acc.add(_entry_from_molecule(molecule, similarity=molecule.similarity))

        return Library(
            target_class=target_class,
            entries=tuple(acc.entries),
            dropped=tuple(acc.dropped),
            unavailable=tuple(unavailable),
        )


__all__ = [
    "CONTROL_ORDER",
    "CURATED",
    "LIBRARY_SIZE_DEFAULT",
    "LIBRARY_SIZE_MAX",
    "SCREEN_USD_PER_MOLECULE",
    "CuratedInhibitor",
    "DropReason",
    "Dropped",
    "Library",
    "LibraryBuilder",
    "LibraryEntry",
    "control_for",
    "curated_for",
    "salt_free_key",
]
