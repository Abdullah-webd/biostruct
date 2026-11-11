"""One live BoltzProt-1 run, to answer two questions test mode cannot.

Test-mode results are synthetic: the structure artifact is a 522-byte stub and
the two protein chains have random lengths. That leaves two things unknown, and
both block the 3D viewer and the FASTA export:

1. **Which chain is the designed binder?** The viewer must colour binder and
   target differently; the export must label them. Guessing would mislabel
   every result.
2. **What does a real structure artifact contain?** Mol* cannot be validated
   against a stub.

It also checks whether ``ipsae_min`` — absent from every test-mode result —
appears in live ones, which decides whether ranking usually redistributes its
weight or usually does not.

Costs real money, so: a hard cap, an explicit estimate check before submitting,
and the run id written to disk so an interrupted script can re-attach instead
of starting a second billed run.

    python scripts/live_probe.py                 # 10 designs, caps at $1.00
    python scripts/live_probe.py --attach         # re-attach to the saved run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from biostruct.engines import (
    BoltzHostedEngine,
    CuratedBinder,
    DesignRules,
    DesignSpec,
    JobStatus,
    StructureTarget,
    TargetChain,
)

REPO = Path(__file__).resolve().parents[3]
STATE_FILE = Path(__file__).resolve().parents[1] / ".live_probe_run.json"
FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "boltz"

# KPC-2, PDB 2OV5 chain A. Indices are 0-based, converted from the author
# numbering of the catalytic residues Ser70, Lys73, Ser130, Glu166.
KPC2_CIF = "https://files.rcsb.org/download/2OV5.cif"
KPC2_EPITOPE = (39, 42, 99, 135)


# The modelled target sequence, read from the captured RCSB fixture rather than
# hardcoded. A hand-typed copy was wrong by three residues on the first
# attempt, and the chain classification below depends entirely on it being
# exact.
def _kpc2_modelled_sequence() -> str:
    data = json.loads((FIXTURES.parent / "rcsb" / "2ov5_chain_a.json").read_text())
    return str(data["sequence"])


HARD_CAP_USD = Decimal("1.00")


def load_live_key() -> str:
    for line in (REPO / ".env").read_text().splitlines():
        m = re.match(r"\s*BOLTZ_LIVE_API_KEY\s*=\s*(.*)", line)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return ""


def heading(text: str) -> None:
    print(f"\n{'=' * 72}\n{text}\n{'=' * 72}")


def spec_for(num_proteins: int) -> DesignSpec:
    return DesignSpec(
        target=StructureTarget(
            structure_url=KPC2_CIF,
            chains=(TargetChain(chain_id="A", epitope_residues=KPC2_EPITOPE),),
        ),
        binder=CuratedBinder(
            kind="nanobody",
            rules=DesignRules(
                excluded_amino_acids=("C",),
                excluded_sequence_motifs=("NXS", "NXT"),
                max_hydrophobic_fraction=Decimal("0.45"),
            ),
        ),
        num_proteins=num_proteins,
    )


def classify_chains(entities: list[dict[str, Any]]) -> dict[str, Any]:
    """Work out which entity is the designed binder.

    The target's sequence is known, so the chain that matches it is the target
    and the other is the binder. Identity comparison rather than a length
    heuristic, because a nanobody and a cropped target can be similar lengths.
    """
    target = _kpc2_modelled_sequence()
    findings = []
    for entity in entities:
        sequence = entity.get("value", "")
        chains = entity.get("chain_ids", [])
        exact = sequence == target
        contained = sequence in target or target in sequence
        overlap = 0
        if sequence:
            window = sequence[:30]
            overlap = 1 if window and window in target else 0
        findings.append(
            {
                "chain_ids": chains,
                "length": len(sequence),
                "matches_target_exactly": exact,
                "contained_in_target": contained,
                "prefix_found_in_target": bool(overlap),
                "sequence_head": sequence[:48],
            }
        )
    targets = [f for f in findings if f["matches_target_exactly"] or f["prefix_found_in_target"]]
    binders = [f for f in findings if f not in targets]
    return {
        "entities": findings,
        "target_chains": [c for f in targets for c in f["chain_ids"]],
        "binder_chains": [c for f in binders for c in f["chain_ids"]],
        "conclusive": len(targets) == 1 and len(binders) == 1,
    }


def inspect_cif(text: str) -> dict[str, Any]:
    """Summarise a real mmCIF: chains, residues, atom counts."""
    lines = text.splitlines()
    atom_rows = [ln for ln in lines if ln.startswith(("ATOM", "HETATM"))]
    chains: dict[str, set[str]] = {}
    for row in atom_rows:
        parts = row.split()
        if len(parts) < 9:
            continue
        # mmCIF atom_site column order varies; find it from the loop header.
        chain = parts[6] if len(parts) > 6 else "?"
        residue = parts[8] if len(parts) > 8 else "?"
        chains.setdefault(chain, set()).add(residue)
    return {
        "bytes": len(text),
        "first_line": lines[0] if lines else "",
        "is_mmcif": text.lstrip().startswith("data_"),
        "atom_records": len(atom_rows),
        "chain_count": len(chains),
        "residues_per_chain": {k: len(v) for k, v in sorted(chains.items())},
        "has_atom_site_loop": "_atom_site." in text,
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--designs", type=int, default=10)
    parser.add_argument("--attach", action="store_true", help="resume the saved run")
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("--cap", default=str(HARD_CAP_USD), help="refuse to spend more than this")
    args = parser.parse_args()
    cap = Decimal(args.cap)

    key = load_live_key()
    if not key:
        print("BOLTZ_LIVE_API_KEY not found in .env")
        return 2

    engine = BoltzHostedEngine(key)
    try:
        heading("Key check")
        identity = await engine.whoami()
        print(f"  mode={identity.mode}  key_type={identity.key_type}")
        if not identity.is_live:
            print("  This is not a live key. Nothing to learn here; stopping.")
            return 3

        job_id: str | None = None
        if args.attach and STATE_FILE.exists():
            job_id = json.loads(STATE_FILE.read_text()).get("job_id")
            print(f"  re-attaching to {job_id}")

        if job_id is None:
            heading("Cost estimate (free)")
            spec = spec_for(args.designs)
            estimate = await engine.estimate_cost(spec)
            print(f"  {estimate.units} designs x ${estimate.unit_usd} = ${estimate.total_usd}")
            if estimate.total_usd > cap:
                print(f"  Refusing: ${estimate.total_usd} is over the ${cap} cap.")
                return 4
            print(f"  Within the ${cap} cap. Submitting.")

            heading("Submit")
            idem = f"live-probe-{int(time.time())}"
            job = await engine.start_design(spec, idempotency_key=idem)
            job_id = job.job_id
            STATE_FILE.write_text(
                json.dumps(
                    {"job_id": job_id, "idempotency_key": idem, "designs": args.designs},
                    indent=2,
                )
            )
            print(f"  job {job_id}  livemode={job.livemode}")
            print(f"  saved to {STATE_FILE.name} so an interruption can re-attach")
            if not job.livemode:
                print("  WARNING: the engine reports this run as not live.")

        heading("Waiting for real GPU work")
        print("  A live run is minutes, not the ~10s of test mode.")
        started = time.monotonic()
        delay = 10.0
        last = ""
        while True:
            job = await engine.get_job(job_id)
            line = f"status={job.status} generated={job.generated}/{job.total_to_generate}"
            if line != last:
                elapsed = int(time.monotonic() - started)
                print(f"  [{elapsed:>5}s] {line}")
                last = line
            if job.status.is_terminal:
                break
            if time.monotonic() - started > args.timeout:
                print("  Timed out waiting. Re-run with --attach to continue.")
                return 5
            await asyncio.sleep(delay)
            delay = min(delay * 1.3, 60.0)

        if job.status is not JobStatus.succeeded:
            print(f"\n  Run ended as {job.status}: {job.error}")
            return 1

        heading("Harvest")
        results = [r async for r in engine.iter_results(job_id)]
        print(f"  {len(results)} results")

        heading("QUESTION 1: which chain is the designed binder?")
        classification = classify_chains(
            [
                {"value": e.value, "chain_ids": list(e.chain_ids), "type": e.kind}
                for e in results[0].protein_entities
            ]
        )
        for entity in classification["entities"]:
            role = (
                "TARGET"
                if entity["matches_target_exactly"] or entity["prefix_found_in_target"]
                else "BINDER"
            )
            print(f"  chain {entity['chain_ids']}  {entity['length']:>4} aa  -> {role}")
            print(f"      exact target match : {entity['matches_target_exactly']}")
            print(f"      prefix in target   : {entity['prefix_found_in_target']}")
            print(f"      starts: {entity['sequence_head']}")
        print(f"\n  binder chain(s): {classification['binder_chains']}")
        print(f"  target chain(s): {classification['target_chains']}")
        print(f"  conclusive     : {classification['conclusive']}")

        # Does the rule hold across every result, or only the first?
        consistent = True
        for result in results:
            other = classify_chains(
                [
                    {"value": e.value, "chain_ids": list(e.chain_ids), "type": e.kind}
                    for e in result.protein_entities
                ]
            )
            if other["binder_chains"] != classification["binder_chains"]:
                consistent = False
                print(f"  !! {result.result_id} differs: binder={other['binder_chains']}")
        print(f"  same chain is the binder in all {len(results)} results: {consistent}")

        heading("QUESTION 2: what is in a real structure artifact?")
        first = results[0]
        if first.structure is None:
            print("  no structure artifact")
        else:
            async with httpx.AsyncClient(timeout=180, follow_redirects=True) as http:
                response = await http.get(first.structure.url)
            cif = response.text
            summary = inspect_cif(cif)
            for key_name, value in summary.items():
                print(f"  {key_name:22s}: {value}")
            FIXTURES.mkdir(parents=True, exist_ok=True)
            out = FIXTURES / "structure_live_mode.cif"
            out.write_text(cif)
            print(f"\n  saved {out.relative_to(REPO)} as a permanent viewer fixture")

        heading("QUESTION 3: is ipsae_min reported in live mode?")
        present = [r.result_id for r in results if r.metrics.ipsae_min is not None]
        print(f"  present on {len(present)} of {len(results)} results")
        if present:
            values = [r.metrics.ipsae_min for r in results if r.metrics.ipsae_min is not None]
            print(f"  range: {min(values)} to {max(values)}")

        heading("Metrics across the run")
        for r in sorted(results, key=lambda r: r.metrics.binding_confidence or 0, reverse=True):
            m = r.metrics
            lengths = "/".join(str(len(e.value)) for e in r.protein_entities)
            print(
                f"  {r.result_id[-10:]}  bind={m.binding_confidence} iptm={m.iptm} "
                f"pae={m.min_interaction_pae} struct={m.structure_confidence} "
                f"ipsae={m.ipsae_min} chains({lengths}aa)"
            )

        # Keep one real result payload as a fixture, with presigned URLs stripped.
        def scrub(obj: Any) -> Any:
            if isinstance(obj, dict):
                return {k: scrub(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [scrub(v) for v in obj]
            if isinstance(obj, str) and "X-Amz-Signature" in obj:
                return obj.split("?")[0] + "?<presigned-query-redacted>"
            return obj

        payload = {
            "_comment": (
                "A real BoltzProt-1 result, captured live on a KPC-2 nanobody run. "
                "Presigned query strings redacted. This is the ground truth for "
                "chain roles and for the structure viewer."
            ),
            "classification": classification,
            "results": [scrub(r.raw) for r in results[:3]],
        }
        out = FIXTURES / "design_results_live.json"
        out.write_text(json.dumps(payload, indent=2, sort_keys=True))
        print(f"\nsaved {out.relative_to(REPO)}")

        heading("DONE")
        print(f"  job {job_id}")
        print(f"  binder chain: {classification['binder_chains']}")
        print(f"  target chain: {classification['target_chains']}")
        return 0
    finally:
        await engine.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
