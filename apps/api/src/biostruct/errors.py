"""RFC 9457 problem details, plus the domain error codes from ARCHITECTURE.md §9.

Every error the API returns has a stable machine-readable ``type`` so the web app
can branch on it without string-matching human prose.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# Starlette renamed the 422 constant; use the number so we are not coupled to either name.
HTTP_422 = 422

PROBLEM_BASE = "https://biostruct.app/problems"
CONTENT_TYPE = "application/problem+json"


class ProblemError(Exception):
    """An error that renders itself as an RFC 9457 problem document."""

    def __init__(
        self,
        *,
        code: str,
        title: str,
        status_code: int,
        detail: str | None = None,
        **extra: Any,
    ) -> None:
        self.code = code
        self.title = title
        self.status_code = status_code
        self.detail = detail
        self.extra = extra
        super().__init__(detail or title)

    def to_response(self, instance: str | None = None) -> JSONResponse:
        body: dict[str, Any] = {
            "type": f"{PROBLEM_BASE}/{self.code}",
            "title": self.title,
            "status": self.status_code,
        }
        if self.detail:
            body["detail"] = self.detail
        if instance:
            body["instance"] = instance
        body.update(self.extra)
        return JSONResponse(body, status_code=self.status_code, media_type=CONTENT_TYPE)


# --- the domain errors named in ARCHITECTURE.md §9 -------------------------


def unauthenticated() -> ProblemError:
    return ProblemError(
        code="unauthenticated",
        title="Not signed in",
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Sign in to continue.",
    )


def invalid_credentials() -> ProblemError:
    """Deliberately identical for 'no such user' and 'wrong password'.

    Distinguishing them turns the login endpoint into a way to discover which
    researchers have accounts here.
    """
    return ProblemError(
        code="invalid_credentials",
        title="Incorrect email or password",
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


def email_taken() -> ProblemError:
    return ProblemError(
        code="email_taken",
        title="That email already has an account",
        status_code=status.HTTP_409_CONFLICT,
        detail="Sign in instead, or use a different email.",
    )


def weak_password(reason: str) -> ProblemError:
    return ProblemError(
        code="weak_password",
        title="Password is not strong enough",
        status_code=HTTP_422,
        detail=reason,
    )


def not_found(what: str = "Resource") -> ProblemError:
    return ProblemError(
        code="not_found",
        title=f"{what} not found",
        status_code=status.HTTP_404_NOT_FOUND,
    )


def forbidden() -> ProblemError:
    return ProblemError(
        code="forbidden",
        title="Not allowed",
        status_code=status.HTTP_403_FORBIDDEN,
    )


def budget_exceeded(detail: str, **extra: Any) -> ProblemError:
    return ProblemError(
        code="budget_exceeded",
        title="Project budget exceeded",
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        detail=detail,
        **extra,
    )


def cost_estimate_stale(detail: str) -> ProblemError:
    return ProblemError(
        code="cost_estimate_stale",
        title="Cost estimate no longer valid",
        status_code=status.HTTP_409_CONFLICT,
        detail=detail,
    )


def engine_unavailable(detail: str) -> ProblemError:
    return ProblemError(
        code="engine_unavailable",
        title="Design engine unavailable",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=detail,
    )


def rate_limited(retry_after_seconds: int) -> ProblemError:
    return ProblemError(
        code="rate_limited",
        title="Too many requests",
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Try again in {retry_after_seconds} seconds.",
        retry_after=retry_after_seconds,
    )


def csrf_failed() -> ProblemError:
    return ProblemError(
        code="csrf_failed",
        title="Request could not be verified",
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Reload the page and try again.",
    )


# --- handlers --------------------------------------------------------------


async def problem_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ProblemError)
    return exc.to_response(instance=str(request.url.path))


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    return ProblemError(
        code="http_error",
        title=str(exc.detail),
        status_code=exc.status_code,
    ).to_response(instance=str(request.url.path))


async def validation_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    return ProblemError(
        code="validation_failed",
        title="Request body is invalid",
        status_code=HTTP_422,
        errors=[
            {"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]}
            for e in exc.errors()
        ],
    ).to_response(instance=str(request.url.path))
