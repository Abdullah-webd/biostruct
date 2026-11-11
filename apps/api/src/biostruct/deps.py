"""FastAPI dependencies: database handles, repositories, and the current user."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Request
from motor.motor_asyncio import AsyncIOMotorDatabase

from biostruct import errors, security
from biostruct.config import Settings, get_settings
from biostruct.models.user import User
from biostruct.repositories import ProjectRepository, UserRepository


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


UsersDep = Annotated[UserRepository, Depends(get_users)]
ProjectsDep = Annotated[ProjectRepository, Depends(get_projects)]


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
