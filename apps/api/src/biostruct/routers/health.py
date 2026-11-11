"""Liveness and readiness.

Separate endpoints on purpose: Railway should restart a container that is not
alive, but must not restart one that is merely waiting on MongoDB.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter

from biostruct import __version__
from biostruct.db import database
from biostruct.deps import DbDep, SettingsDep

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process is up. Must not touch the database."""
    return {"status": "ok", "version": __version__}


@router.get("/readyz")
async def readyz(db: DbDep, settings: SettingsDep) -> dict[str, Any]:
    """Readiness: we can actually serve requests.

    Reports the Boltz key mode so a misconfigured deployment is visible from
    outside, without ever exposing the key itself.
    """
    db_ok = True
    try:
        await db.command("ping")
    except Exception:
        db_ok = False

    status_value: Literal["ready", "degraded"] = "ready" if db_ok else "degraded"
    return {
        "status": status_value,
        "version": __version__,
        "env": settings.env.value,
        "mongodb": "ok" if db_ok else "unreachable",
        "change_streams": await database.supports_change_streams(),
        "boltz_key_mode": settings.boltz_key_mode(),
        "storage_backend": settings.storage_backend,
    }
