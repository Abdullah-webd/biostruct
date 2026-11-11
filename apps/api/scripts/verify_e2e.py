"""Drive the whole workflow against a running API and the real Boltz engine.

Steps 1 through 5 of the product, end to end: register, create a project, find
a target, choose an epitope in author numbering, estimate, confirm, submit, watch
the reconciler poll it, and read back candidates whose structures were mirrored
into our own storage.

    python scripts/verify_e2e.py [base_url]

Uses the configured Boltz key. With a test key this is free and the metrics are
synthetic, which is exactly what is wanted for checking plumbing. Refuses to
proceed with a live key unless --allow-live is passed.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

PASSWORD = "ribosome velvet cartography"
KPC2 = "Q9F663"
# Author numbering, as the literature cites them. The API converts.
CATALYTIC = [70, 73, 130, 166]

_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_jar))
_failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}{f' - {detail}' if detail else ''}")
    if not ok:
        _failures.append(label)


def heading(text: str) -> None:
    print(f"\n{'=' * 70}\n{text}\n{'=' * 70}")


class Client:
    def __init__(self, root: str) -> None:
        self.base = root.rstrip("/") + "/api/v1"

    def _csrf(self) -> str | None:
        return next((c.value for c in _jar if c.name == "biostruct_csrf"), None)

    def call(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("content-type", "application/json")
        token = self._csrf()
        if token:
            req.add_header("x-csrf-token", token)
        try:
            with _opener.open(req, timeout=60) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            return e.code, (json.loads(raw) if raw else None)
        except urllib.error.URLError as e:
            print(f"  cannot reach the API: {e.reason}")
            raise SystemExit(2) from e


def boltz_mode() -> str:
    key = os.environ.get("BOLTZ_API_KEY", "")
    if not key:
        for candidate in (Path(".env"), Path(__file__).resolve().parents[3] / ".env"):
            if candidate.exists():
                for line in candidate.read_text().splitlines():
                    m = re.match(r"\s*BOLTZ_API_KEY\s*=\s*(.*)", line)
                    if m:
                        key = m.group(1).strip().strip('"').strip("'")
                        break
    if "_live_" in key:
        return "live"
    if "_test_" in key:
        return "test"
    return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url", nargs="?", default="http://localhost:8000")
    parser.add_argument("--designs", type=int, default=10)
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    mode = boltz_mode()
    print(f"Boltz key mode: {mode}")
    if mode == "live" and not args.allow_live:
        print("This would spend real credits. Re-run with --allow-live if intended.")
        return 3

    api = Client(args.base_url)

    heading("1. Account and project")
    api.call("GET", "/auth/csrf")
    email = f"e2e-{uuid.uuid4().hex[:8]}@example.org"
    status, _ = api.call(
        "POST", "/auth/register", {"email": email, "name": "E2E", "password": PASSWORD}
    )
    check("register", status == 201, str(status))

    status, project = api.call(
        "POST",
        "/projects",
        {
            "name": "KPC-2 nanobody binders",
            "infection": {
                "name": "Carbapenem-resistant Klebsiella pneumoniae",
                "pathogen": "Klebsiella pneumoniae",
            },
            "budget_usd": "25",
        },
    )
    check("create project", status == 201, str(status))
    if not project:
        return 1
    project_id = project["id"]

    heading("2. Find the target (UniProt + RCSB + PubMed)")
    status, found = api.call(
        "POST",
        f"/projects/{project_id}/targets/search",
        {"query": 'protein_name:"carbapenem-hydrolyzing beta-lactamase KPC"'},
    )
    check("target search", status == 200, str(status))
    if found:
        check("no sources degraded", not found["degraded_sources"], str(found["degraded_sources"]))
        print(f"      {len(found['items'])} candidates")

    status, target = api.call("POST", f"/projects/{project_id}/targets", {"accession": KPC2})
    check("persist target with evidence", status == 201, str(status))
    if not target:
        return 1
    target_id = target["id"]
    print(f"      {target['protein_name'][:50]}")
    print(f"      {len(target['structures'])} structures, {len(target['references'])} references")
    check("target is designable", bool(target["structures"]))

    heading("3. Choose the epitope (author numbering in, indices out)")
    status, updated = api.call(
        "PUT",
        f"/projects/{project_id}/targets/{target_id}/epitope",
        {
            "pdb_id": "2OV5",
            "chain_id": "A",
            "author_residues": CATALYTIC,
            # Prove the conversion rather than trusting it.
            "expected_residues": {"70": "Ser", "73": "Lys", "130": "Ser", "166": "Glu"},
        },
    )
    check("set epitope", status == 200, str(status))
    if not updated:
        return 1
    epitope = updated["epitope"]
    print(f"      author {epitope['author_residues']} -> indices {epitope['design_indices']}")
    print(f"      residues selected: {epitope['residue_letters']}")
    print(
        f"      numbering: {epitope['mapping']['author_numbering']}, "
        f"gaps {epitope['mapping']['gaps']}"
    )
    check("mapping verified against expected residues", epitope["verified"] is True)
    check(
        "catalytic residues selected",
        epitope["residue_letters"] == "SKSE",
        epitope["residue_letters"],
    )
    check("indices are the converted ones", epitope["design_indices"] == [39, 42, 99, 135])

    heading("4. Estimate, and refuse an unconfirmed price")
    status, estimate = api.call(
        "POST",
        f"/projects/{project_id}/runs/estimate",
        {"target_id": target_id, "num_proteins": args.designs},
    )
    check("estimate", status == 200, str(status))
    if not estimate:
        return 1
    print(
        f"      ${estimate['total_usd']} for {estimate['units']} designs "
        f"(${estimate['unit_usd']} each)"
    )
    print(f"      budget remaining: ${estimate['budget_remaining_usd']}")
    check("within budget", estimate["within_budget"] is True)

    status, _ = api.call(
        "POST",
        f"/projects/{project_id}/runs",
        {
            "request": {"target_id": target_id, "num_proteins": args.designs},
            "confirmed_cost_usd": "0.01",
        },
    )
    check("a wrong confirmed price is refused", status == 409, str(status))

    heading("5. Submit with the confirmed price")
    status, run = api.call(
        "POST",
        f"/projects/{project_id}/runs",
        {
            "request": {"target_id": target_id, "num_proteins": args.designs},
            "confirmed_cost_usd": estimate["total_usd"],
        },
    )
    check("submit", status == 202, str(status))
    if not run:
        return 1
    run_id = run["id"]
    print(f"      run {run_id}")
    print(f"      engine job {run['engine_job_id']} livemode={run['livemode']}")
    check("engine job created", bool(run["engine_job_id"]))
    check("livemode matches the key", run["livemode"] == (mode == "live"))

    _, budget = api.call("GET", f"/projects/{project_id}/budget")
    if budget:
        print(f"      reserved: ${budget['reserved_usd']}, remaining: ${budget['remaining_usd']}")
        check("budget was reserved", Decimal(budget["reserved_usd"]) > 0)

    heading("6. The reconciler polls, harvests and mirrors")
    deadline = time.monotonic() + args.timeout
    last = None
    final: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        _, current = api.call("GET", f"/projects/{project_id}/runs/{run_id}")
        if not current:
            break
        line = (
            f"status={current['status']} generated="
            f"{current['progress']['generated']} harvested={current['progress']['harvested']}"
        )
        if line != last:
            print(f"      {line}")
            last = line
        if current["status"] in (
            "complete",
            "failed",
            "stopped",
            "harvest_partial",
            "submit_failed",
        ):
            final = current
            break
        time.sleep(5)

    check("run reached a terminal state", final is not None)
    if final is None:
        return 1
    check(
        "run completed",
        final["status"] == "complete",
        f"{final['status']}: {final.get('error')}",
    )
    check(
        "all designs harvested",
        final["progress"]["harvested"] == args.designs,
        str(final["progress"]["harvested"]),
    )

    _, budget = api.call("GET", f"/projects/{project_id}/budget")
    if budget:
        print(
            f"      after settle: spent ${budget['spent_usd']}, reserved ${budget['reserved_usd']}"
        )
        check("reservation released", Decimal(budget["reserved_usd"]) == 0)
        if mode != "live":
            check("test-mode run was not billed", Decimal(budget["spent_usd"]) == 0)

    heading("7. Candidates, with structures served from our own storage")
    status, candidates = api.call("GET", f"/projects/{project_id}/runs/{run_id}/candidates")
    check("list candidates", status == 200, str(status))
    if not candidates:
        return 1
    print(f"      {candidates['total']} candidates, livemode={candidates['livemode']}")
    check("candidate count matches", candidates["total"] == args.designs)

    top = candidates["items"][:3]
    print("\n      top 3 by binding confidence:")
    for c in top:
        m = c["metrics"]
        print(
            f"        {c['id'][:8]}  bind={m.get('binding_confidence')} "
            f"iptm={m.get('iptm')} pae={m.get('min_interaction_pae')} "
            f"seq={len(c['sequences'][0]) if c['sequences'] else 0}aa"
        )
    check("candidates carry sequences", all(c["sequences"] for c in top))
    check(
        "candidates carry binding metrics",
        all(c["metrics"].get("binding_confidence") is not None for c in top),
    )
    check("structures were mirrored to our storage", all(c["structure_key"] for c in top))

    first = top[0]
    req = urllib.request.Request(
        f"{args.base_url.rstrip('/')}/api/v1/projects/{project_id}"
        f"/candidates/{first['id']}/structure.cif"
    )
    token = next((c.value for c in _jar if c.name == "biostruct_csrf"), None)
    if token:
        req.add_header("x-csrf-token", token)
    try:
        with _opener.open(req, timeout=60) as r:
            cif = r.read().decode("utf-8", "replace")
        print(
            f"\n      structure.cif served: {len(cif)} bytes, starts {cif.splitlines()[0][:30]!r}"
        )
        check("structure is served from our storage", cif.lstrip().startswith("data_"))
    except urllib.error.HTTPError as e:
        check("structure is served from our storage", False, f"HTTP {e.code}")

    heading("8. Ranking, computed from stored metrics")
    status, ranking = api.call("GET", f"/projects/{project_id}/runs/{run_id}/ranking")
    check("ranking", status == 200, str(status))
    if ranking:
        print(f"      {ranking['total']} ranked")
        print(f"      weights: { {k: v for k, v in ranking['weights'].items()} }")
        if ranking["metrics_absent_everywhere"]:
            print(f"      absent across the run: {ranking['metrics_absent_everywhere']}")
        print()
        for item in ranking["items"][:3]:
            print(
                f"      #{item['rank']} score={item['score']} "
                f"({item['percentile']}th pct) verdict={item['verdict']}"
            )
            for c in item["contributions"]:
                arrow = "higher better" if c["higher_is_better"] else "lower better"
                flag = " [constant]" if c["is_constant"] else ""
                print(
                    f"           {c['label']:28s} raw={c['raw']:<8} "
                    f"norm={c['normalised']} x{c['weight']} = {c['contribution']}"
                    f"  ({arrow}){flag}"
                )
            print(f"           {item['explanation'][:150]}...")
            print()

        check(
            "ranks are sequential",
            [i["rank"] for i in ranking["items"]] == list(range(1, len(ranking["items"]) + 1)),
        )
        scores = [Decimal(i["score"]) for i in ranking["items"]]
        check("scores descend", scores == sorted(scores, reverse=True))
        check(
            "contributions sum to the score",
            all(
                abs(
                    sum(Decimal(c["contribution"]) for c in i["contributions"])
                    - Decimal(i["score"])
                )
                < Decimal("0.001")
                for i in ranking["items"]
            ),
        )
        check("every score is explained", all(i["explanation"] for i in ranking["items"]))
        check("the caveat is present", "therapeutic effect" in ranking["caveat"])
        check(
            "missing ipsae_min was redistributed, not zeroed",
            all(i["weight_redistributed"] for i in ranking["items"]),
        )

        heading("9. Re-ranking with different weights costs nothing")
        status, _ = api.call(
            "PUT",
            f"/projects/{project_id}/ranking-weights",
            {
                "binding_confidence": "0.05",
                "iptm": "0.05",
                "ipsae_min": "0.0",
                "min_interaction_pae": "0.85",
                "structure_confidence": "0.05",
            },
        )
        check("weights updated", status == 200, str(status))
        _, reranked = api.call("GET", f"/projects/{project_id}/runs/{run_id}/ranking")
        if reranked:
            before = [i["candidate"]["id"] for i in ranking["items"]]
            after = [i["candidate"]["id"] for i in reranked["items"]]
            print(f"      default order : {[b[:8] for b in before[:5]]}")
            print(f"      PAE-weighted  : {[a[:8] for a in after[:5]]}")
            check("the order changed", before != after)
            top = reranked["items"][0]
            pae = next(c for c in top["contributions"] if c["metric"] == "min_interaction_pae")
            check(
                "the new top candidate has the best interface error",
                pae["normalised"] == "1.0000",
                pae["normalised"],
            )

    heading("RESULT")
    if _failures:
        print(f"{len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("All checks passed. Disease to ranked candidates works end to end.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
