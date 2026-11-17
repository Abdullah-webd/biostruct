"""Hosted Boltz implementation of :class:`DesignEngine`.

This is the only module in the codebase that speaks the Boltz wire format.

It talks HTTP directly rather than through the ``boltz_api`` SDK. The SDK is
useful as a *schema reference* — reading its generated types is how the wire
format was discovered, since Boltz publishes no OpenAPI spec — but its own
convenience ``run()`` helper blocks while polling, and our reconciler must own
polling. Using httpx directly also keeps the dependency surface honest: an SDK
minor bump cannot change our parsing, only our fixtures.

Everything here matches responses observed from the live API on 2026-11-11; see
``docs/boltz-api-observed.md`` for the capture and the places where the SDK's
declared types disagree with reality.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, cast

import httpx

from biostruct.engines.base import (
    Artifact,
    CostEstimate,
    CuratedBinder,
    DesignEntity,
    DesignMetrics,
    DesignResult,
    DesignRules,
    DesignSpec,
    DesignWarning,
    EngineIdentity,
    EngineJob,
    EngineRejected,
    EngineUnavailable,
    JobStatus,
    SequenceTarget,
    StructureTarget,
)
from biostruct.engines.prediction import (
    MODEL as PREDICTION_MODEL,
)
from biostruct.engines.prediction import (
    Entity,
    LigandBinding,
    PredictedSample,
    PredictionJob,
    PredictionSpec,
    ProteinBinding,
    StructureMetrics,
)
from biostruct.engines.screening import (
    ADME_MODEL,
    Adme,
    AdmeJob,
    AdmeResult,
    AdmeSpec,
    MoleculeDesignSpec,
    MoleculeJob,
    MoleculeMetrics,
    MoleculeProgress,
    MoleculeResult,
    MoleculeWarning,
    ScreenSpec,
    ScreenTarget,
    Solubility,
)

log = logging.getLogger(__name__)

BASE_URL = "https://api.boltz.bio"
API_PREFIX = "/compute/v1"

#: Step 2's endpoint family. Billed per sample, $0.0250 each as of 2026-11-17.
PREDICTION_PATH = "/predictions/structure-and-binding"

#: Step 3's endpoints. Billed per molecule: $0.0250 to screen, $0.0100 for
#: ADME alone, so ADME is the cheap way to triage a library before screening.
SCREEN_PATH = "/small-molecule/library-screen"
MOLECULE_DESIGN_PATH = "/small-molecule/design"
ADME_PATH = "/predictions/adme"

# Submission should fail fast; result pages carry more data and get longer.
TIMEOUT_SUBMIT = httpx.Timeout(30.0, connect=10.0)
TIMEOUT_READ = httpx.Timeout(60.0, connect=10.0)


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _money(value: Any) -> Decimal:
    """The API returns money as a string ("0.0500"). Never route it via float."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        raise TypeError("refusing to parse money from a float")
    return Decimal(str(value))


