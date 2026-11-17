"""The agent's actual capabilities (M7).

Free tools do their work. Paid tools return a priced ``Proposal`` and spend
nothing — the dispatcher enforces that, and M8 adds the confirmation route that
turns a proposal into a run.

Every summary here is written to be *read*: by the model on its next step, and
by a researcher afterwards. That is why they contain sentences and units rather
than JSON. A summary like ``{"n": 20}`` tells the model nothing it can reason
with, and tells the researcher nothing at all.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from biostruct.agent.tools.base import (
    Proposal,
    Registry,
    Tool,
    ToolContext,
    ToolError,
    ToolResultData,
    numeric,
    text,
)
from biostruct.library import (
    LIBRARY_SIZE_MAX,
    SCREEN_USD_PER_MOLECULE,
    LibraryBuilder,
    control_for,
)
from biostruct.sources.http import SourceUnavailable

log = logging.getLogger(__name__)

#: Measured 2026-11-17. Every price the agent quotes comes from here, and the
#: engine's own estimate-cost endpoint is still called before anything is
#: submitted — these are for the proposal, not for the bill.
FOLD_USD_PER_SAMPLE = Decimal("0.0250")
#: Re-exported from the library module so there is exactly one screen price in
#: the codebase; a second copy is a second thing to forget to update.
DESIGN_USD_PER_MOLECULE = Decimal("0.0250")
ADME_USD_PER_MOLECULE = Decimal("0.0100")


def _require(args: dict[str, Any], key: str) -> str:
    value = str(args.get(key) or "").strip()
    if not value:
        raise ToolError(f"{key} is required and was empty.")
    return value


def _int_arg(args: dict[str, Any], key: str, default: int) -> int:
    raw = args.get(key)
    if raw in (None, "", 0):
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ToolError(f"{key} must be a whole number, got {raw!r}.") from None


# --------------------------------------------------------------- literature


async def literature_search(args: dict[str, Any], ctx: ToolContext) -> ToolResultData:
    query = _require(args, "query")
    limit = min(_int_arg(args, "limit", 6), 20)
    if ctx.europepmc is None:
        raise ToolError("The literature source is not configured in this environment.")
    try:
        papers = await ctx.europepmc.search(query, limit=limit)
    except SourceUnavailable as exc:
        raise ToolError(
            f"Europe PMC could not be reached ({exc.detail}). Say so rather than "
            "citing anything from memory.",
            retryable=True,
        ) from exc
    if not papers:
        return ToolResultData(
            summary=(
                f"Europe PMC returned nothing for {query!r}. Do not cite anything: "
                "there is no paper here to cite. Try different terms or tell the "
                "researcher the literature is thin."
            ),
            payload={"query": query, "results": []},
            papers=(),
        )
    lines = [f"{len(papers)} papers found for {query!r}. Cite only from this list:"]
    for paper in papers:
        note = " (preprint, not peer reviewed)" if paper.is_preprint else ""
        lines.append(
            f"- [cite:{paper.key}] {paper.title} — {paper.journal or 'unknown journal'}, "
            f"{paper.year or 'year unknown'}{note}"
        )
    return ToolResultData(
        summary="\n".join(lines),
        payload={
            "query": query,
            "results": [
                {
                    "key": p.key,
                    "title": p.title,
                    "authors": p.authors,
                    "journal": p.journal,
                    "year": p.year,
                    "doi": p.doi,
                    "url": p.url,
                    "is_preprint": p.is_preprint,
                    "abstract": p.abstract[:2000],
                }
                for p in papers
            ],
        },
        papers=tuple(papers),
        artifact={
            "kind": "evidence",
            "title": f"Literature: {query}",
            "payload": {"query": query, "count": len(papers)},
        },
    )


async def resistance_literature(args: dict[str, Any], ctx: ToolContext) -> ToolResultData:
    enzyme = _require(args, "enzyme")
    organism = str(args.get("organism") or "").strip()
    if ctx.europepmc is None:
        raise ToolError("The literature source is not configured in this environment.")
    try:
        papers = await ctx.europepmc.search_for_resistance(enzyme, organism, limit=8)
    except SourceUnavailable as exc:
        raise ToolError(f"Europe PMC could not be reached ({exc.detail}).", retryable=True) from exc
    subject = f"{enzyme} in {organism}" if organism else enzyme
    if not papers:
        return ToolResultData(
            summary=f"No papers found on {subject}. Cite nothing.",
            payload={"enzyme": enzyme, "organism": organism, "results": []},
        )
    lines = [f"{len(papers)} papers on {subject}. Cite only from this list:"]
    for paper in papers:
        lines.append(f"- [cite:{paper.key}] {paper.title} ({paper.year or 'year unknown'})")
    return ToolResultData(
        summary="\n".join(lines),
        payload={
            "enzyme": enzyme,
            "organism": organism,
            "results": [
                {"key": p.key, "title": p.title, "year": p.year, "url": p.url} for p in papers
            ],
        },
        papers=tuple(papers),
    )


# ------------------------------------------------------------------- targets


async def target_find(args: dict[str, Any], ctx: ToolContext) -> ToolResultData:
    query = _require(args, "query")
    if ctx.sources is None:
        raise ToolError("The protein sources are not configured in this environment.")
    result = await ctx.sources.search_targets(query, limit=8)
    if not result.targets:
        detail = (
            f" UniProt reported: {'; '.join(result.degradation.failures.values())}."
            if result.degradation.any
            else ""
        )
        raise ToolError(
            f"No reviewed protein matched {query!r}.{detail} Ask the researcher for "
            "the enzyme name or the organism rather than guessing an accession."
        )
    lines = [f"{len(result.targets)} candidate proteins for {query!r}:"]
    for target in result.targets:
        sites = (
            f", {len(target.active_sites)} annotated active-site residues"
            if target.active_sites
            else ", no annotated active site"
        )
        lines.append(
            f"- {target.accession} {target.protein_name} ({target.organism}), "
            f"{target.length} residues, {target.evidence_level}{sites}"
        )
    lines.append(
        "Confirm which one the researcher means before anything is folded or screened. "
        "These are candidates, not a decision."
    )
    return ToolResultData(
        summary="\n".join(lines),
        payload={
            "query": query,
            "candidates": [
                {
                    "accession": t.accession,
                    "protein_name": t.protein_name,
                    "organism": t.organism,
                    "length": t.length,
                    "reviewed": t.reviewed,
                    "function": t.function[:1000],
                    "gene_names": list(t.gene_names),
                    "pdb_ids": list(t.pdb_ids),
                    "active_sites": [
                        {"position": s.position, "description": s.description}
                        for s in t.active_sites
                    ],
                }
                for t in result.targets
            ],
            "degraded_sources": result.degradation.sources,
        },
    )


async def target_detail(args: dict[str, Any], ctx: ToolContext) -> ToolResultData:
    accession = _require(args, "accession").upper()
    if ctx.sources is None:
        raise ToolError("The protein sources are not configured in this environment.")
    try:
        evidence = await ctx.sources.gather_evidence(accession)
    except SourceUnavailable as exc:
        raise ToolError(f"Could not load {accession}: {exc.detail}", retryable=True) from exc
    target = evidence.target
    best = evidence.best_structure
    lines = [
        f"{target.accession} — {target.protein_name} ({target.organism}), "
        f"{target.length} residues, {target.evidence_level}.",
    ]
    if target.function:
        lines.append(f"Function: {target.function[:600]}")
    if target.active_sites:
        positions = ", ".join(str(s.position) for s in target.active_sites[:8])
        lines.append(
            f"Annotated active-site residues (UniProt numbering): {positions}. "
            "These are the pocket to aim at, and they are measured annotation, not a guess."
        )
    else:
        lines.append(
            "No annotated active site. A pocket would have to be inferred, which is "
            "weaker evidence and worth telling the researcher."
        )
    if best is not None:
        lines.append(
            f"{len(evidence.structures)} experimental structures; best is {best.pdb_id} "
            f"({best.quality_note})."
        )
    else:
        lines.append(
            "No experimental structure. The sequence can still be folded — which is "
            "the point, since a resistant isolate's enzyme is usually a mutant that "
            "no structure database holds."
        )
    if evidence.degradation.any:
        lines.append(f"Could not reach: {', '.join(evidence.degradation.sources)}.")
    return ToolResultData(
        summary="\n".join(lines),
        payload={
            "accession": target.accession,
            "sequence": target.sequence,
            "summary": evidence.summary(),
            "active_sites": [
                {"position": s.position, "description": s.description} for s in target.active_sites
            ],
            "structures": [
                {
                    "pdb_id": s.pdb_id,
                    "title": s.title,
                    "method": s.method,
                    "resolution_a": s.resolution_a,
                    "quality": s.quality_note,
                }
                for s in evidence.structures
            ],
        },
        artifact={
            "kind": "structure",
            "title": f"{target.accession} {target.protein_name}",
            "payload": {
                "accession": target.accession,
                "pdb_id": best.pdb_id if best else "",
                "length": target.length,
            },
        },
    )


# ----------------------------------------------------------------- molecules


async def molecules_library(args: dict[str, Any], ctx: ToolContext) -> ToolResultData:
    target_class = str(args.get("ambler_class") or "A").strip().upper()
    size = min(_int_arg(args, "size", 24), LIBRARY_SIZE_MAX)
    chembl_target_id = str(args.get("chembl_target_id") or "").strip()

    control = control_for(target_class)
    if control is None:
        raise ToolError(
            f"No positive control is available for Ambler class {target_class!r}. "
            "Without one, a screen cannot be shown to be working, so do not propose "
            "one. Classes with a control: A, B, C, D."
        )
    library = await LibraryBuilder(ctx.chembl).assemble(
        target_class=target_class, chembl_target_id=chembl_target_id, max_size=size
    )
    lines = [library.summary()]
    lines.append(
        f"The control is {library.controls[0].label}, which is a known inhibitor of "
        f"class {target_class}. If the screen cannot rank it near the top, the run is "
        "not telling us anything — say so rather than reporting the other hits."
    )
    if control.status != "approved":
        lines.append(
            f"Note: {control.label} is {control.status}, not an approved drug. "
            "Do not describe it as a licensed treatment."
        )
    for entry in library.entries[:10]:
        potency = (
            f", measured {entry.known_potency_nm:g} nM against this target"
            if entry.known_potency_nm is not None
            else ""
        )
        lines.append(f"- {entry.label}{potency}")
    return ToolResultData(
        summary="\n".join(lines),
        payload={
            "ambler_class": target_class,
            "estimated_screen_cost_usd": str(library.estimated_cost_usd),
            "entries": [
                {
                    "smiles": e.smiles,
                    "external_id": e.external_id,
                    "name": e.name,
                    "provenance": e.provenance,
                    "note": e.note,
                    "is_control": e.is_control,
                    "known_potency_nm": e.known_potency_nm,
                }
                for e in library.entries
            ],
            "dropped": [
                {"identifier": d.identifier, "name": d.name, "reason": d.reason}
                for d in library.dropped
            ],
            "unavailable": list(library.unavailable),
        },
        artifact={
            "kind": "library",
            "title": f"Class {target_class} library, {library.size} molecules",
            "payload": {
                "size": library.size,
                "cost_usd": str(library.estimated_cost_usd),
                "control": library.controls[0].label,
            },
        },
    )


async def chembl_target_lookup(args: dict[str, Any], ctx: ToolContext) -> ToolResultData:
    query = _require(args, "query")
    organism = str(args.get("organism") or "").strip()
    if ctx.chembl is None:
        raise ToolError("ChEMBL is not configured in this environment.")
    try:
        targets = await ctx.chembl.find_targets(
            query, organism=organism, proteins_only=True, limit=10
        )
    except SourceUnavailable as exc:
        raise ToolError(f"ChEMBL could not be reached ({exc.detail}).", retryable=True) from exc
    if not targets:
        return ToolResultData(
            summary=(
                f"ChEMBL has no protein target matching {query!r}"
                f"{f' in {organism}' if organism else ''}. A library can still be built "
                "from the curated set alone; it will just be smaller."
            ),
            payload={"query": query, "targets": []},
        )
    lines = [
        f"{len(targets)} ChEMBL targets for {query!r}. "
        "ChEMBL's relevance ranking is unreliable — confirm the organism matches "
        "what the researcher described before using one:"
    ]
    for t in targets:
        lines.append(f"- {t.target_chembl_id} {t.name} ({t.organism})")
    return ToolResultData(
        summary="\n".join(lines),
        payload={
            "query": query,
            "targets": [
                {
                    "chembl_id": t.target_chembl_id,
                    "name": t.name,
                    "organism": t.organism,
                    "accessions": list(t.accessions),
                }
                for t in targets
            ],
        },
    )


# -------------------------------------------------------- paid: proposals only


async def propose_fold(args: dict[str, Any], ctx: ToolContext) -> Proposal:
    accession = str(args.get("accession") or "").strip().upper()
    sequence = "".join(str(args.get("sequence") or "").split()).upper()
    if not accession and not sequence:
        raise ToolError(
            "Folding needs either a UniProt accession or the amino-acid sequence "
            "itself. For a resistant isolate the researcher's own sequencing output "
            "is the right input — that is the mutant no database holds."
        )
    samples = max(1, min(_int_arg(args, "num_samples", 1), 10))
    estimate = FOLD_USD_PER_SAMPLE * samples
    subject = accession or f"a {len(sequence)}-residue sequence"
    return Proposal(
        tool="fold_structure",
        arguments={"accession": accession, "sequence": sequence, "num_samples": samples},
        title=f"Fold {subject}",
        detail=(
            f"Predict the 3D structure of {subject} with Boltz, {samples} "
            f"sample{'s' if samples != 1 else ''}."
        ),
        estimate_usd=estimate,
        yields="A 3D structure with per-residue confidence, and the pocket to aim at.",
    )


async def propose_screen(args: dict[str, Any], ctx: ToolContext) -> Proposal:
    molecules = _int_arg(args, "num_molecules", 24)
    if molecules < 1:
        raise ToolError("A screen needs at least one molecule.")
    if molecules > LIBRARY_SIZE_MAX:
        raise ToolError(
            f"We cap a screen at {LIBRARY_SIZE_MAX} molecules "
            f"(about ${LIBRARY_SIZE_MAX * SCREEN_USD_PER_MOLECULE:.0f}). "
            "Ask the researcher before going near that."
        )
    accession = str(args.get("accession") or "").strip().upper()
    estimate = SCREEN_USD_PER_MOLECULE * molecules
    return Proposal(
        tool="screen_molecules",
        arguments={"accession": accession, "num_molecules": molecules},
        title=f"Screen {molecules} molecules against {accession or 'the target'}",
        detail=(
            f"Each molecule is docked and scored against the pocket. "
            f"{molecules} molecules at ${SCREEN_USD_PER_MOLECULE} each."
        ),
        estimate_usd=estimate,
        yields=(
            "A ranked list with a binding confidence, a predicted complex structure "
            "and drug-likeness for each molecule."
        ),
    )


async def propose_design(args: dict[str, Any], ctx: ToolContext) -> Proposal:
    molecules = max(10, min(_int_arg(args, "num_molecules", 10), LIBRARY_SIZE_MAX))
    accession = str(args.get("accession") or "").strip().upper()
    return Proposal(
        tool="design_molecules",
        arguments={"accession": accession, "num_molecules": molecules},
        title=f"Invent {molecules} new molecules for {accession or 'the target'}",
        detail=(
            "Rather than screening things that already exist, the model generates "
            f"molecules shaped to this pocket. {molecules} molecules at "
            f"${DESIGN_USD_PER_MOLECULE} each."
        ),
        estimate_usd=DESIGN_USD_PER_MOLECULE * molecules,
        yields=(
            "Novel SMILES with binding confidence and drug-likeness. These are "
            "computational hypotheses, and every one still needs a laboratory."
        ),
    )


def build_registry() -> Registry:
    """The tools a turn may use. Order is the order the model sees them."""
    return Registry(
        [
            Tool(
                name="target_find",
                description=(
                    "Find candidate proteins in UniProt from a plain-language description "
                    "of an infection or an enzyme, e.g. 'the enzyme that makes Klebsiella "
                    "resistant to carbapenems'. Free. Use this first: everything else "
                    "needs a specific protein. Returns candidates to confirm with the "
                    "researcher, never a decision."
                ),
                properties={
                    "query": text("What the researcher described, in their own words."),
                },
                required=("query",),
                handler=target_find,
            ),
            Tool(
                name="target_detail",
                description=(
                    "Load one UniProt protein in full: its sequence, its annotated "
                    "active-site residues (the pocket to aim at) and any experimental "
                    "structures. Free. Call this before proposing a fold or a screen."
                ),
                properties={"accession": text("UniProt accession, e.g. Q9F663.")},
                required=("accession",),
                handler=target_detail,
            ),
            Tool(
                name="literature_search",
                description=(
                    "Search Europe PMC. Free. This is the ONLY way to obtain a citation: "
                    "you may cite a paper only if it appears in a tool result this turn, "
                    "using the exact [cite:SOURCE:ID] marker shown. A reference you write "
                    "from memory is removed before the researcher sees it, and the claim "
                    "is flagged as unsupported."
                ),
                properties={
                    "query": text("Search terms."),
                    "limit": numeric("How many papers, 1-20.", minimum=1, maximum=20),
                },
                required=("query",),
                handler=literature_search,
            ),
            Tool(
                name="resistance_literature",
                description=(
                    "Literature on a specific resistance enzyme, optionally in a specific "
                    "organism. Free. You may cite only the papers this returns, using the "
                    "exact [cite:SOURCE:ID] marker shown; a reference written from memory "
                    "is removed and the claim flagged as unsupported."
                ),
                properties={
                    "enzyme": text("Enzyme or gene, e.g. 'KPC-2' or 'NDM-1'."),
                    "organism": text("Organism, or an empty string for any."),
                },
                required=("enzyme",),
                handler=resistance_literature,
            ),
            Tool(
                name="chembl_target_lookup",
                description=(
                    "Find a target in ChEMBL so its measured inhibitors can be pulled into "
                    "a library. Free. ChEMBL's relevance ranking is unreliable, so check "
                    "the organism before using a result."
                ),
                properties={
                    "query": text("Enzyme name, e.g. 'KPC-2'."),
                    "organism": text("Organism to filter by, or an empty string."),
                },
                required=("query",),
                handler=chembl_target_lookup,
            ),
            Tool(
                name="molecules_library",
                description=(
                    "Assemble a candidate molecule library for an enzyme class, always "
                    "including a positive control — a compound known to inhibit that "
                    "class, so the screen can be shown to be working. Free: this only "
                    "chooses the molecules and prices the screen. Ambler class A is KPC, "
                    "CTX-M, TEM, SHV; B is the metallo-enzymes NDM, VIM, IMP; C is AmpC "
                    "and CMY; D is the OXA enzymes."
                ),
                properties={
                    "ambler_class": text("A, B, C or D."),
                    "size": numeric("How many molecules.", minimum=1, maximum=LIBRARY_SIZE_MAX),
                    "chembl_target_id": text(
                        "ChEMBL target id from chembl_target_lookup, or an empty string "
                        "to use the curated set alone."
                    ),
                },
                required=("ambler_class",),
                handler=molecules_library,
            ),
            Tool(
                name="propose_fold",
                description=(
                    "Propose predicting a protein's 3D structure. COSTS MONEY, so this "
                    "only produces a priced proposal for the researcher to confirm — it "
                    "never starts the run. Say what it will cost in your reply."
                ),
                properties={
                    "accession": text("UniProt accession, or an empty string."),
                    "sequence": text(
                        "The amino-acid sequence, or an empty string. Prefer the "
                        "researcher's own sequencing output for a resistant isolate."
                    ),
                    "num_samples": numeric("How many samples, 1-10.", minimum=1, maximum=10),
                },
                required=(),
                handler=propose_fold,
                spend="paid",
            ),
            Tool(
                name="propose_screen",
                description=(
                    "Propose screening a molecule library against a target. COSTS MONEY: "
                    "produces a priced proposal only, never starts the run."
                ),
                properties={
                    "accession": text("UniProt accession of the target."),
                    "num_molecules": numeric(
                        "How many molecules.", minimum=1, maximum=LIBRARY_SIZE_MAX
                    ),
                },
                required=("accession",),
                handler=propose_screen,
                spend="paid",
            ),
            Tool(
                name="propose_design",
                description=(
                    "Propose inventing new molecules shaped to a pocket, rather than "
                    "screening existing ones. COSTS MONEY: produces a priced proposal "
                    "only, never starts the run."
                ),
                properties={
                    "accession": text("UniProt accession of the target."),
                    "num_molecules": numeric(
                        "How many molecules to generate, at least 10.",
                        minimum=10,
                        maximum=LIBRARY_SIZE_MAX,
                    ),
                },
                required=("accession",),
                handler=propose_design,
                spend="paid",
            ),
        ]
    )


__all__ = [
    "ADME_USD_PER_MOLECULE",
    "DESIGN_USD_PER_MOLECULE",
    "FOLD_USD_PER_SAMPLE",
    "SCREEN_USD_PER_MOLECULE",
    "build_registry",
]
