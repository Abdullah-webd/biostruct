"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from biostruct import __version__, errors
from biostruct.config import Env, Settings, get_settings
from biostruct.db import database, ensure_indexes, startup_checks
from biostruct.engines import BoltzHostedEngine
from biostruct.jobs import Reconciler
from biostruct.routers import auth, health, projects, runs, targets
from biostruct.sources import ScienceSources
from biostruct.storage import build_storage

logging.basicConfig(
    level=logging.INFO,
    format='{"level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
)
log = logging.getLogger("biostruct")

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Refuse to start on a key/environment mismatch, before anything else.
        cfg.assert_key_mode_matches_env()

        db = await database.connect(cfg.mongodb_uri, cfg.mongodb_db)
        app.state.db = db
        await ensure_indexes(db)
        await startup_checks(db, strict=cfg.is_production)
        # Shared across requests so the rate-limit buckets and connection pools
        # are process-wide, not per-request.
        app.state.sources = ScienceSources(
            db=db,
            ncbi_email=cfg.ncbi_email,
            ncbi_api_key=cfg.ncbi_api_key.get_secret_value(),
        )
        app.state.storage = build_storage(
            backend=cfg.storage_backend,
            local_path=cfg.storage_local_path,
            endpoint_url=cfg.s3_endpoint_url,
            access_key_id=cfg.s3_access_key_id.get_secret_value(),
            secret_access_key=cfg.s3_secret_access_key.get_secret_value(),
            bucket=cfg.s3_bucket,
        )
        app.state.engine = BoltzHostedEngine(
            cfg.boltz_api_key.get_secret_value() or "sk_bc_ws_test_unset",
            base_url=cfg.boltz_base_url,
        )
        # The reconciler runs in-process here for convenience. In production it
        # is a separate Railway service, so that a web deploy cannot interrupt a
        # harvest mid-flight (ARCHITECTURE.md §14).
        app.state.reconciler = Reconciler(db=db, engine=app.state.engine, storage=app.state.storage)
        if cfg.run_reconciler:
            app.state.reconciler.start()
        log.info(
            "started env=%s boltz_key_mode=%s storage=%s",
            cfg.env.value,
            cfg.boltz_key_mode(),
            cfg.storage_backend,
        )
        try:
            yield
        finally:
            await app.state.reconciler.stop()
            await app.state.engine.aclose()
            await app.state.sources.aclose()
            await database.close()

    app = FastAPI(
        title="Biostruct API",
        version=__version__,
        description=(
            "Scientific backend for therapeutic protein design against drug-resistant "
            "infections. Outputs are computational prioritisation, not evidence of "
            "therapeutic effect."
        ),
        lifespan=lifespan,
        # Interactive docs are useful in development and an unnecessary surface in production.
        docs_url=None if cfg.env is Env.prod else "/docs",
        redoc_url=None,
        openapi_url=None if cfg.env is Env.prod else "/openapi.json",
    )

    # Credentialed CORS requires an explicit origin; "*" is not permitted with
    # cookies, and we would not want it anyway.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[cfg.app_base_url],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["content-type", "x-csrf-token"],
    )

    app.add_exception_handler(errors.ProblemError, errors.problem_handler)
    app.add_exception_handler(StarletteHTTPException, errors.http_exception_handler)
    app.add_exception_handler(RequestValidationError, errors.validation_handler)

    app.include_router(health.router)
    app.include_router(auth.router, prefix=API_PREFIX)
    app.include_router(projects.router, prefix=API_PREFIX)
    app.include_router(targets.router, prefix=API_PREFIX)
    app.include_router(runs.router, prefix=API_PREFIX)

    return app


app = create_app()
