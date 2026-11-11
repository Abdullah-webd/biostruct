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
from biostruct.routers import auth, health, projects, targets
from biostruct.sources import ScienceSources

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
        log.info(
            "started env=%s boltz_key_mode=%s storage=%s",
            cfg.env.value,
            cfg.boltz_key_mode(),
            cfg.storage_backend,
        )
        try:
            yield
        finally:
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

    return app


app = create_app()
