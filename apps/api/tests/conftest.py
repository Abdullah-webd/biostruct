from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

from biostruct import security
from biostruct.config import Env, Settings
from biostruct.main import API_PREFIX, create_app
from tests.stubs import StubSources

MONGO_URI = os.environ.get("TEST_MONGODB_URI", "mongodb://localhost:27017/?directConnection=true")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Mark everything that uses the ``client`` fixture as an integration test."""
    for item in items:
        if "client" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.integration)


@pytest.fixture(autouse=True)
def _reset_rate_limiters() -> None:
    """Rate limiters are process-global, so tests would otherwise leak into each other."""
    security.login_limiter_by_email.clear()
    security.login_limiter_by_ip.clear()
    security.register_limiter_by_ip.clear()


@pytest_asyncio.fixture
async def mongo_available() -> bool:
    try:
        probe: AsyncIOMotorClient[dict] = AsyncIOMotorClient(
            MONGO_URI, serverSelectionTimeoutMS=1500
        )
        await probe.admin.command("ping")
        probe.close()
        return True
    except Exception:
        return False


# One shared test database, wiped between tests.
#
# This used to create a fresh database per test. That looked tidy and was
# actually a bug: 80 databases x 4 collections of WiredTiger files exhausted the
# container's file descriptors mid-run, and MongoDB died with
# "Too many open files" and a WT_PANIC. Wiping documents is both isolated and
# cheap, and it keeps the suite fast.
TEST_DB_NAME = os.environ.get("TEST_MONGODB_DB", "biostruct_test")

# Every collection the app writes to. Anything added later must be listed here,
# or state will leak between tests.
_COLLECTIONS = ("users", "sessions", "projects", "source_cache")


@pytest_asyncio.fixture
async def settings(mongo_available: bool) -> Settings:
    if not mongo_available:
        pytest.skip("needs MongoDB: docker compose up -d mongo")
    return Settings(
        env=Env.test,
        mongodb_uri=MONGO_URI,
        mongodb_db=TEST_DB_NAME,
        session_secret="test-secret",
        app_base_url="http://testserver",
        boltz_api_key="",  # no key in unit tests; the mode check is a no-op
        default_project_budget_usd="25",
    )


@pytest_asyncio.fixture
async def clean_db(settings: Settings) -> AsyncIterator[None]:
    """Empty every collection before the test runs.

    Before rather than after, so a crashed test still leaves the next one with a
    clean slate.
    """
    client: AsyncIOMotorClient[dict] = AsyncIOMotorClient(settings.mongodb_uri)
    db = client[settings.mongodb_db]
    for name in _COLLECTIONS:
        await db[name].delete_many({})
    try:
        yield
    finally:
        client.close()


@pytest_asyncio.fixture
async def client(settings: Settings, clean_db: None) -> AsyncIterator[AsyncClient]:
    """An HTTP client against the real app, on a freshly emptied database."""
    app = create_app(settings)
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver", follow_redirects=True
        ) as ac:
            yield ac


class Api:
    """Small helper so tests read like intent, not like cookie plumbing."""

    def __init__(self, client: AsyncClient) -> None:
        self.c = client

    async def _headers(self) -> dict[str, str]:
        """Read the CSRF token from the cookie on EVERY request.

        This is what a browser client does, and it is the only correct
        approach: the server rotates the token on logout, so a cached value
        goes stale. Caching it here previously hid exactly that bug.
        """
        token = self.c.cookies.get(security.CSRF_COOKIE)
        if not token:
            r = await self.c.get(f"{API_PREFIX}/auth/csrf")
            token = r.json()["csrf_token"]
        return {security.CSRF_HEADER: token}

    async def register(
        self,
        email: str = "researcher@example.org",
        name: str = "Test Researcher",
        password: str = "correct-horse-battery",
    ):
        return await self.c.post(
            f"{API_PREFIX}/auth/register",
            json={"email": email, "name": name, "password": password},
            headers=await self._headers(),
        )

    async def login(
        self,
        email: str = "researcher@example.org",
        password: str = "correct-horse-battery",
    ):
        return await self.c.post(
            f"{API_PREFIX}/auth/login",
            json={"email": email, "password": password},
            headers=await self._headers(),
        )

    async def logout(self):
        return await self.c.post(f"{API_PREFIX}/auth/logout", headers=await self._headers())

    async def me(self):
        return await self.c.get(f"{API_PREFIX}/auth/me")

    async def post(self, path: str, json: object | None = None):
        return await self.c.post(f"{API_PREFIX}{path}", json=json, headers=await self._headers())

    async def patch(self, path: str, json: object | None = None):
        return await self.c.patch(f"{API_PREFIX}{path}", json=json, headers=await self._headers())

    async def put(self, path: str, json: object | None = None):
        return await self.c.put(f"{API_PREFIX}{path}", json=json, headers=await self._headers())

    async def get(self, path: str):
        return await self.c.get(f"{API_PREFIX}{path}")

    async def delete(self, path: str):
        return await self.c.delete(f"{API_PREFIX}{path}", headers=await self._headers())


@pytest.fixture
def api(client: AsyncClient) -> Api:
    return Api(client)


@pytest_asyncio.fixture
async def stub_sources(client: AsyncClient) -> AsyncIterator[StubSources]:
    """Replace the app's ScienceSources with a controllable stub.

    ``get_sources`` reads ``app.state.sources`` per request, so swapping it after
    startup is enough, and it keeps endpoint tests off the network.
    """
    app = client._transport.app  # type: ignore[attr-defined]
    real = app.state.sources
    stub = StubSources()
    app.state.sources = stub
    try:
        yield stub
    finally:
        app.state.sources = real
