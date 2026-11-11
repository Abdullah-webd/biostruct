"""Authentication behaviour, against a real database.

These assert the security properties promised in ARCHITECTURE.md §11, not just
the happy path. The unhappy paths are the point.
"""

from __future__ import annotations

from biostruct import security

from .conftest import Api

GOOD_PASSWORD = "ribosome velvet cartography"


class TestRegistration:
    async def test_register_returns_user_and_signs_them_in(self, api: Api) -> None:
        r = await api.register()
        assert r.status_code == 201
        body = r.json()
        assert body["email"] == "researcher@example.org"
        assert body["name"] == "Test Researcher"
        assert body["role"] == "researcher"

        # Registration signs you in, so /me works immediately.
        me = await api.me()
        assert me.status_code == 200
        assert me.json()["email"] == "researcher@example.org"

    async def test_password_hash_never_appears_in_any_response(self, api: Api) -> None:
        r = await api.register(password=GOOD_PASSWORD)
        assert "password" not in r.text.lower()
        assert "argon2" not in r.text.lower()
        me = await api.me()
        assert "password" not in me.text.lower()

    async def test_email_is_lowercased_so_case_cannot_duplicate_an_account(self, api: Api) -> None:
        r1 = await api.register(email="Mixed.Case@Example.ORG", password=GOOD_PASSWORD)
        assert r1.status_code == 201
        assert r1.json()["email"] == "mixed.case@example.org"

        r2 = await api.register(email="mixed.case@example.org", password=GOOD_PASSWORD)
        assert r2.status_code == 409
        assert r2.json()["type"].endswith("/email_taken")

    async def test_duplicate_email_rejected(self, api: Api) -> None:
        assert (await api.register()).status_code == 201
        dup = await api.register()
        assert dup.status_code == 409
        assert dup.headers["content-type"].startswith("application/problem+json")

    async def test_weak_password_rejected_with_a_reason(self, api: Api) -> None:
        r = await api.register(password="password123")
        assert r.status_code == 422
        body = r.json()
        assert body["type"].endswith("/weak_password")
        assert body["detail"]  # an actionable reason, not just a refusal

    async def test_short_password_rejected(self, api: Api) -> None:
        r = await api.register(password="short")
        assert r.status_code == 422

    async def test_invalid_email_rejected(self, api: Api) -> None:
        r = await api.register(email="not-an-email", password=GOOD_PASSWORD)
        assert r.status_code == 422


class TestLogin:
    async def test_login_after_register(self, api: Api) -> None:
        await api.register(password=GOOD_PASSWORD)
        await api.logout()
        r = await api.login(password=GOOD_PASSWORD)
        assert r.status_code == 200
        assert (await api.me()).status_code == 200

    async def test_wrong_password_and_unknown_email_are_indistinguishable(self, api: Api) -> None:
        """The core anti-enumeration property: identical status and body.

        If these differed, anyone could discover which researchers have accounts.
        """
        await api.register(password=GOOD_PASSWORD)
        await api.logout()

        wrong_pw = await api.login(password="definitely-not-the-password")
        unknown = await api.login(email="nobody@example.org", password=GOOD_PASSWORD)

        assert wrong_pw.status_code == unknown.status_code == 401
        assert wrong_pw.json() == unknown.json()

    async def test_failed_login_does_not_create_a_session(self, api: Api) -> None:
        await api.register(password=GOOD_PASSWORD)
        await api.logout()
        await api.login(password="wrong-password-entirely")
        assert (await api.me()).status_code == 401


