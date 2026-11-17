"""The citation guardrail.

The brief was explicit: no invented references. This module is how that is
kept, and the method matters — **it is a code gate, not a prompt instruction.**

A prompt that says "only cite real papers" is a request. A model under pressure
to be helpful will sometimes produce a plausible-looking identifier anyway, and
a researcher who follows it to nothing has been actively misled: worse than no
citation at all, because a reference carries authority.

So the model is never trusted to produce an identifier. It may only *point at*
one that a tool handed it this turn, and every pointer is then re-resolved
against the source before a human sees it. Two independent checks:

1. **Provenance.** The marker must name a paper that a tool returned during
   this turn. Anything else was invented, however real it looks.
2. **Existence.** The identifier is looked up again. Europe PMC answers 200
   with zero results for an id that does not exist, so an invented-but-plausible
   id fails here even if provenance were somehow satisfied.

A marker that fails either check is removed and the sentence that carried it is
flagged, so the claim survives but its false authority does not. We never
silently delete the claim — a researcher is entitled to see that the model
asserted something it could not support.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass

from biostruct.sources.europepmc import Paper

log = logging.getLogger(__name__)

#: How the model points at a paper: ``[cite:MED:42942151]``.
#:
#: Deliberately not a bare number or a ``[1]``: the model must name the record,
#: which makes provenance checkable. A numbered form would be unverifiable
#: because any integer looks valid.
MARKER = re.compile(r"\[cite:([A-Za-z]{2,6}):([A-Za-z0-9]{1,32})\]")

#: Sentence split for flagging. Deliberately simple: this decides where a
#: warning is attached, never what is shown, so over-splitting is harmless.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

Resolver = Callable[[str, str], Awaitable[Paper | None]]


class Reason:
    """Why a marker was removed. Shown to the user, so it is plain English."""

    FABRICATED = "no tool returned this paper, so the model invented the reference"
    UNRESOLVABLE = "this paper could not be found when we checked it again"


@dataclass(frozen=True, slots=True)
class RemovedCitation:
    key: str
    reason: str
    sentence: str


@dataclass(frozen=True, slots=True)
class VerifiedAnswer:
    """What the user is allowed to see.

    ``text`` has every surviving marker rewritten to a numbered reference and
    every failed one stripped. ``papers`` is the reference list, in citation
    order. ``removed`` is what did not survive, and is surfaced rather than
    hidden.
    """

    text: str
    papers: tuple[Paper, ...] = ()
    removed: tuple[RemovedCitation, ...] = ()
    flagged_sentences: tuple[str, ...] = ()

    @property
    def is_clean(self) -> bool:
        return not self.removed

    @property
    def warning(self) -> str:
        """One sentence for the interface, or empty when nothing was removed."""
        if not self.removed:
            return ""
        n = len(self.removed)
        noun = "reference" if n == 1 else "references"
        return (
            f"{n} {noun} in this answer could not be verified and {'was' if n == 1 else 'were'} "
            "removed. The claims they supported are marked."
        )


def _tidy(text: str) -> str:
    """Remove markers and the gaps they leave behind."""
    text = MARKER.sub("", text)
    text = re.sub(r"\s+([.,;:])", r"\1", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _sentence_containing(text: str, index: int) -> str:
    """The sentence a marker sits in, for the flag."""
    start = 0
    for match in _SENTENCE_END.finditer(text):
        if match.end() > index:
            break
        start = match.end()
    end = len(text)
    tail = _SENTENCE_END.search(text, index)
    if tail:
        end = tail.start()
    # Cleaned, so a flagged sentence matches the text the user is shown and the
    # interface can highlight it without re-deriving the cleanup.
    return _tidy(text[start:end])


def extract_markers(text: str) -> list[tuple[str, str]]:
    """Every ``(source, external_id)`` the draft points at, in order."""
    return [(m.group(1).upper(), m.group(2)) for m in MARKER.finditer(text)]


def allowed_keys(papers: Iterable[Paper]) -> set[str]:
    """The provenance set: papers tools actually returned this turn."""
    return {p.key for p in papers}


async def verify(
    draft: str,
    *,
    available: Sequence[Paper],
    resolve: Resolver,
) -> VerifiedAnswer:
    """Strip every citation the model could not have legitimately made.

    ``available`` is every paper returned by tool calls in this turn, and
    ``resolve`` re-checks an identifier against the source. Both are required:
    provenance alone would pass a stale id, and existence alone would pass a
    real paper the model never actually read.

    Resolution failures caused by the *source* being down are treated as
    unverified, not as fabrication. We would rather drop a real citation than
    show an unchecked one, and the user is told either way.
    """
    allowed = allowed_keys(available)

    kept: list[Paper] = []
    numbering: dict[str, int] = {}
    removed: list[RemovedCitation] = []
    flagged: list[str] = []

    # Resolve each distinct key once, however many times it is cited.
    checked: dict[str, Paper | None] = {}

    for source, external_id in extract_markers(draft):
        key = f"{source}:{external_id}"
        if key in checked or key not in allowed:
            continue
        try:
            checked[key] = await resolve(source, external_id)
        except Exception as exc:
            log.warning("citation check failed for %s: %s", key, exc)
            checked[key] = None

    def replace(match: re.Match[str]) -> str:
        source = match.group(1).upper()
        external_id = match.group(2)
        key = f"{source}:{external_id}"
        sentence = _sentence_containing(draft, match.start())

        if key not in allowed:
            removed.append(RemovedCitation(key, Reason.FABRICATED, sentence))
            if sentence and sentence not in flagged:
                flagged.append(sentence)
            return ""

        # Resolution is the authority here: ``by_key`` is what the model was
        # shown, which is exactly what we are declining to trust.
        paper = checked.get(key)
        if paper is None:
            removed.append(RemovedCitation(key, Reason.UNRESOLVABLE, sentence))
            if sentence and sentence not in flagged:
                flagged.append(sentence)
            return ""

        if key not in numbering:
            kept.append(paper)
            numbering[key] = len(kept)
        return f"[{numbering[key]}]"

    # Removing a marker can leave " ." or a double space behind.
    text = _tidy(MARKER.sub(replace, draft))

    return VerifiedAnswer(
        text=text,
        papers=tuple(kept),
        removed=tuple(removed),
        flagged_sentences=tuple(flagged),
    )


def reference_list(papers: Sequence[Paper]) -> list[dict[str, str]]:
    """The reference block for the interface.

    Preprints are labelled. A reader is entitled to know that a supporting
    citation has not been peer reviewed, and nothing else on the page would
    tell them.
    """
    out: list[dict[str, str]] = []
    for n, paper in enumerate(papers, start=1):
        out.append(
            {
                "n": str(n),
                "key": paper.key,
                "title": paper.title,
                "citation": paper.citation(),
                "url": paper.url,
                "doi": paper.doi,
                "note": "preprint, not peer reviewed" if paper.is_preprint else "",
            }
        )
    return out


__all__ = [
    "MARKER",
    "Reason",
    "RemovedCitation",
    "VerifiedAnswer",
    "allowed_keys",
    "extract_markers",
    "reference_list",
    "verify",
]