def _opt_float(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None


class BoltzHostedEngine:
    """BoltzProt-1 for design, over the hosted Boltz API."""

    name = "boltz-hosted"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("a Boltz API key is required")
        self._api_key = api_key
        self._base = base_url.rstrip("/") + API_PREFIX
        self._client = client
        self._owns_client = client is None

    # --- plumbing ----------------------------------------------------------

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"x-api-key": self._api_key, "accept": "application/json"},
                timeout=TIMEOUT_READ,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        http_timeout: httpx.Timeout | None = None,
    ) -> dict[str, Any]:
        client = await self._http()
        try:
            response = await client.request(
                method,
                self._base + path,
                json=json,
                params=params,
                headers={"x-api-key": self._api_key, "accept": "application/json"},
                timeout=http_timeout or TIMEOUT_READ,
            )
        except httpx.HTTPError as exc:
            # Transport failures are retryable and must not fail a run.
            raise EngineUnavailable(f"Boltz request failed: {exc}") from exc

        if response.status_code >= 500:
            raise EngineUnavailable(f"Boltz returned {response.status_code} for {method} {path}")
        if response.status_code == 429:
            raise EngineUnavailable("Boltz rate limited this request")
        if response.status_code >= 400:
            payload: dict[str, Any] = {}
            with contextlib.suppress(ValueError):
                payload = response.json()
            raise EngineRejected(
                payload.get("message") or response.text[:300] or "request rejected",
                status_code=response.status_code,
                code=str(payload.get("code", "")),
            )
        if not response.content:
            return {}
        return cast(dict[str, Any], response.json())

    # --- request building --------------------------------------------------

    @staticmethod
    def _rules_payload(rules: DesignRules) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if rules.excluded_amino_acids:
            payload["excluded_amino_acids"] = list(rules.excluded_amino_acids)
        if rules.excluded_sequence_motifs:
            payload["excluded_sequence_motifs"] = list(rules.excluded_sequence_motifs)
        if rules.max_hydrophobic_fraction is not None:
            payload["max_hydrophobic_fraction"] = float(rules.max_hydrophobic_fraction)
        return payload

    @classmethod
    def _binder_payload(cls, binder: CuratedBinder) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": "boltz_curated",
            "binder": f"boltz_{binder.kind}",
        }
        rules = cls._rules_payload(binder.rules)
        if rules:
            payload["rules"] = rules
        return payload

    @staticmethod
    def _target_payload(spec: DesignSpec) -> dict[str, Any]:
        target = spec.target
        if isinstance(target, SequenceTarget):
            return {
                "type": "no_template",
                "entities": [
                    {"type": "protein", "value": seq, "chain_ids": [chain_id]}
                    for chain_id, seq in target.sequences
                ],
            }
        if isinstance(target, StructureTarget):
            structure: dict[str, Any] = (
                {"type": "url", "url": target.structure_url}
                if target.structure_url
                else {
                    "type": "base64",
                    "media_type": "chemical/x-cif",
                    "data": target.structure_cif_base64,
                }
            )
            chain_selection: dict[str, Any] = {}
            for chain in target.chains:
                entry: dict[str, Any] = {
                    "chain_type": "polymer",
                    "crop_residues": (
                        "all" if chain.crop_residues == "all" else sorted(set(chain.crop_residues))
                    ),
                }
                if chain.epitope_residues:
                    entry["epitope_residues"] = sorted(set(chain.epitope_residues))
                if chain.non_binding_residues:
                    entry["non_binding_residues"] = sorted(set(chain.non_binding_residues))
                if chain.flexible_residues:
                    entry["flexible_residues"] = sorted(set(chain.flexible_residues))
                chain_selection[chain.chain_id] = entry
            return {
                "type": "structure_template",
                "structure": structure,
                "chain_selection": chain_selection,
            }
        raise TypeError(f"unsupported target type: {type(target)!r}")

    def _design_payload(self, spec: DesignSpec) -> dict[str, Any]:
        return {
            "target": self._target_payload(spec),
            "binder_specification": self._binder_payload(spec.binder),
            "num_proteins": spec.num_proteins,
        }

    # --- response parsing --------------------------------------------------

    @staticmethod
    def parse_job(payload: dict[str, Any]) -> EngineJob:
        progress = payload.get("progress") or {}
        raw_status = str(payload.get("status", "")).lower()
        try:
            status = JobStatus(raw_status)
        except ValueError:
            # An unknown status must not crash the reconciler. Treat it as
            # still running so the next poll re-reads it.
            log.warning("unknown Boltz job status %r, treating as running", raw_status)
            status = JobStatus.running
        error = payload.get("error")
        return EngineJob(
            job_id=str(payload.get("id", "")),
            status=status,
            # Absent livemode is the dangerous default, so assume test.
            livemode=bool(payload.get("livemode", False)),
            total_to_generate=progress.get("total_proteins_to_generate"),
            generated=int(progress.get("num_proteins_generated") or 0),
            engine=str(payload.get("engine", "")),
            engine_version=str(payload.get("engine_version", "")),
            error=str(error) if error else None,
            created_at=_parse_dt(payload.get("created_at")),
            started_at=_parse_dt(payload.get("started_at")),
            completed_at=_parse_dt(payload.get("completed_at")),
        )

    @staticmethod
    def _parse_artifact(payload: Any) -> Artifact | None:
        if not isinstance(payload, dict) or not payload.get("url"):
            return None
        return Artifact(
            url=str(payload["url"]),
            expires_at=_parse_dt(payload.get("url_expires_at")),
        )

    @classmethod
    def parse_result(cls, payload: dict[str, Any]) -> DesignResult:
        metrics_raw = payload.get("metrics") or {}
        artifacts = payload.get("artifacts") or {}
        return DesignResult(
            result_id=str(payload.get("id", "")),
            entities=tuple(
                DesignEntity(
                    chain_ids=tuple(e.get("chain_ids") or ()),
                    kind=str(e.get("type", "")),
                    value=str(e.get("value", "")),
                )
                for e in payload.get("entities") or []
                if isinstance(e, dict)
            ),
            metrics=DesignMetrics(
                # structure_confidence is the one metric present on every
                # result shape, binder or generic.
                structure_confidence=float(metrics_raw.get("structure_confidence") or 0.0),
                helix_fraction=_opt_float(metrics_raw.get("helix_fraction")),
                sheet_fraction=_opt_float(metrics_raw.get("sheet_fraction")),
                loop_fraction=_opt_float(metrics_raw.get("loop_fraction")),
                binding_confidence=_opt_float(metrics_raw.get("binding_confidence")),
                iptm=_opt_float(metrics_raw.get("iptm")),
                min_interaction_pae=_opt_float(metrics_raw.get("min_interaction_pae")),
                ipsae_min=_opt_float(metrics_raw.get("ipsae_min")),
            ),
            structure=cls._parse_artifact(artifacts.get("structure")),
            archive=cls._parse_artifact(artifacts.get("archive")),
            warnings=tuple(
                DesignWarning(code=str(w.get("code", "")), message=str(w.get("message", "")))
                for w in payload.get("warnings") or []
                if isinstance(w, dict)
            ),
            created_at=_parse_dt(payload.get("created_at")),
            raw=payload,
        )

    # --- DesignEngine ------------------------------------------------------

    async def whoami(self) -> EngineIdentity:
        payload = await self._request("GET", "/auth/me")
        mode = str(payload.get("mode", "")).lower()
        return EngineIdentity(
            key_type=str(payload.get("key_type", "")),
            mode=cast(
                Literal["test", "live", "unknown"],
                mode if mode in ("test", "live") else "unknown",
            ),
            workspace_id=str(payload.get("workspace_id", "")),
            organization_name=str(payload.get("organization_name", "")),
        )

    async def estimate_cost(self, spec: DesignSpec) -> CostEstimate:
        payload = await self._request(
            "POST", "/protein/design/estimate-cost", json=self._design_payload(spec)
        )
        breakdown = payload.get("breakdown") or {}
        return CostEstimate(
            total_usd=_money(payload.get("estimated_cost_usd", "0")),
            unit_usd=_money(breakdown.get("cost_per_unit_usd", "0")),
            units=int(breakdown.get("num_units") or spec.num_proteins),
            application=str(breakdown.get("application", "protein_design")),
            disclaimer=str(payload.get("disclaimer", "")),
        )

    async def start_design(self, spec: DesignSpec, *, idempotency_key: str) -> EngineJob:
        if not idempotency_key:
            # Without this a retried submission can start — and bill — a second run.
            raise ValueError("idempotency_key is required when starting a design run")
        body = self._design_payload(spec)
        body["idempotency_key"] = idempotency_key
        payload = await self._request(
            "POST", "/protein/design", json=body, http_timeout=TIMEOUT_SUBMIT
        )
        return self.parse_job(payload)

    async def get_job(self, job_id: str) -> EngineJob:
        return self.parse_job(await self._request("GET", f"/protein/design/{job_id}"))

    async def iter_results(
        self, job_id: str, *, after_id: str | None = None, page_size: int = 100
    ) -> AsyncIterator[DesignResult]:
        """Page through results with the API's cursor.

        Pagination is cursor-based (``after_id`` / ``has_more`` / ``last_id``),
        not offset-based, so a run that grows between pages cannot duplicate or
        skip results. Verified against a 10-result run paged 4 at a time.
        """
        cursor = after_id
        # Each result becomes exactly one candidate document, so a duplicate id
        # from an overlapping page would duplicate a candidate. Deduplicate here
        # rather than trusting the server never to overlap.
        seen: set[str] = set()
        while True:
            params: dict[str, Any] = {"limit": page_size}
            if cursor:
                params["after_id"] = cursor
            page = await self._request("GET", f"/protein/design/{job_id}/results", params=params)
            rows = page.get("data") or []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                result = self.parse_result(row)
                if result.result_id in seen:
                    log.warning(
                        "Boltz returned duplicate result %s for %s; skipping",
                        result.result_id,
                        job_id,
                    )
                    continue
                seen.add(result.result_id)
                yield result
            if not page.get("has_more") or not rows:
                return
            last_id = page.get("last_id")
            if not last_id or last_id == cursor:
                # Defensive: a server that stops advancing the cursor would
                # otherwise spin forever.
                log.warning("Boltz results cursor did not advance for %s", job_id)
                return
            cursor = str(last_id)

    async def stop_design(self, job_id: str) -> EngineJob:
        return self.parse_job(await self._request("POST", f"/protein/design/{job_id}/stop"))

    async def resume_design(self, job_id: str) -> EngineJob:
        return self.parse_job(await self._request("POST", f"/protein/design/{job_id}/resume"))

    async def delete_data(self, job_id: str) -> None:
        await self._request("POST", f"/protein/design/{job_id}/delete-data")

    # --- StructureEngine ---------------------------------------------------
    #
    # Step 2 of the pipeline: fold a complex. A different application from
    # design, with its own endpoints, its own billing unit (samples, $0.0250
    # each as of 2026-11-17) and its own result shape. Paths are
    # ``/predictions/structure-and-binding``; see ``prediction.py`` for the
    # domain types and ``docs/boltz-api-observed.md`` Part 2 for the capture.

    @staticmethod
    def _entity_payload(entity: Entity) -> dict[str, Any]:
        return {
            "type": entity.kind,
            "value": entity.value,
            "chain_ids": list(entity.chain_ids),
        }

    @classmethod
    def _prediction_payload(cls, spec: PredictionSpec) -> dict[str, Any]:
        """Build the wire body.

        Unlike the library screen, this endpoint *does* take an ``input``
        wrapper and *does* take ``model``. The two applications disagree, which
        is easy to get backwards — sending ``model`` to the screen makes its
        validator demand a field that does not exist.
        """
        payload: dict[str, Any] = {
            "entities": [cls._entity_payload(e) for e in spec.entities],
            "num_samples": spec.num_samples,
        }

        match spec.binding:
            case LigandBinding(binder_chain_id=cid):
                payload["binding"] = {
                    "type": "ligand_protein_binding",
                    "binder_chain_id": cid,
                }
            case ProteinBinding(binder_chain_ids=cids):
                payload["binding"] = {
                    "type": "protein_protein_binding",
                    "binder_chain_ids": list(cids),
                }
            case None:
                pass

        if spec.options is not None:
            options: dict[str, Any] = {}
            if spec.options.recycling_steps is not None:
                options["recycling_steps"] = spec.options.recycling_steps
            if spec.options.sampling_steps is not None:
                options["sampling_steps"] = spec.options.sampling_steps
            if spec.options.seed is not None:
                options["seed"] = spec.options.seed
            if spec.options.step_scale is not None:
                options["step_scale"] = float(spec.options.step_scale)
            if options:
                payload["model_options"] = options

        return {"input": payload, "model": PREDICTION_MODEL}

    @staticmethod
    def _parse_metrics(raw: dict[str, Any]) -> StructureMetrics:
        return StructureMetrics(
            structure_confidence=float(raw.get("structure_confidence") or 0.0),
            ptm=_opt_float(raw.get("ptm")),
            iptm=_opt_float(raw.get("iptm")),
            ligand_iptm=_opt_float(raw.get("ligand_iptm")),
            protein_iptm=_opt_float(raw.get("protein_iptm")),
            complex_plddt=_opt_float(raw.get("complex_plddt")),
            complex_iplddt=_opt_float(raw.get("complex_iplddt")),
            complex_pde=_opt_float(raw.get("complex_pde")),
            complex_ipde=_opt_float(raw.get("complex_ipde")),
        )

    @classmethod
    def _parse_sample(cls, raw: dict[str, Any]) -> PredictedSample:
        return PredictedSample(
            metrics=cls._parse_metrics(raw.get("metrics") or {}),
            structure=cls._parse_artifact(raw.get("structure")),
        )

    @classmethod
    def parse_prediction(cls, payload: dict[str, Any]) -> PredictionJob:
        raw_status = str(payload.get("status", "")).lower()
        try:
            status = JobStatus(raw_status)
        except ValueError:
            # An unknown status must not crash the reconciler, and must not be
            # mistaken for a terminal one.
            log.warning("Boltz returned unknown prediction status %r", raw_status)
            status = JobStatus.running

        output = payload.get("output") or {}
        best_raw = output.get("best_sample")
        samples_raw = output.get("all_sample_results") or []

        error = payload.get("error")
        return PredictionJob(
            job_id=str(payload.get("id", "")),
            status=status,
            livemode=bool(payload.get("livemode", False)),
            model=str(payload.get("model", "")),
            version=str(payload.get("version", "")),
            error=str(error.get("message", "")) if isinstance(error, dict) else "",
            created_at=_parse_dt(payload.get("created_at")),
            started_at=_parse_dt(payload.get("started_at")),
            completed_at=_parse_dt(payload.get("completed_at")),
            expires_at=_parse_dt(payload.get("expires_at")),
            best=cls._parse_sample(best_raw) if isinstance(best_raw, dict) else None,
            samples=tuple(cls._parse_sample(row) for row in samples_raw if isinstance(row, dict)),
            archive=cls._parse_artifact(output.get("archive")),
        )

    async def estimate_prediction_cost(self, spec: PredictionSpec) -> CostEstimate:
        payload = await self._request(
            "POST",
            f"{PREDICTION_PATH}/estimate-cost",
            json=self._prediction_payload(spec),
        )
        breakdown = payload.get("breakdown") or {}
        return CostEstimate(
            total_usd=_money(payload.get("estimated_cost_usd", "0")),
            unit_usd=_money(breakdown.get("cost_per_unit_usd", "0")),
            units=int(breakdown.get("num_units") or spec.num_samples),
            application=str(breakdown.get("application", "structure_and_binding")),
            disclaimer=str(payload.get("disclaimer", "")),
        )

    async def start_prediction(
        self, spec: PredictionSpec, *, idempotency_key: str
    ) -> PredictionJob:
        if not idempotency_key:
            # Same rule as design: a retried submission must not bill twice.
            raise ValueError("idempotency_key is required when starting a prediction")
        body = self._prediction_payload(spec)
        body["idempotency_key"] = idempotency_key
        payload = await self._request(
            "POST", PREDICTION_PATH, json=body, http_timeout=TIMEOUT_SUBMIT
        )
        return self.parse_prediction(payload)

    async def get_prediction(self, job_id: str) -> PredictionJob:
        return self.parse_prediction(await self._request("GET", f"{PREDICTION_PATH}/{job_id}"))

    async def delete_prediction_data(self, job_id: str) -> None:
        await self._request("POST", f"{PREDICTION_PATH}/{job_id}/delete-data")

    # --- ScreeningEngine ---------------------------------------------------
    #
    # Step 3: which molecules stick to the target. Billed per molecule —
    # $0.0250 to screen, $0.0100 for ADME alone (2026-11-17).

    @staticmethod
    def _screen_target_payload(target: ScreenTarget) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "entities": [
                {"type": "protein", "chain_ids": [chain_id], "value": sequence}
                for chain_id, sequence in target.entities
            ]
        }
        if target.pocket_residues:
            payload["pocket_residues"] = {
                chain_id: sorted(set(residues))
                for chain_id, residues in target.pocket_residues.items()
            }
        if target.reference_ligands:
            payload["reference_ligands"] = list(target.reference_ligands)
        return payload

    @classmethod
    def _screen_payload(cls, spec: ScreenSpec) -> dict[str, Any]:
        """Build the screen body.

        **Flat, and with no ``model`` field** — unlike the prediction endpoint,
        which wraps in ``input`` and requires ``model``. Sending ``model`` here
        makes the union validator demand ``target_profile``, a field that does
        not exist; that error cost hours once and must not cost them twice.
        """
        molecules: list[dict[str, Any]] = []
        for molecule in spec.molecules:
            entry: dict[str, Any] = {"smiles": molecule.smiles}
            if molecule.external_id:
                entry["id"] = molecule.external_id
            molecules.append(entry)
        return {
            "target": cls._screen_target_payload(spec.target),
            "molecules": molecules,
            "molecule_filters": {"boltz_smarts_catalog_filter_level": spec.filter_level},
        }

    @staticmethod
    def _parse_adme(raw: Any) -> Adme:
        if not isinstance(raw, dict):
            return Adme()
        solubility = raw.get("solubility")
        return Adme(
            lipophilicity=_opt_float(raw.get("lipophilicity")),
            permeability=_opt_float(raw.get("permeability")),
            solubility=(cast(Solubility, solubility) if isinstance(solubility, str) else None),
        )

    @classmethod
    def parse_molecule_job(cls, payload: dict[str, Any]) -> MoleculeJob:
        raw_status = str(payload.get("status", "")).lower()
        try:
            status = JobStatus(raw_status)
        except ValueError:
            log.warning("Boltz returned unknown screen status %r", raw_status)
            status = JobStatus.running

        progress = payload.get("progress") or {}
        error = payload.get("error")
        return MoleculeJob(
            job_id=str(payload.get("id", "")),
            status=status,
            livemode=bool(payload.get("livemode", False)),
            # A screen counts "screened", a design run counts "generated".
            # Same meaning, two names, one type.
            progress=MoleculeProgress(
                total=int(
                    progress.get("total_molecules_to_screen")
                    or progress.get("total_molecules_to_generate")
                    or 0
                ),
                done=int(
                    progress.get("num_molecules_screened")
                    or progress.get("num_molecules_generated")
                    or 0
                ),
                failed=int(progress.get("num_molecules_failed") or 0),
                latest_result_id=str(progress.get("latest_result_id") or ""),
            ),
            # ``engine``/``engine_version`` carry the same values but are
            # deprecated, so read the pipeline fields and ignore them.
            pipeline=str(payload.get("pipeline", "")),
            pipeline_version=str(payload.get("pipeline_version", "")),
            error=str(error.get("message", "")) if isinstance(error, dict) else "",
            created_at=_parse_dt(payload.get("created_at")),
            started_at=_parse_dt(payload.get("started_at")),
            completed_at=_parse_dt(payload.get("completed_at")),
        )

    @classmethod
    def parse_molecule_result(cls, payload: dict[str, Any]) -> MoleculeResult:
        metrics = payload.get("metrics") or {}
        artifacts = payload.get("artifacts") or {}
        return MoleculeResult(
            result_id=str(payload.get("id", "")),
            smiles=str(payload.get("smiles", "")),
            external_id=str(payload.get("external_id") or ""),
            metrics=MoleculeMetrics(
                binding_confidence=_opt_float(metrics.get("binding_confidence")),
                optimization_score=_opt_float(metrics.get("optimization_score")),
                structure_confidence=_opt_float(metrics.get("structure_confidence")),
                iptm=_opt_float(metrics.get("iptm")),
                ptm=_opt_float(metrics.get("ptm")),
                complex_plddt=_opt_float(metrics.get("complex_plddt")),
                complex_iplddt=_opt_float(metrics.get("complex_iplddt")),
            ),
            adme=cls._parse_adme(payload.get("adme")),
            structure=cls._parse_artifact(artifacts.get("structure")),
            ligand_structure=cls._parse_artifact(artifacts.get("ligand_structure")),
            archive=cls._parse_artifact(artifacts.get("archive")),
            warnings=tuple(
                MoleculeWarning(code=str(w.get("code", "")), message=str(w.get("message", "")))
                for w in payload.get("warnings") or []
                if isinstance(w, dict)
            ),
            created_at=_parse_dt(payload.get("created_at")),
        )

    async def estimate_screen_cost(self, spec: ScreenSpec) -> CostEstimate:
        payload = await self._request(
            "POST", f"{SCREEN_PATH}/estimate-cost", json=self._screen_payload(spec)
        )
        breakdown = payload.get("breakdown") or {}
        return CostEstimate(
            total_usd=_money(payload.get("estimated_cost_usd", "0")),
            unit_usd=_money(breakdown.get("cost_per_unit_usd", "0")),
            units=int(breakdown.get("num_units") or len(spec.molecules)),
            application=str(breakdown.get("application", "small_molecule_library_screen")),
            disclaimer=str(payload.get("disclaimer", "")),
        )

    async def start_screen(self, spec: ScreenSpec, *, idempotency_key: str) -> MoleculeJob:
        if not idempotency_key:
            raise ValueError("idempotency_key is required when starting a screen")
        body = self._screen_payload(spec)
        body["idempotency_key"] = idempotency_key
        payload = await self._request("POST", SCREEN_PATH, json=body, http_timeout=TIMEOUT_SUBMIT)
        return self.parse_molecule_job(payload)

    async def get_screen(self, job_id: str) -> MoleculeJob:
        return self.parse_molecule_job(await self._request("GET", f"{SCREEN_PATH}/{job_id}"))

    async def stop_screen(self, job_id: str) -> MoleculeJob:
        return self.parse_molecule_job(await self._request("POST", f"{SCREEN_PATH}/{job_id}/stop"))

    async def resume_screen(self, job_id: str) -> MoleculeJob:
        return self.parse_molecule_job(
            await self._request("POST", f"{SCREEN_PATH}/{job_id}/resume")
        )

    async def delete_screen_data(self, job_id: str) -> None:
        await self._request("POST", f"{SCREEN_PATH}/{job_id}/delete-data")

    async def iter_screen_results(
        self, job_id: str, *, after_id: str | None = None, page_size: int = 100
    ) -> AsyncIterator[MoleculeResult]:
        """Page through screened molecules as they are scored."""
        async for result in self._iter_molecules(
            SCREEN_PATH, job_id, after_id=after_id, page_size=page_size
        ):
            yield result

    async def iter_design_results(
        self, job_id: str, *, after_id: str | None = None, page_size: int = 100
    ) -> AsyncIterator[MoleculeResult]:
        """Page through invented molecules as they are generated."""
        async for result in self._iter_molecules(
            MOLECULE_DESIGN_PATH, job_id, after_id=after_id, page_size=page_size
        ):
            yield result

    async def _iter_molecules(
        self,
        path: str,
        job_id: str,
        *,
        after_id: str | None = None,
        page_size: int = 100,
    ) -> AsyncIterator[MoleculeResult]:
        """Page a molecule run's results with the API's cursor.

        Results stream in during a run rather than arriving at the end, so this
        is safe to call on a job that is still going — and that is the point,
        since a researcher watching a long run should see hits appear.

        Screening and design return the identical result shape, so they share
        this entirely.
        """
        cursor = after_id
        seen: set[str] = set()
        while True:
            params: dict[str, Any] = {"limit": page_size}
            if cursor:
                params["after_id"] = cursor
            page = await self._request("GET", f"{path}/{job_id}/results", params=params)
            rows = page.get("data") or []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                result = self.parse_molecule_result(row)
                if result.result_id in seen:
                    log.warning(
                        "Boltz returned duplicate molecule result %s for %s; skipping",
                        result.result_id,
                        job_id,
                    )
                    continue
                seen.add(result.result_id)
                yield result
            if not page.get("has_more") or not rows:
                return
            last_id = page.get("last_id")
            if not last_id or last_id == cursor:
                log.warning("Boltz molecule cursor did not advance for %s", job_id)
                return
            cursor = str(last_id)

    # --- de novo molecule design -------------------------------------------

    @classmethod
    def _molecule_design_payload(cls, spec: MoleculeDesignSpec) -> dict[str, Any]:
        """Flat, like the screen — and the target schema is shared with it."""
        return {
            "target": cls._screen_target_payload(spec.target),
            "num_molecules": spec.num_molecules,
        }

    async def estimate_design_cost(self, spec: MoleculeDesignSpec) -> CostEstimate:
        payload = await self._request(
            "POST",
            f"{MOLECULE_DESIGN_PATH}/estimate-cost",
            json=self._molecule_design_payload(spec),
        )
        breakdown = payload.get("breakdown") or {}
        return CostEstimate(
            total_usd=_money(payload.get("estimated_cost_usd", "0")),
            unit_usd=_money(breakdown.get("cost_per_unit_usd", "0")),
            units=int(breakdown.get("num_units") or spec.num_molecules),
            application=str(breakdown.get("application", "small_molecule_design")),
            disclaimer=str(payload.get("disclaimer", "")),
        )

    async def start_molecule_design(
        self, spec: MoleculeDesignSpec, *, idempotency_key: str
    ) -> MoleculeJob:
        if not idempotency_key:
            raise ValueError("idempotency_key is required when starting a molecule design run")
        body = self._molecule_design_payload(spec)
        body["idempotency_key"] = idempotency_key
        payload = await self._request(
            "POST", MOLECULE_DESIGN_PATH, json=body, http_timeout=TIMEOUT_SUBMIT
        )
        return self.parse_molecule_job(payload)

    async def get_molecule_design(self, job_id: str) -> MoleculeJob:
        return self.parse_molecule_job(
            await self._request("GET", f"{MOLECULE_DESIGN_PATH}/{job_id}")
        )

    async def stop_molecule_design(self, job_id: str) -> MoleculeJob:
        return self.parse_molecule_job(
            await self._request("POST", f"{MOLECULE_DESIGN_PATH}/{job_id}/stop")
        )

    async def delete_molecule_design_data(self, job_id: str) -> None:
        await self._request("POST", f"{MOLECULE_DESIGN_PATH}/{job_id}/delete-data")

    # --- ADME --------------------------------------------------------------

    @staticmethod
    def _adme_payload(spec: AdmeSpec) -> dict[str, Any]:
        molecules: list[dict[str, Any]] = []
        for molecule in spec.molecules:
            entry: dict[str, Any] = {"smiles": molecule.smiles}
            if molecule.external_id:
                entry["id"] = molecule.external_id
            molecules.append(entry)
        return {"input": {"molecules": molecules}, "model": ADME_MODEL}

    @classmethod
    def parse_adme_job(cls, payload: dict[str, Any]) -> AdmeJob:
        raw_status = str(payload.get("status", "")).lower()
        try:
            status = JobStatus(raw_status)
        except ValueError:
            log.warning("Boltz returned unknown ADME status %r", raw_status)
            status = JobStatus.running

        error = payload.get("error")
        rows = (payload.get("output") or {}).get("molecules") or []
        results: list[AdmeResult] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_error = row.get("error")
            results.append(
                AdmeResult(
                    molecule_id=str(row.get("id", "")),
                    smiles=str(row.get("smiles", "")),
                    external_id=str(row.get("external_id") or ""),
                    succeeded=str(row.get("status", "")).lower() == "succeeded",
                    error=(
                        str(row_error.get("message", "")) if isinstance(row_error, dict) else ""
                    ),
                    adme=cls._parse_adme(row.get("adme")),
                )
            )

        return AdmeJob(
            job_id=str(payload.get("id", "")),
            status=status,
            livemode=bool(payload.get("livemode", False)),
            model=str(payload.get("model", "")),
            version=str(payload.get("version", "")),
            error=str(error.get("message", "")) if isinstance(error, dict) else "",
            created_at=_parse_dt(payload.get("created_at")),
            completed_at=_parse_dt(payload.get("completed_at")),
            expires_at=_parse_dt(payload.get("expires_at")),
            results=tuple(results),
        )

    async def estimate_adme_cost(self, spec: AdmeSpec) -> CostEstimate:
        payload = await self._request(
            "POST", f"{ADME_PATH}/estimate-cost", json=self._adme_payload(spec)
        )
        breakdown = payload.get("breakdown") or {}
        return CostEstimate(
            total_usd=_money(payload.get("estimated_cost_usd", "0")),
            unit_usd=_money(breakdown.get("cost_per_unit_usd", "0")),
            units=int(breakdown.get("num_units") or len(spec.molecules)),
            application=str(breakdown.get("application", "adme")),
            disclaimer=str(payload.get("disclaimer", "")),
        )

    async def start_adme(self, spec: AdmeSpec, *, idempotency_key: str) -> AdmeJob:
        if not idempotency_key:
            raise ValueError("idempotency_key is required when starting an ADME run")
        body = self._adme_payload(spec)
        body["idempotency_key"] = idempotency_key
        payload = await self._request("POST", ADME_PATH, json=body, http_timeout=TIMEOUT_SUBMIT)
        return self.parse_adme_job(payload)

    async def get_adme(self, job_id: str) -> AdmeJob:
        return self.parse_adme_job(await self._request("GET", f"{ADME_PATH}/{job_id}"))

    # --- misc --------------------------------------------------------------

    async def list_curated_specifications(
        self, kind: Literal["nanobody", "antibody"] = "nanobody"
    ) -> list[dict[str, Any]]:
        """The curated template library.

        Note each entry's ``structure.url`` is presigned and expires in about 30
        minutes, so these must not be cached beyond that or handed to a client
        to fetch later.
        """
        payload = await self._request(
            "GET", "/protein/design/curated-specifications", params={"type": kind}
        )
        return [row for row in payload.get("data") or [] if isinstance(row, dict)]
