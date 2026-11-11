"""Exercise the whole Boltz path through the adapter, against the live API.

This is the M1 acceptance check. Contract tests prove we parse the responses we
captured; this proves the responses are still what we captured, and that the
adapter drives a real run end to end: identity, cost, submit, poll, harvest,
download.

Default target is KPC-2 (PDB 2OV5), the reference target for the project.

    python scripts/verify_boltz.py                # 10 designs
    python scripts/verify_boltz.py --designs 20
    python scripts/verify_boltz.py --allow-live   # required if the key is live

Refuses to run a live (billed) key unless --allow-live is passed, so this cannot
quietly spend money.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from biostruct.engines import (  # noqa: E402
    BoltzHostedEngine,
    CuratedBinder,
    DesignRules,
    DesignSpec,
    JobStatus,
    StructureTarget,
    TargetChain,
)

KPC2_CIF = "https://files.rcsb.org/download/2OV5.cif"
# Neighbourhood of the KPC-2 active site. 0-indexed, as the API requires.
KPC2_EPITOPE = (67, 68, 69, 70, 71, 128, 129, 130, 164, 165, 166, 167, 168, 169, 170)

TERMINAL_WAIT_SECONDS = 1800


def load_key() -> str:
    key = os.environ.get("BOLTZ_API_KEY", "")
    if key:
        return key
    for candidate in (Path(".env"), Path("../../.env"), Path(__file__).resolve().parents[3] / ".env"):
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                m = re.match(r"\s*BOLTZ_API_KEY\s*=\s*(.*)", line)
                if m:
                    return m.group(1).strip().strip('"').strip("'")
    return ""


def heading(text: str) -> None:
    print(f"\n{'=' * 70}\n{text}\n{'=' * 70}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--designs", type=int, default=10, help="num_proteins (min 10)")
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="permit a live, billed key; refuses otherwise",
    )
    args = parser.parse_args()

    key = load_key()
    if not key:
        print("No BOLTZ_API_KEY found (env or .env).")
        return 2

    engine = BoltzHostedEngine(key)
    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"{'PASS' if ok else 'FAIL'}  {label}{f' - {detail}' if detail else ''}")
        if not ok:
            failures.append(label)

    try:
        heading("1. Identity and key mode")
        identity = await engine.whoami()
        print(f"  key_type     : {identity.key_type}")
        print(f"  mode         : {identity.mode}")
        print(f"  organisation : {identity.organization_name}")
        check("key resolves", bool(identity.key_type))
        check("mode is known", identity.mode in ("test", "live"), identity.mode)

        if identity.is_live and not args.allow_live:
            print(
                "\nThis is a LIVE key and would spend real credits. "
                "Re-run with --allow-live if that is intended."
            )
            return 3
        if identity.is_live:
            cost = Decimal("0.05") * args.designs
            print(f"\n  !! LIVE key: this will bill approximately ${cost}")

        spec = DesignSpec(
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
            num_proteins=args.designs,
        )

        heading("2. Cost estimate (free, and the spend guard depends on it)")
        estimate = await engine.estimate_cost(spec)
        print(f"  total     : ${estimate.total_usd}")
        print(f"  per unit  : ${estimate.unit_usd}")
        print(f"  units     : {estimate.units}")
        check("total is Decimal", isinstance(estimate.total_usd, Decimal))
        check("units match the request", estimate.units == args.designs)
        check(
            "total equals unit x units",
            estimate.total_usd == estimate.unit_usd * estimate.units,
            f"{estimate.total_usd} vs {estimate.unit_usd * estimate.units}",
        )

        heading("3. Submit (RCSB URL handed straight to Boltz)")
        idem = f"verify-{int(time.time())}"
        job = await engine.start_design(spec, idempotency_key=idem)
        print(f"  job id    : {job.job_id}")
        print(f"  status    : {job.status}")
        print(f"  livemode  : {job.livemode}")
        print(f"  engine    : {job.engine} {job.engine_version}")
        check("job accepted", bool(job.job_id))
        check("livemode matches the key", job.livemode == identity.is_live)
        check("total_to_generate echoed", job.total_to_generate == args.designs)

        heading("4. Poll to a terminal state")
        started = time.monotonic()
        delay = 5.0
        while True:
            job = await engine.get_job(job.job_id)
            print(f"  status={job.status} generated={job.generated} pct={job.percent_complete}")
            if job.status.is_terminal:
                break
            if time.monotonic() - started > TERMINAL_WAIT_SECONDS:
                check("reached a terminal state", False, "timed out")
                break
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 60.0)
        check("run succeeded", job.status is JobStatus.succeeded, str(job.error or job.status))
        check("generated what was asked", job.generated == args.designs)

        heading("5. Harvest results")
        results = [r async for r in engine.iter_results(job.job_id, page_size=4)]
        print(f"  harvested : {len(results)}")
        print(f"  unique ids: {len({r.result_id for r in results})}")
        check("all results harvested", len(results) == args.designs)
        check("no duplicates", len({r.result_id for r in results}) == len(results))

        binder_count = sum(1 for r in results if r.metrics.is_binder)
        check("results are binder results", binder_count == len(results), f"{binder_count}")

        missing = sorted({m for r in results for m in r.metrics.missing_binding_metrics})
        print(f"  metrics absent across the run: {missing or 'none'}")
        if "ipsae_min" in missing:
            print("    (expected in test mode; ranking redistributes its weight)")

        print("\n  top 3 by binding_confidence:")
        ranked = sorted(
            results, key=lambda r: r.metrics.binding_confidence or 0.0, reverse=True
        )
        for r in ranked[:3]:
            m = r.metrics
            seqs = "/".join(str(len(e.value)) for e in r.protein_entities)
            print(
                f"    {r.result_id[-8:]}  bind={m.binding_confidence} iptm={m.iptm} "
                f"pae={m.min_interaction_pae} struct={m.structure_confidence} chains({seqs}aa)"
            )

        heading("6. Artifacts (the mirroring deadline)")
        first = ranked[0]
        check("structure artifact present", first.structure is not None)
        if first.structure is not None:
            print(f"  expires_at: {first.structure.expires_at}")
            check("artifact URL is presigned", first.structure.is_presigned)
            import httpx

            async with httpx.AsyncClient(timeout=120) as http:
                resp = await http.get(first.structure.url)
            body = resp.text
            print(f"  downloaded: {len(body)} bytes")
            check("artifact downloads", resp.status_code == 200, str(resp.status_code))
            check("artifact is mmCIF", body.lstrip().startswith("data_"))
            if not job.livemode and len(body) < 2000:
                print("    (test mode returns a stub CIF, not a real structure)")

        heading("RESULT")
        if failures:
            print(f"{len(failures)} check(s) failed:")
            for f in failures:
                print(f"  - {f}")
            return 1
        print("All checks passed. The adapter drives the live API end to end.")
        return 0
    finally:
        await engine.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
