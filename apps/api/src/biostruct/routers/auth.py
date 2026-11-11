"""Email + password authentication (ADR-021).

Security properties this module is responsible for, each tested in
``tests/test_auth.py``:

* Wrong password and unknown email are indistinguishable in status, body and
  rough timing.
* The session cookie is httpOnly, so JavaScript — including an injected script —
  cannot read it.
* Logout invalidates the session server-side, so the cookie is useless afterwards.
* A password hash never appears in any response.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

from biostruct import errors, security
from biostruct.deps import CsrfDep, CurrentUser, SettingsDep, UsersDep, client_ip
from biostruct.models.user import LoginRequest, RegisterRequest, UserPublic

router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[CsrfDep])


def _set_session_cookies(
    response: Response, *, token: str, csrf: str, ttl_days: int, secure: bool
) -> None:
    max_age = ttl_days * 24 * 60 * 60
    # httpOnly: the session token must be unreadable from JavaScript.
    response.set_cookie(
        security.SESSION_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    # Deliberately NOT httpOnly: the web app must read this to echo it back in
    # the X-CSRF-Token header. That is the double-submit pattern, and this token
    # is useless on its own.
    response.set_cookie(
        security.CSRF_COOKIE,
        csrf,
        max_age=max_age,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )


def _end_session_cookies(response: Response, *, ttl_days: int, secure: bool) -> None:
    """Drop the session, but hand back a FRESH CSRF token.

    Deleting the CSRF cookie too would leave the user on the sign-in page with
    no token, so their next login attempt would be refused until they reloaded.
    Rotating it instead keeps the form usable immediately, and a new token after
    a privilege change is the safer choice anyway. A CSRF token is not a
    credential: it only has to be unguessable by another origin.
    """
    response.delete_cookie(security.SESSION_COOKIE, path="/")
    response.set_cookie(
        security.CSRF_COOKIE,
        security.new_csrf_token(),
        max_age=ttl_days * 24 * 60 * 60,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )


class CsrfToken(BaseModel):
    csrf_token: str


@router.get("/csrf")
async def issue_csrf(request: Request, response: Response, settings: SettingsDep) -> CsrfToken:
    """Issue a CSRF token, reusing the existing one if the caller already has it.

    The web app needs a token before it can register or sign in, which is the
    one case where no session exists yet to carry one. Reusing an existing
    cookie keeps a second browser tab from invalidating the first one's token.
    """
    token = request.cookies.get(security.CSRF_COOKIE) or security.new_csrf_token()
    response.set_cookie(
        security.CSRF_COOKIE,
        token,
        max_age=settings.session_ttl_days * 24 * 60 * 60,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return CsrfToken(csrf_token=token)


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    request: Request,
    response: Response,
    users: UsersDep,
    settings: SettingsDep,
) -> UserPublic:
    ip = client_ip(request)
    if (retry := security.register_limiter_by_ip.check(f"register:{ip}")) is not None:
        raise errors.rate_limited(retry)

    if (
        reason := security.validate_password_strength(
            body.password, email=body.email, name=body.name
        )
    ) is not None:
        raise errors.weak_password(reason)

    user = await users.create(email=body.email, name=body.name, password=body.password)
    if user is None:
        raise errors.email_taken()

    token = await users.create_session(
        user_id=user.id,
        ttl_days=settings.session_ttl_days,
        user_agent=request.headers.get("user-agent"),
        ip=ip,
    )
    _set_session_cookies(
        response,
        token=token,
        csrf=request.cookies.get(security.CSRF_COOKIE) or security.new_csrf_token(),
        ttl_days=settings.session_ttl_days,
        secure=settings.cookie_secure,
    )
    return user.public()


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    users: UsersDep,
    settings: SettingsDep,
) -> UserPublic:
    ip = client_ip(request)
    # Two buckets: a shared network cannot lock out one researcher, and one
    # attacker cannot spread an attack across many emails from one address.
    if (retry := security.login_limiter_by_ip.check(f"ip:{ip}")) is not None:
        raise errors.rate_limited(retry)
    if (retry := security.login_limiter_by_email.check(f"email:{body.email}")) is not None:
        raise errors.rate_limited(retry)

    user = await users.get_by_email(body.email)
    # verify_password is called even when the user is absent, so the timing of a
    # failed login does not reveal whether the account exists.
    ok = security.verify_password(body.password, user.password_hash if user else None)
    if not ok or user is None:
        raise errors.invalid_credentials()

    rehashed = (
        security.hash_password(body.password) if security.needs_rehash(user.password_hash) else None
    )
    await users.record_login(user.id, new_password_hash=rehashed)

    security.login_limiter_by_email.reset(f"email:{body.email}")
    security.login_limiter_by_ip.reset(f"ip:{ip}")

    token = await users.create_session(
        user_id=user.id,
        ttl_days=settings.session_ttl_days,
        user_agent=request.headers.get("user-agent"),
        ip=ip,
    )
    _set_session_cookies(
        response,
        token=token,
        csrf=request.cookies.get(security.CSRF_COOKIE) or security.new_csrf_token(),
        ttl_days=settings.session_ttl_days,
        secure=settings.cookie_secure,
    )
    return user.public()


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request, response: Response, users: UsersDep, settings: SettingsDep
) -> Response:
    """Invalidate the session server-side, then clear the session cookie.

    Clearing the cookie alone would leave a valid session in the database, so a
    copied cookie would still work after "logging out".
    """
    token = request.cookies.get(security.SESSION_COOKIE)
    if token:
        await users.delete_session(token)
    _end_session_cookies(
        response, ttl_days=settings.session_ttl_days, secure=settings.cookie_secure
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me")
async def me(user: CurrentUser) -> UserPublic:
    return user.public()
