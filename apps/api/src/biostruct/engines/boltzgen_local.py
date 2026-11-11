"""Reserved seat for a self-hosted BoltzGen engine (ADR-002).

Not implemented, and deliberately present anyway. Some African research
institutions cannot send unpublished target data to a US API, and keeping this
seam occupied means that constraint stays visible in the codebase instead of
being rediscovered as a rewrite.

What it would need (docs/research.md §1): an A100-class GPU, ~6 GB of Boltz
weights plus ~33 GB of ESMFold2 weights, a CUDA 13 driver, and minutes per
design. That is an infrastructure project, not a feature, which is why the
hosted engine ships first.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from biostruct.engines.base import (
    CostEstimate,
    DesignResult,
    DesignSpec,
    EngineIdentity,
    EngineJob,
)

_MESSAGE = (
    "The self-hosted BoltzGen engine is not implemented. Use the hosted Boltz "
    "engine, or see ARCHITECTURE.md ADR-002."
)


class BoltzGenLocalEngine:
    name = "boltzgen-local"

    async def estimate_cost(self, spec: DesignSpec) -> CostEstimate:
        raise NotImplementedError(_MESSAGE)

    async def start_design(self, spec: DesignSpec, *, idempotency_key: str) -> EngineJob:
        raise NotImplementedError(_MESSAGE)

    async def get_job(self, job_id: str) -> EngineJob:
        raise NotImplementedError(_MESSAGE)

    def iter_results(
        self, job_id: str, *, after_id: str | None = None, page_size: int = 100
    ) -> AsyncIterator[DesignResult]:
        raise NotImplementedError(_MESSAGE)

    async def stop_design(self, job_id: str) -> EngineJob:
        raise NotImplementedError(_MESSAGE)

    async def resume_design(self, job_id: str) -> EngineJob:
        raise NotImplementedError(_MESSAGE)

    async def delete_data(self, job_id: str) -> None:
        raise NotImplementedError(_MESSAGE)

    async def whoami(self) -> EngineIdentity:
        raise NotImplementedError(_MESSAGE)
