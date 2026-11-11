"""Mapping author residue numbers to the 0-based indices the design API wants.

This is the most dangerous conversion in the product, so it gets its own module
and refuses to guess.

A researcher names residues the way the literature does: "the catalytic serine
is Ser70". That is the *author* numbering in a PDB file. The Boltz design API
wants **0-based indices into the modelled polymer sequence** of a chain
(docs/boltz-api-observed.md). Those are not the same number, and the difference
is not a constant offset.

Measured on PDB 2OV5 chain A (KPC-2), the project's reference target:

* 261 residues are modelled, out of 293 in the UniProt entry
* author numbering runs 30 to 292, and is **not contiguous** — it jumps 57 to 59
  and 252 to 254, because class A beta-lactamases use Ambler numbering
* so ``index = author - 30`` is right for Ser70 by luck and wrong for everything
  after the first gap: Lys73 would land on Gly, Glu166 on Leu
* passing the author number straight through as an index puts Ser70 on Ala101

None of those mistakes raises an error anywhere. The run completes, costs money
and produces designs aimed at the wrong surface. Hence this module, and hence
``verify_identities``: when the caller knows which amino acid it expects, we can
*prove* the mapping is right rather than trusting it.

**There are three numbering systems in play, not two:**

======================  ==========================================================
UniProt position        1-based over the full sequence, contiguous. Where UniProt's
                        curated active-site annotations live.
PDB author number       Ambler numbering for this family: has gaps, does not start
                        at 1. What the literature cites and what PDB files use.
Design index            0-based into the modelled chain. What the design API wants.
======================  ==========================================================

For Q9F663 / 2OV5 these genuinely differ. UniProt annotates the catalytic
nucleophile at position **69**; the literature and the PDB call it **Ser70**.
Both are the same residue. The +1 appears only after the author numbering's gap
at 58, so it is not a constant offset either.

Feeding a UniProt position in as an author number would therefore select Cys
instead of the catalytic Ser. :meth:`ResidueMap.index_from_uniprot_position`
exists to stop exactly that, and it verifies the residue identity as it goes.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# Three-letter to one-letter, for validating against literature that cites
# residues as "Ser70" rather than "S70".
THREE_TO_ONE = {
    "ALA": "A",
    "ARG": "R",
    "ASN": "N",
    "ASP": "D",
    "CYS": "C",
    "GLN": "Q",
    "GLU": "E",
    "GLY": "G",
    "HIS": "H",
    "ILE": "I",
    "LEU": "L",
    "LYS": "K",
    "MET": "M",
    "PHE": "F",
    "PRO": "P",
    "SER": "S",
    "THR": "T",
    "TRP": "W",
    "TYR": "Y",
    "VAL": "V",
}


class ResidueMappingError(ValueError):
    """A residue could not be mapped, and guessing would be unsafe."""


@dataclass(frozen=True)
class ResidueMap:
    """The author-number to sequence-index mapping for one chain.

    Built from RCSB's ``auth_to_entity_poly_seq_mapping``, which is an explicit
    array precisely because the relationship is not arithmetic: element *i*
    holds the author number of the residue at 0-based index *i*.
    """

    pdb_id: str
    chain_id: str
    sequence: str
    author_numbers: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.author_numbers) != len(self.sequence):
            raise ResidueMappingError(
                f"{self.pdb_id} chain {self.chain_id}: {len(self.author_numbers)} author "
                f"numbers for {len(self.sequence)} sequence positions. Refusing to map, "
                "because a partial mapping would silently mis-place residues."
            )

    # --- construction ------------------------------------------------------

    @classmethod
    def from_rcsb(
        cls,
        *,
        pdb_id: str,
        chain_id: str,
        sequence: str,
        auth_to_entity_poly_seq_mapping: list[Any],
    ) -> ResidueMap:
        return cls(
            pdb_id=pdb_id.upper(),
            chain_id=chain_id,
            sequence=sequence.replace("\n", "").strip(),
            author_numbers=tuple(str(x) for x in auth_to_entity_poly_seq_mapping),
        )

    # --- properties --------------------------------------------------------

    @property
    def _index_by_author(self) -> dict[str, int]:
        # Built on demand; the map is frozen and small (hundreds of entries).
        return {author: i for i, author in enumerate(self.author_numbers)}

    @property
    def length(self) -> int:
        return len(self.sequence)

    @property
    def author_range(self) -> tuple[str, str]:
        return self.author_numbers[0], self.author_numbers[-1]

    @property
    def is_contiguous(self) -> bool:
        """False when the numbering skips, as Ambler numbering does.

        Worth surfacing: a contiguous chain is the only case where an offset
        would have worked, and it is not the common case.
        """
        try:
            nums = [int(a) for a in self.author_numbers]
        except ValueError:
            return False  # insertion codes present, e.g. "52A"
        return all(b == a + 1 for a, b in itertools.pairwise(nums))

    @property
    def missing_author_numbers(self) -> tuple[str, ...]:
        """Author numbers inside the modelled range that are absent.

        These are usually disordered loops the crystallographer could not place.
        A researcher may well name one, and we must say so rather than snap to a
        neighbour.
        """
        try:
            nums = sorted(int(a) for a in self.author_numbers)
        except ValueError:
            return ()
        present = set(nums)
        return tuple(str(n) for n in range(nums[0], nums[-1] + 1) if n not in present)

    # --- the conversion ----------------------------------------------------

    def to_index(self, author_number: int | str) -> int:
        """One author number to its 0-based sequence index.

        Raises rather than returning a best guess: an off-by-one here is a
        scientific error, not a UI annoyance.
        """
        key = str(author_number).strip()
        index = self._index_by_author.get(key)
        if index is not None:
            return index
        first, last = self.author_range
        hint = ""
        if key in self.missing_author_numbers:
            hint = (
                " That residue is within the modelled range but absent from this "
                "structure, most likely a disordered region. Pick another structure "
                "or another residue."
            )
        raise ResidueMappingError(
            f"{self.pdb_id} chain {self.chain_id}: residue {key} is not modelled. "
            f"This chain covers author residues {first}-{last}.{hint}"
        )

    def to_indices(self, author_numbers: list[int | str]) -> tuple[int, ...]:
        """Several author numbers at once, reporting *all* failures together.

        Reporting one at a time would make a researcher fix a ten-residue
        epitope one error per round trip.
        """
        indices: list[int] = []
        problems: list[str] = []
        for author in author_numbers:
            try:
                indices.append(self.to_index(author))
            except ResidueMappingError as exc:
                problems.append(str(exc))
        if problems:
            raise ResidueMappingError("; ".join(problems))
        return tuple(sorted(set(indices)))

    def to_author(self, index: int) -> str:
        """The inverse, for showing results back in the numbering people use."""
        if not 0 <= index < len(self.author_numbers):
            raise ResidueMappingError(
                f"{self.pdb_id} chain {self.chain_id}: index {index} is outside "
                f"0-{len(self.author_numbers) - 1}"
            )
        return self.author_numbers[index]

    def residue_at_author(self, author_number: int | str) -> str:
        return self.sequence[self.to_index(author_number)]

    # --- the third numbering system ----------------------------------------

    def reference_offset(self, reference_sequence: str) -> int:
        """Where this modelled chain starts inside a full reference sequence.

        Returns a 0-based offset, so UniProt position ``p`` (1-based) is at
        design index ``p - 1 - offset``.

        Found by locating the modelled sequence inside the reference rather than
        assuming it starts at the beginning, because a crystal structure usually
        omits a signal peptide and disordered termini — here 32 of 293 residues.
        """
        reference = reference_sequence.replace("\n", "").strip()
        if not reference:
            raise ResidueMappingError("a reference sequence is required to align against")
        offset = reference.find(self.sequence)
        if offset >= 0:
            return offset
        # Exact match failed, which happens when the structure carries point
        # mutations. Fall back to anchoring on the first 25 residues, then
        # confirm by identity at the call site.
        anchor = reference.find(self.sequence[:25])
        if anchor < 0:
            raise ResidueMappingError(
                f"{self.pdb_id} chain {self.chain_id}: its sequence does not align to the "
                "reference sequence, so UniProt positions cannot be converted. Use author "
                "residue numbers instead."
            )
        return anchor

    def index_from_uniprot_position(
        self, position: int, reference_sequence: str, *, expected: str | None = None
    ) -> int:
        """Convert a UniProt (1-based) position to a design index.

        ``expected`` is the amino acid the caller believes is there — UniProt
        annotations always know it — and is checked, because this conversion has
        two chances to be wrong and no way to notice otherwise.
        """
        offset = self.reference_offset(reference_sequence)
        index = position - 1 - offset
        if not 0 <= index < self.length:
            raise ResidueMappingError(
                f"{self.pdb_id} chain {self.chain_id}: UniProt position {position} is "
                f"outside the modelled region (which covers UniProt positions "
                f"{offset + 1}-{offset + self.length})."
            )
        got = self.sequence[index]
        if expected is not None:
            want = THREE_TO_ONE.get(expected.strip().upper(), expected.strip().upper())
            if got != want:
                raise ResidueMappingError(
                    f"{self.pdb_id} chain {self.chain_id}: UniProt position {position} maps "
                    f"to {got}, but {want} was expected. The structure may carry a mutation, "
                    "or the sequences may be misaligned. Refusing to guess."
                )
        return index

    def author_from_uniprot_position(self, position: int, reference_sequence: str) -> str:
        """The author number a UniProt position corresponds to.

        Useful for showing a researcher that UniProt's "position 69" is the
        residue they know as 70.
        """
        return self.to_author(self.index_from_uniprot_position(position, reference_sequence))

    # --- proving the mapping is right --------------------------------------

    def verify_identities(self, expected: Mapping[Any, str]) -> None:
        """Check that mapped residues are the amino acids the caller expects.

        ``expected`` maps author number to a one-letter or three-letter code,
        e.g. ``{70: "S", 166: "Glu"}``. Keys may be ``int`` or ``str``: Python
        callers use ints, JSON bodies always arrive with string keys.

        This turns "I think the mapping is right" into "the mapping is right",
        which for KPC-2's catalytic residues is a check we can actually run.
        """
        mismatches: list[str] = []
        for author, want in expected.items():
            code = want.strip().upper()
            one = THREE_TO_ONE.get(code, code)
            if len(one) != 1:
                raise ResidueMappingError(f"unrecognised residue code {want!r}")
            try:
                got = self.residue_at_author(author)
            except ResidueMappingError as exc:
                mismatches.append(str(exc))
                continue
            if got != one:
                mismatches.append(
                    f"residue {author} maps to index {self.to_index(author)} which is "
                    f"{got}, but {one} was expected"
                )
        if mismatches:
            raise ResidueMappingError(
                "Residue mapping failed verification: " + "; ".join(mismatches)
            )

    def describe(self) -> dict[str, Any]:
        """A summary safe to show a researcher, so the mapping is not a black box."""
        first, last = self.author_range
        return {
            "pdb_id": self.pdb_id,
            "chain_id": self.chain_id,
            "modelled_residues": self.length,
            "author_numbering": f"{first}-{last}",
            "contiguous": self.is_contiguous,
            "gaps": list(self.missing_author_numbers),
        }
