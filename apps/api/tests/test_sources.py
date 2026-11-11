"""Tests for the scientific data sources.

Parsers run against responses captured live on 2026-11-11, so a schema change at
UniProt, RCSB or NCBI fails the build rather than quietly emptying an evidence
panel. No network.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from biostruct.sources.http import (
    CachedHTTPClient,
    Degradation,
    SourceUnavailable,
    TokenBucket,
)
from biostruct.sources.pubmed import PubMedClient
from biostruct.sources.rcsb import RCSBClient, cif_url
from biostruct.sources.science import ScienceSources
from biostruct.sources.uniprot import UniProtClient

FIXTURES = Path(__file__).parent / "fixtures"


def load(*parts: str) -> Any:
    return json.loads((FIXTURES.joinpath(*parts)).read_text())


def client_for(source: str, handler: Any, **kwargs: Any) -> CachedHTTPClient:
    return CachedHTTPClient(
        source=source,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        rate_per_second=1000.0,
        **kwargs,
    )


def respond(payload: Any) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return handler


# --- UniProt ---------------------------------------------------------------


class TestUniProtParsing:
    async def test_parses_the_captured_search(self) -> None:
        client = UniProtClient(client_for("uniprot", respond(load("uniprot", "kpc_search.json"))))
        targets = await client.search("kpc")
        assert len(targets) == 2
        kpc2 = next(t for t in targets if t.accession == "Q9F663")
        assert kpc2.length == 293
        assert kpc2.organism == "Klebsiella pneumoniae"
        assert kpc2.reviewed is True
        assert "KPC-2" in kpc2.protein_name

    async def test_evidence_level_is_in_plain_words(self) -> None:
        """ "Swiss-Prot" means nothing to most researchers."""
        client = UniProtClient(client_for("uniprot", respond(load("uniprot", "q9f663.json"))))
        target = await client.get("Q9F663")
        assert target.evidence_level == "reviewed (Swiss-Prot)"

    async def test_pdb_cross_references_are_collected(self) -> None:
        client = UniProtClient(client_for("uniprot", respond(load("uniprot", "q9f663.json"))))
        target = await client.get("Q9F663")
        assert len(target.pdb_ids) == 86
        assert "2OV5" in target.pdb_ids
        assert target.has_structure is True

    async def test_active_sites_are_parsed(self) -> None:
        """These are UniProt-numbered, which is NOT the author numbering."""
        client = UniProtClient(client_for("uniprot", respond(load("uniprot", "q9f663.json"))))
        target = await client.get("Q9F663")
        positions = [s.position for s in target.active_sites]
        assert positions == [69, 72, 129, 165]
        assert "Nucleophile" in target.active_sites[0].description

    async def test_function_annotation_is_extracted(self) -> None:
        client = UniProtClient(client_for("uniprot", respond(load("uniprot", "q9f663.json"))))
        target = await client.get("Q9F663")
        assert target.function

    async def test_reviewed_only_is_sent_in_the_query(self) -> None:
        """An unfiltered search buries curated entries under predicted ones."""
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.params.get("query", ""))
            return httpx.Response(200, json={"results": []})

        client = UniProtClient(client_for("uniprot", handler))
        await client.search("kpc", reviewed_only=True)
        assert "reviewed:true" in seen[0]
        await client.search("kpc", reviewed_only=False)
        assert "reviewed:true" not in seen[1]

    async def test_organism_filter_is_sent(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.params.get("query", ""))
            return httpx.Response(200, json={"results": []})

        await UniProtClient(client_for("uniprot", handler)).search("kpc", organism_id=573)
        assert "organism_id:573" in seen[0]

    async def test_empty_results_are_not_an_error(self) -> None:
        client = UniProtClient(client_for("uniprot", respond({"results": []})))
        assert await client.search("nonsense") == []


# --- RCSB ------------------------------------------------------------------


class TestRCSBParsing:
    async def test_parses_a_structure_entry(self) -> None:
        client = RCSBClient(client_for("rcsb", respond(load("rcsb", "2ov5_entry.json"))))
        structure = await client.get_structure("2ov5")
        assert structure.pdb_id == "2OV5"
        assert structure.resolution_a == 1.85
        assert structure.method == "X-RAY DIFFRACTION"
        assert "KPC-2" in structure.title

    async def test_quality_note_is_readable(self) -> None:
        client = RCSBClient(client_for("rcsb", respond(load("rcsb", "2ov5_entry.json"))))
        structure = await client.get_structure("2ov5")
        assert "1.85" in structure.quality_note
        assert "high resolution" in structure.quality_note

    def test_cif_url_points_at_rcsb(self) -> None:
        """This exact URL is handed to the design engine, verified accepted."""
        assert cif_url("2ov5") == "https://files.rcsb.org/download/2OV5.cif"

    async def test_search_returns_pdb_ids(self) -> None:
        payload = {"result_set": [{"identifier": "9f0t"}, {"identifier": "2ov5"}]}
        client = RCSBClient(client_for("rcsb", respond(payload)))
        assert await client.structures_for_uniprot("Q9F663") == ["9F0T", "2OV5"]

    async def test_no_hits_is_an_empty_list_not_an_error(self) -> None:
        """RCSB answers "no matches" with a 404, which is not an outage."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={})

        client = RCSBClient(client_for("rcsb", handler))
        assert await client.structures_for_uniprot("Q00000") == []

    async def test_residue_map_is_built_from_two_calls(self) -> None:
        instance = load("rcsb", "2ov5_instance_a.json")
        entity = load("rcsb", "2ov5_entity_1.json")

        def handler(request: httpx.Request) -> httpx.Response:
            if "polymer_entity_instance" in str(request.url):
                return httpx.Response(200, json=instance)
            return httpx.Response(200, json=entity)

        rmap = await RCSBClient(client_for("rcsb", handler)).get_residue_map("2OV5", "A")
        assert rmap.length == 261
        assert rmap.to_index(70) == 39
        assert rmap.sequence[39] == "S"

    async def test_missing_mapping_refuses_rather_than_guessing(self) -> None:
        """Without the mapping an epitope cannot be converted safely at all."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"rcsb_polymer_entity_instance_container_identifiers": {}}
            )

        client = RCSBClient(client_for("rcsb", handler))
        with pytest.raises(SourceUnavailable, match="no residue mapping"):
            await client.get_residue_map("2OV5", "A")


# --- PubMed ----------------------------------------------------------------


class TestPubMedParsing:
    async def test_parses_search_and_summaries(self) -> None:
        esearch = load("pubmed", "esearch.json")
        esummary = load("pubmed", "esummary.json")

        def handler(request: httpx.Request) -> httpx.Response:
            if "esearch" in str(request.url):
                return httpx.Response(200, json=esearch)
            return httpx.Response(200, json=esummary)

        refs = await PubMedClient(client_for("pubmed", handler)).search("kpc-2")
        assert len(refs) == 3
        assert all(r.pmid and r.title for r in refs)
        assert refs[0].url.startswith("https://pubmed.ncbi.nlm.nih.gov/")

    async def test_doi_and_journal_are_extracted(self) -> None:
        esearch = load("pubmed", "esearch.json")
        esummary = load("pubmed", "esummary.json")

        def handler(request: httpx.Request) -> httpx.Response:
            if "esearch" in str(request.url):
                return httpx.Response(200, json=esearch)
            return httpx.Response(200, json=esummary)

        refs = await PubMedClient(client_for("pubmed", handler)).search("kpc-2")
        assert any(r.doi for r in refs)
        assert all(r.journal for r in refs)

    async def test_relevance_order_is_preserved(self) -> None:
        """esummary returns a dict; its order is not the relevance order."""
        esearch = {"esearchresult": {"idlist": ["333", "111", "222"]}}
        esummary = {
            "result": {
                "111": {"title": "one"},
                "222": {"title": "two"},
                "333": {"title": "three"},
            }
        }

        def handler(request: httpx.Request) -> httpx.Response:
            if "esearch" in str(request.url):
                return httpx.Response(200, json=esearch)
            return httpx.Response(200, json=esummary)

        refs = await PubMedClient(client_for("pubmed", handler)).search("x")
        assert [r.pmid for r in refs] == ["333", "111", "222"]

    async def test_no_results_skips_the_summary_call(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(200, json={"esearchresult": {"idlist": []}})

        assert await PubMedClient(client_for("pubmed", handler)).search("x") == []
        assert len(calls) == 1

    async def test_tool_and_email_are_sent_as_ncbi_asks(self) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(dict(request.url.params))
            return httpx.Response(200, json={"esearchresult": {"idlist": []}})

        client = PubMedClient(client_for("pubmed", handler), email="ops@example.org")
        await client.search("x")
        assert seen["tool"] == "biostruct"
        assert seen["email"] == "ops@example.org"


# --- transport behaviour ---------------------------------------------------


class TestRetriesAndErrors:
    async def test_retries_on_server_error_then_succeeds(self) -> None:
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            if attempts["n"] < 3:
                return httpx.Response(503, text="later")
            return httpx.Response(200, json={"ok": True})

        client = client_for("test", handler)
        assert await client.get_json("https://example.org/x") == {"ok": True}
        assert attempts["n"] == 3

    async def test_gives_up_after_max_attempts(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="down")

        client = client_for("test", handler, max_attempts=2)
        with pytest.raises(SourceUnavailable, match="after 2 attempts"):
            await client.get_json("https://example.org/x")

    async def test_client_errors_are_not_retried(self) -> None:
        """Our request is wrong; retrying just wastes the source's capacity."""
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            return httpx.Response(400, text="bad query")

        with pytest.raises(SourceUnavailable):
            await client_for("test", handler).get_json("https://example.org/x")
        assert attempts["n"] == 1

    async def test_404_is_distinguished_from_an_outage(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        with pytest.raises(SourceUnavailable, match="not found"):
            await client_for("test", handler).get_json("https://example.org/x")

    async def test_rate_limit_is_retried(self) -> None:
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            return httpx.Response(429 if attempts["n"] == 1 else 200, json={"ok": 1})

        assert await client_for("test", handler).get_json("https://example.org/x") == {"ok": 1}

    async def test_non_json_response_is_an_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>maintenance</html>")

        with pytest.raises(SourceUnavailable, match="not JSON"):
            await client_for("test", handler).get_json("https://example.org/x")


class TestTokenBucket:
    async def test_allows_the_burst_immediately(self) -> None:
        bucket = TokenBucket(1.0, burst=3)
        start = time.monotonic()
        for _ in range(3):
            await bucket.acquire()
        assert time.monotonic() - start < 0.1

    async def test_throttles_beyond_the_burst(self) -> None:
        """NCBI blocks rather than throttles, so this must actually hold us back."""
        bucket = TokenBucket(20.0, burst=1)
        await bucket.acquire()
        start = time.monotonic()
        await bucket.acquire()
        assert time.monotonic() - start >= 0.03

    async def test_concurrent_callers_are_serialised(self) -> None:
        bucket = TokenBucket(50.0, burst=1)
        start = time.monotonic()
        await asyncio.gather(*(bucket.acquire() for _ in range(5)))
        assert time.monotonic() - start >= 0.06


class TestDegradation:
    def test_records_and_reports_failures(self) -> None:
        d = Degradation()
        assert d.any is False
        d.record("pubmed", "HTTP 429")
        assert d.any is True
        assert d.sources == ["pubmed"]
        assert d.failures["pubmed"] == "HTTP 429"

    def test_sources_are_sorted_for_stable_output(self) -> None:
        d = Degradation()
        d.record("rcsb", "x")
        d.record("pubmed", "y")
        assert d.sources == ["pubmed", "rcsb"]


class TestEvidenceFanOut:
    """A partial panel must be labelled partial, never shown as complete."""

    async def test_pubmed_failure_degrades_rather_than_fails(self) -> None:
        uniprot = load("uniprot", "q9f663.json")

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "rest.uniprot.org" in url:
                return httpx.Response(200, json=uniprot)
            if "eutils" in url:
                return httpx.Response(503, text="NCBI down")
            if "search.rcsb.org" in url:
                return httpx.Response(200, json={"result_set": [{"identifier": "2OV5"}]})
            return httpx.Response(200, json=load("rcsb", "2ov5_entry.json"))

        sources = ScienceSources(
            uniprot=UniProtClient(client_for("uniprot", handler)),
            rcsb=RCSBClient(client_for("rcsb", handler)),
            pubmed=PubMedClient(client_for("pubmed", handler, max_attempts=1)),
        )
        evidence = await sources.gather_evidence("Q9F663")
        assert evidence.target.accession == "Q9F663"
        assert evidence.structures, "structures should still be returned"
        assert evidence.references == []
        assert evidence.degradation.sources == ["pubmed"]
        assert evidence.summary()["degraded_sources"] == ["pubmed"]

    async def test_rcsb_failure_leaves_the_target_undesignable(self) -> None:
        uniprot = load("uniprot", "q9f663.json")
        esearch = load("pubmed", "esearch.json")
        esummary = load("pubmed", "esummary.json")

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "rest.uniprot.org" in url:
                return httpx.Response(200, json=uniprot)
            if "esearch" in url:
                return httpx.Response(200, json=esearch)
            if "esummary" in url:
                return httpx.Response(200, json=esummary)
            return httpx.Response(503, text="RCSB down")

        sources = ScienceSources(
            uniprot=UniProtClient(client_for("uniprot", handler)),
            rcsb=RCSBClient(client_for("rcsb", handler, max_attempts=1)),
            pubmed=PubMedClient(client_for("pubmed", handler)),
        )
        evidence = await sources.gather_evidence("Q9F663")
        assert evidence.references
        assert evidence.is_designable is False
        assert "rcsb" in evidence.degradation.sources

    async def test_best_structure_is_the_highest_resolution(self) -> None:
        uniprot = load("uniprot", "q9f663.json")
        entry = load("rcsb", "2ov5_entry.json")
        better = json.loads(json.dumps(entry))
        better["rcsb_entry_info"]["resolution_combined"] = [0.98]

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "rest.uniprot.org" in url:
                return httpx.Response(200, json=uniprot)
            if "search.rcsb.org" in url:
                return httpx.Response(
                    200,
                    json={"result_set": [{"identifier": "2OV5"}, {"identifier": "9F0T"}]},
                )
            if "9F0T" in url:
                return httpx.Response(200, json=better)
            if "core/entry" in url:
                return httpx.Response(200, json=entry)
            return httpx.Response(200, json={"esearchresult": {"idlist": []}})

        sources = ScienceSources(
            uniprot=UniProtClient(client_for("uniprot", handler)),
            rcsb=RCSBClient(client_for("rcsb", handler)),
            pubmed=PubMedClient(client_for("pubmed", handler)),
        )
        evidence = await sources.gather_evidence("Q9F663")
        best = evidence.best_structure
        assert best is not None
        assert best.resolution_a == 0.98

    async def test_uniprot_failure_in_search_returns_nothing_but_says_why(self) -> None:
        """UniProt is the spine of this step, so it degrades to empty, loudly."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="down")

        sources = ScienceSources(
            uniprot=UniProtClient(client_for("uniprot", handler, max_attempts=1)),
            rcsb=RCSBClient(client_for("rcsb", handler)),
            pubmed=PubMedClient(client_for("pubmed", handler)),
        )
        result = await sources.search_targets("kpc")
        assert result.targets == []
        assert result.degradation.sources == ["uniprot"]
