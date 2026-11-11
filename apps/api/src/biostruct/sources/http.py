"""Shared HTTP plumbing for the scientific data sources.

Three jobs, all of which the sources would otherwise each get slightly wrong:

* **Caching** in a MongoDB TTL collection, which replaced Redis (ADR-003).
  UniProt, RCSB and PubMed answers change on the order of weeks, so caching is
  both polite and a large latency win.
* **Rate limiting**, because PubMed allows only 3 requests/second without a key
  and will start refusing if we exceed it.
* **Degradation that is visible.** Any source can be down. A partial evidence
  panel presented as complete is the most dangerous failure this layer can have,
  so a failed source is recorded and surfaced, never silently dropped.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from motor.motor_asyncio import AsyncIOMotorDatabase

log = logging.getLogger(__name__)

CACHE_COLLECTION = "source_cache"
DEFAULT_TTL = timedelta(hours=24)

# NCBI asks that automated clients identify themselves. A generic client name is
# used rather than anything identifying the user (see the privacy rules).
USER_AGENT = "biostruct/0.1 (therapeutic protein design platform)"


class SourceUnavailable(RuntimeError):
    """A source could not be reached or returned an error we cannot use.

    Callers degrade rather than fail: they record the source as unavailable and
    return what they do have.
    """

    def __init__(self, source: str, detail: str) -> None:
        self.source = source
        self.detail = detail
        super().__init__(f"{source}: {detail}")


class TokenBucket:
    """A simple async rate limiter.

    Deliberately in-process, and deliberately conservative because of that: with
    several API instances the effective rate multiplies, so the per-instance
    limit is set below the published one.
    """

    def __init__(self, rate_per_second: float, *, burst: int = 1) -> None:
        self.rate = rate_per_second
        self.burst = max(1, burst)
        self._tokens = float(self.burst)
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(
                    float(self.burst), self._tokens + (now - self._updated) * self.rate
                )
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                await asyncio.sleep((1.0 - self._tokens) / self.rate)


@dataclass
class Degradation:
    """Which sources failed during one logical operation, and why."""

    failures: dict[str, str] = field(default_factory=dict)

    def record(self, source: str, detail: str) -> None:
        self.failures[source] = detail

    @property
    def sources(self) -> list[str]:
        return sorted(self.failures)

    @property
    def any(self) -> bool:
        return bool(self.failures)


class CachedHTTPClient:
    """JSON over HTTP with a TTL cache, a rate limit and bounded retries.

    Retries only on 429, 5xx and transport errors. A 4xx is a bug in our request
    and retrying it just wastes the source's capacity.
    """

    def __init__(
        self,
        *,
        source: str,
        db: AsyncIOMotorDatabase[dict[str, Any]] | None = None,
        client: httpx.AsyncClient | None = None,
        rate_per_second: float = 3.0,
        burst: int = 1,
        ttl: timedelta = DEFAULT_TTL,
        max_attempts: int = 3,
        timeout: float = 10.0,
    ) -> None:
        self.source = source
        self._db = db
        self._client = client
        self._owns_client = client is None
        self._bucket = TokenBucket(rate_per_second, burst=burst)
        self._ttl = ttl
        self._max_attempts = max_attempts
        self._timeout = timeout

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"accept": "application/json", "user-agent": USER_AGENT},
                timeout=self._timeout,
                follow_redirects=True,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def _cache_key(source: str, method: str, url: str, body: Any) -> str:
        payload = json.dumps(body, sort_keys=True, default=str) if body is not None else ""
        digest = hashlib.sha256(f"{method} {url} {payload}".encode()).hexdigest()
        return f"{source}:{digest}"

    async def _cache_get(self, key: str) -> Any | None:
        if self._db is None:
            return None
        try:
            doc = await self._db[CACHE_COLLECTION].find_one({"_id": key})
        except Exception as exc:  # a cache miss must never fail a request
            log.warning("source cache read failed: %s", exc)
            return None
        if not doc:
            return None
        expires_at = doc.get("expires_at")
        if isinstance(expires_at, datetime):
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            # The TTL index reclaims lazily, so check the expiry here too.
            if expires_at <= datetime.now(UTC):
                return None
        return doc.get("payload")

    async def _cache_put(self, key: str, payload: Any) -> None:
        if self._db is None:
            return
        now = datetime.now(UTC)
        try:
            await self._db[CACHE_COLLECTION].update_one(
                {"_id": key},
                {
                    "$set": {
                        "source": self.source,
                        "payload": payload,
                        "fetched_at": now,
                        "expires_at": now + self._ttl,
                    }
                },
                upsert=True,
            )
        except Exception as exc:  # caching is an optimisation, never a hard dependency
            log.warning("source cache write failed: %s", exc)

    async def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        use_cache: bool = True,
    ) -> Any:
        full = str(httpx.URL(url, params=params or {}))
        return await self._fetch("GET", full, None, use_cache=use_cache)

    async def post_json(self, url: str, body: dict[str, Any], *, use_cache: bool = True) -> Any:
        return await self._fetch("POST", url, body, use_cache=use_cache)

    async def _fetch(
        self, method: str, url: str, body: dict[str, Any] | None, *, use_cache: bool
    ) -> Any:
        key = self._cache_key(self.source, method, url, body)
        if use_cache:
            cached = await self._cache_get(key)
            if cached is not None:
                return cached

        client = await self._http()
        last_detail = "unknown error"
        for attempt in range(1, self._max_attempts + 1):
            await self._bucket.acquire()
            try:
                response = await client.request(method, url, json=body)
            except httpx.HTTPError as exc:
                last_detail = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code == 404:
                    # Not an outage: the thing genuinely is not there.
                    raise SourceUnavailable(self.source, "not found (404)")
                if response.status_code < 400:
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        raise SourceUnavailable(
                            self.source, f"response was not JSON: {exc}"
                        ) from exc
                    if use_cache:
                        await self._cache_put(key, payload)
                    return payload
                if response.status_code == 429 or response.status_code >= 500:
                    last_detail = f"HTTP {response.status_code}"
                else:
                    # Our request is wrong; retrying cannot help.
                    raise SourceUnavailable(
                        self.source, f"HTTP {response.status_code}: {response.text[:200]}"
                    )

            if attempt < self._max_attempts:
                # Jittered backoff so parallel callers do not retry in lockstep.
                delay = (2 ** (attempt - 1)) * 0.5
                await asyncio.sleep(delay + random.uniform(0, delay / 2))  # noqa: S311

        raise SourceUnavailable(self.source, f"{last_detail} after {self._max_attempts} attempts")