class TestSessionCookie:
    async def test_session_cookie_is_httponly(self, api: Api) -> None:
        """httpOnly means injected JavaScript cannot read the session token."""
        r = await api.register(password=GOOD_PASSWORD)
        set_cookies = [v for k, v in r.headers.multi_items() if k.lower() == "set-cookie"]
        session_header = next(h for h in set_cookies if h.startswith(f"{security.SESSION_COOKIE}="))
        assert "httponly" in session_header.lower()
        assert "samesite=lax" in session_header.lower().replace(" ", "")

    async def test_csrf_cookie_is_readable_by_the_app(self, api: Api) -> None:
        """The CSRF cookie must NOT be httpOnly: the client echoes it in a header."""
        r = await api.register(password=GOOD_PASSWORD)
        set_cookies = [v for k, v in r.headers.multi_items() if k.lower() == "set-cookie"]
        csrf_header = next(h for h in set_cookies if h.startswith(f"{security.CSRF_COOKIE}="))
        assert "httponly" not in csrf_header.lower()

    async def test_cookie_does_not_contain_the_user_id_or_email(self, api: Api) -> None:
        """An opaque token: the cookie should leak nothing about the account."""
        r = await api.register(password=GOOD_PASSWORD)
        user_id = r.json()["id"]
        token = api.c.cookies.get(security.SESSION_COOKIE)
        assert token
        assert user_id not in token
        assert "researcher@example.org" not in token

    async def test_garbage_session_token_is_rejected(self, api: Api) -> None:
        api.c.cookies.set(security.SESSION_COOKIE, "not-a-real-token")
        assert (await api.me()).status_code == 401


class TestLogout:
    async def test_logout_invalidates_the_session_server_side(self, api: Api) -> None:
        """Clearing the cookie is not enough; a copied cookie must stop working."""
        await api.register(password=GOOD_PASSWORD)
        stolen = api.c.cookies.get(security.SESSION_COOKIE)
        assert stolen

        assert (await api.logout()).status_code == 204

        # Replay the "stolen" cookie: the session is gone from the database.
        api.c.cookies.set(security.SESSION_COOKIE, stolen)
        assert (await api.me()).status_code == 401

    async def test_logout_when_not_signed_in_is_harmless(self, api: Api) -> None:
        assert (await api.logout()).status_code == 204


class TestCsrf:
    async def test_state_change_without_csrf_header_is_refused(self, api: Api) -> None:
        await api.register(password=GOOD_PASSWORD)
        # Bypass the helper so no CSRF header is sent.
        r = await api.c.post(
            "/api/v1/projects",
            json={"name": "X", "infection": {"name": "Klebsiella pneumoniae"}},
        )
        assert r.status_code == 403
        assert r.json()["type"].endswith("/csrf_failed")

    async def test_mismatched_csrf_header_is_refused(self, api: Api) -> None:
        await api.register(password=GOOD_PASSWORD)
        r = await api.c.post(
            "/api/v1/projects",
            json={"name": "X", "infection": {"name": "Klebsiella pneumoniae"}},
            headers={security.CSRF_HEADER: security.new_csrf_token()},
        )
        assert r.status_code == 403

    async def test_get_requests_need_no_csrf_token(self, api: Api) -> None:
        await api.register(password=GOOD_PASSWORD)
        assert (await api.c.get("/api/v1/projects")).status_code == 200


class TestRateLimiting:
    async def test_repeated_failures_are_eventually_rate_limited(self, api: Api) -> None:
        await api.register(password=GOOD_PASSWORD)
        await api.logout()
        statuses = [
            (await api.login(password=f"wrong-password-{i}")).status_code for i in range(12)
        ]
        assert 429 in statuses, "brute force should be throttled"
        # The limit must not be so tight that a few typos lock someone out.
        assert statuses[:5] == [401] * 5

    async def test_rate_limit_response_says_when_to_retry(self, api: Api) -> None:
        await api.register(password=GOOD_PASSWORD)
        await api.logout()
        last = None
        for i in range(15):
            last = await api.login(password=f"wrong-{i}")
            if last.status_code == 429:
                break
        assert last is not None and last.status_code == 429
        assert last.json()["retry_after"] > 0


class TestProtectedRoutes:
    async def test_me_requires_a_session(self, api: Api) -> None:
        r = await api.me()
        assert r.status_code == 401
        assert r.json()["type"].endswith("/unauthenticated")

    async def test_projects_require_a_session(self, api: Api) -> None:
        assert (await api.get("/projects")).status_code == 401
