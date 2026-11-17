"""FastAPI dependencies: database handles, repositories, and the current user."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Request
from motor.motor_asyncio import AsyncIOMotorDatabase

from biostruct import errors, security
from biostruct.agent.orchestrator import Orchestrator
from biostruct.config import Settings, get_settings
from biostruct.engines.base import DesignEngine
from biostruct.models.user import User
from biostruct.repositories import (
    ProjectRepository,
    RunRepository,
    TargetRepository,
    UserRepository,
)
from biostruct.repositories.conversations import ConversationRepository
from biostruct.sources import ScienceSources
from biostruct.sources.chembl import ChemblClient
from biostruct.sources.europepmc import EuropePMCClient


def get_db(request: Request) -> AsyncIOMotorDatabase[dict[str, Any]]:
    db = getattr(request.app.state, "db", None)
    if db is None:  # pragma: no cover - only if startup was skipped
        raise RuntimeError("database not initialised")
    return db  # type: ignore[no-any-return]


DbDep = Annotated[AsyncIOMotorDatabase[dict[str, Any]], Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_users(db: DbDep) -> UserRepository:
    return UserRepository(db)


def get_projects(db: DbDep) -> ProjectRepository:
    return ProjectRepository(db)


def get_targets(db: DbDep) -> TargetRepository:
    return TargetRepository(db)


def get_sources(request: Request) -> ScienceSources:
    """One ScienceSources per process, created at startup.

    Shared on purpose: it owns the HTTP connection pools and the rate-limit
    token buckets, and a per-request instance would reset the buckets and let us
    sail past NCBI's 3 requests/second.
    """
    sources = getattr(request.app.state, "sources", None)
    if sources is None:  # pragma: no cover - only if startup was skipped
        raise RuntimeError("science sources not initialised")
    return sources  # type: ignore[no-any-return]


def get_runs(db: DbDep) -> RunRepository:
    return RunRepository(db)


def get_conversations(db: DbDep, request: Request) -> ConversationRepository:
    """The conversation store, with storage for oversized tool results.

    A screen result can exceed Mongo's document limit, so the repository needs
    the object store to spill into (ADR-20).
    """
    return ConversationRepository(db, storage=getattr(request.app.state, "storage", None))


def get_chembl(request: Request) -> ChemblClient:
    """One per process: it owns the HTTP pool and the rate-limit bucket."""
    client = getattr(request.app.state, "chembl", None)
    if client is None:  # pragma: no cover - only if startup was skipped
        raise RuntimeError("chembl client not initialised")
    return client  # type: ignore[no-any-return]


def get_europepmc(request: Request) -> EuropePMCClient:
    client = getattr(request.app.state, "europepmc", None)
    if client is None:  # pragma: no cover - only if startup was skipped
        raise RuntimeError("europepmc client not initialised")
    return client  # type: ignore[no-any-return]


def get_orchestrator(db: DbDep, request: Request) -> Orchestrator:
    """A fresh orchestrator per request, over process-wide clients.

    The orchestrator itself is stateless; the LLM client and the sources are
    shared because they own connection pools.
    """
    llm = getattr(request.app.state, "llm", None)
    registry = getattr(request.app.state, "tool_registry", None)
    if llm is None or registry is None:  # pragma: no cover
        raise RuntimeError("agent not initialised")
    return Orchestrator(
        llm=llm,
        registry=registry,
        conversations=get_conversations(db, request),
        europepmc=getattr(request.app.state, "europepmc", None),
    )


def get_engine(request: Request) -> DesignEngine:
    """The configured design engine, created once at startup.

    Shared because it owns an HTTP connection pool.
    """
    engine = getattr(request.app.state, "engine", None)
    if engine is None:  # pragma: no cover - only if startup was skipped
        raise RuntimeError("design engine not initialised")
    return engine  # type: ignore[no-any-return]


UsersDep = Annotated[UserRepository, Depends(get_users)]
ProjectsDep = Annotated[ProjectRepository, Depends(get_projects)]
TargetsDep = Annotated[TargetRepository, Depends(get_targets)]
SourcesDep = Annotated[ScienceSources, Depends(get_sources)]
RunsDep = Annotated[RunRepository, Depends(get_runs)]
EngineDep = Annotated[DesignEngine, Depends(get_engine)]
ConversationsDep = Annotated[ConversationRepository, Depends(get_conversations)]
ChemblDep = Annotated[ChemblClient, Depends(get_chembl)]
EuropePMCDep = Annotated[EuropePMCClient, Depends(get_europepmc)]
OrchestratorDep = Annotated[Orchestrator, Depends(get_orchestrator)]


async def current_user(request: Request, users: UsersDep) -> User:
    """Resolve the session cookie to a user, or raise 401."""
    token = request.cookies.get(security.SESSION_COOKIE)
    if not token:
        raise errors.unauthenticated()
    user = await users.get_user_for_session(token)
    if user is None:
        raise errors.unauthenticated()
    return user


CurrentUser = Annotated[User, Depends(current_user)]


async def verify_csrf(request: Request) -> None:
    """Double-submit CSRF check on every state-changing request.

    Applied as a router dependency rather than middleware so that it is visible
    in the route definition and cannot be silently bypassed by a new router.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    cookie = request.cookies.get(security.CSRF_COOKIE)
    header = request.headers.get(security.CSRF_HEADER)
    if not security.csrf_tokens_match(cookie, header):
        raise errors.csrf_failed()


CsrfDep = Depends(verify_csrf)


def client_ip(request: Request) -> str:
    """Best-effort client IP for rate limiting.

    Behind Railway's proxy the real address is the first entry of
    X-Forwarded-For. This is only used for rate-limit bucketing, never for
    authorisation, so a spoofed value cannot grant access - at worst it lets an
    attacker avoid their own rate-limit bucket, which the per-email limiter
    still covers.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
