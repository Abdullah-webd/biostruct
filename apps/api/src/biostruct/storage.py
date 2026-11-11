"""Object storage behind one interface (ADR-015, ADR-019).

Local disk in development, a Railway Bucket (S3-compatible) in production. The
interface exists for two reasons: Railway's bucket creation sits behind a
subscription checkout, so development must not depend on it, and storage is the
kind of thing that gets swapped.

Why this module matters more than it looks: Boltz artifact URLs are presigned
and expire in **about 30 minutes** (docs/boltz-api-observed.md). Mirroring them
here is the only thing standing between a completed, paid-for design run and
losing its structures.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

import httpx

log = logging.getLogger(__name__)

# Generous: a structure file is small, but a run archive is not.
DOWNLOAD_TIMEOUT = httpx.Timeout(120.0, connect=15.0)
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024


class StorageError(RuntimeError):
    """A mirror failed. Retryable; the caller must not mark the run complete."""


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    size_bytes: int
    content_type: str

    @property
    def is_empty(self) -> bool:
        return self.size_bytes == 0


@runtime_checkable
class StorageBackend(Protocol):
    name: str

    async def put_bytes(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> StoredObject: ...

    async def get_bytes(self, key: str) -> bytes: ...

    async def exists(self, key: str) -> bool: ...

    async def delete(self, key: str) -> None: ...

    async def delete_prefix(self, prefix: str) -> int: ...


class LocalDiskStorage:
    """Development backend. Keeps a contributor free of any cloud account."""

    name = "local"

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Refuse traversal: a key is composed from ids, but it is still a string
        # that ends up as a filesystem path.
        candidate = (self._root / key).resolve()
        if not candidate.is_relative_to(self._root):
            raise StorageError(f"refusing to write outside the storage root: {key!r}")
        return candidate

    async def put_bytes(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> StoredObject:
        path = self._path(key)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Write then rename, so a crash mid-write cannot leave a truncated
            # file that later looks like a successful mirror.
            temp = path.with_suffix(path.suffix + ".partial")
            temp.write_bytes(data)
            temp.replace(path)

        await asyncio.to_thread(write)
        return StoredObject(key=key, size_bytes=len(data), content_type=content_type)

    async def get_bytes(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise StorageError(f"not stored: {key}")
        return await asyncio.to_thread(path.read_bytes)

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._path(key).exists)

    async def delete(self, key: str) -> None:
        path = self._path(key)
        await asyncio.to_thread(path.unlink, True)

    async def delete_prefix(self, prefix: str) -> int:
        base = self._path(prefix)

        def remove() -> int:
            if base.is_dir():
                count = sum(1 for _ in base.rglob("*") if _.is_file())
                shutil.rmtree(base, ignore_errors=True)
                return count
            if base.exists():
                base.unlink()
                return 1
            return 0

        return await asyncio.to_thread(remove)


class S3Storage:
    """Production backend, S3-compatible (Railway Bucket, R2, S3).

    Uses plain signed requests over httpx rather than adding boto3: we need
    exactly put, get, head and delete, and boto3 is synchronous.
    """

    name = "s3"

    def __init__(
        self,
        *,
        endpoint_url: str,
        access_key_id: str,
        secret_access_key: str,
        bucket: str,
        region: str = "auto",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not all([endpoint_url, access_key_id, secret_access_key, bucket]):
            raise ValueError("S3 storage needs endpoint, credentials and a bucket")
        self._endpoint = endpoint_url.rstrip("/")
        self._access_key = access_key_id
        self._secret_key = secret_access_key
        self._bucket = bucket
        self._region = region
        self._client = client
        self._owns_client = client is None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    def _url(self, key: str) -> str:
        return f"{self._endpoint}/{self._bucket}/{key.lstrip('/')}"

    def _sign(self, method: str, key: str, payload: bytes) -> dict[str, str]:
        """AWS SigV4, the subset we need.

        Written out rather than imported because the alternative is a large
        synchronous dependency for four verbs.
        """
        import datetime
        import hmac
        from urllib.parse import quote

        now = datetime.datetime.now(datetime.UTC)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")
        payload_hash = hashlib.sha256(payload).hexdigest()

        host = self._endpoint.split("://", 1)[1]
        canonical_uri = "/" + quote(f"{self._bucket}/{key.lstrip('/')}", safe="/~")
        canonical_headers = (
            f"host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n"
        )
        signed_headers = "host;x-amz-content-sha256;x-amz-date"
        canonical_request = (
            f"{method}\n{canonical_uri}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
        )
        scope = f"{datestamp}/{self._region}/s3/aws4_request"
        to_sign = (
            "AWS4-HMAC-SHA256\n"
            f"{amz_date}\n{scope}\n"
            f"{hashlib.sha256(canonical_request.encode()).hexdigest()}"
        )

        def hmac_sha256(key_bytes: bytes, message: str) -> bytes:
            return hmac.new(key_bytes, message.encode(), hashlib.sha256).digest()

        signing_key = hmac_sha256(
            hmac_sha256(
                hmac_sha256(
                    hmac_sha256(f"AWS4{self._secret_key}".encode(), datestamp), self._region
                ),
                "s3",
            ),
            "aws4_request",
        )
        signature = hmac.new(signing_key, to_sign.encode(), hashlib.sha256).hexdigest()
        return {
            "x-amz-date": amz_date,
            "x-amz-content-sha256": payload_hash,
            "Authorization": (
                f"AWS4-HMAC-SHA256 Credential={self._access_key}/{scope}, "
                f"SignedHeaders={signed_headers}, Signature={signature}"
            ),
        }

    async def put_bytes(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> StoredObject:
        client = await self._http()
        headers = {**self._sign("PUT", key, data), "content-type": content_type}
        try:
            response = await client.put(self._url(key), content=data, headers=headers)
        except httpx.HTTPError as exc:
            raise StorageError(f"could not store {key}: {exc}") from exc
        if response.status_code >= 300:
            raise StorageError(
                f"could not store {key}: HTTP {response.status_code} {response.text[:200]}"
            )
        return StoredObject(key=key, size_bytes=len(data), content_type=content_type)

    async def get_bytes(self, key: str) -> bytes:
        client = await self._http()
        response = await client.get(self._url(key), headers=self._sign("GET", key, b""))
        if response.status_code >= 300:
            raise StorageError(f"could not read {key}: HTTP {response.status_code}")
        return response.content

    async def exists(self, key: str) -> bool:
        client = await self._http()
        response = await client.head(self._url(key), headers=self._sign("HEAD", key, b""))
        return response.status_code < 300

    async def delete(self, key: str) -> None:
        client = await self._http()
        response = await client.delete(self._url(key), headers=self._sign("DELETE", key, b""))
        if response.status_code >= 300 and response.status_code != 404:
            raise StorageError(f"could not delete {key}: HTTP {response.status_code}")

    async def delete_prefix(self, prefix: str) -> int:
        # Listing and bulk delete are not needed yet: project deletion walks the
        # candidates it knows about, which is exact rather than prefix-based.
        raise NotImplementedError(
            "delete_prefix is not implemented for S3; delete known keys individually"
        )


# --- mirroring -------------------------------------------------------------


def artifact_key(run_id: str, result_id: str, kind: str, extension: str) -> str:
    """A content-addressable-ish key.

    Deterministic, so re-mirroring the same artifact overwrites identically and
    a retried harvest cannot create duplicates.
    """
    safe_result = result_id.replace("/", "_")
    return f"runs/{run_id}/{kind}/{safe_result}.{extension.lstrip('.')}"


async def mirror_url(
    *,
    storage: StorageBackend,
    url: str,
    key: str,
    content_type: str = "chemical/x-cif",
    client: httpx.AsyncClient | None = None,
) -> StoredObject:
    """Download a presigned artifact and store it under our own key.

    The whole reason the harvester exists. Boltz URLs expire in about 30
    minutes, so a failure here is not a cosmetic problem: the structure is gone
    and the run would have to be paid for again.
    """
    own_client = client is None
    http = client or httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True)
    try:
        response = await http.get(url)
        if response.status_code >= 300:
            raise StorageError(
                f"artifact download failed with HTTP {response.status_code}. "
                "If the presigned URL has expired the result must be re-fetched "
                "from the engine."
            )
        data = response.content
    except httpx.HTTPError as exc:
        raise StorageError(f"artifact download failed: {exc}") from exc
    finally:
        if own_client:
            await http.aclose()

    if not data:
        raise StorageError("artifact download returned no data")
    if len(data) > MAX_ARTIFACT_BYTES:
        raise StorageError(f"artifact is {len(data)} bytes, over the {MAX_ARTIFACT_BYTES} limit")
    return await storage.put_bytes(key, data, content_type=content_type)


def build_storage(
    *,
    backend: str,
    local_path: str,
    endpoint_url: str = "",
    access_key_id: str = "",
    secret_access_key: str = "",
    bucket: str = "",
) -> StorageBackend:
    if backend == "local":
        return LocalDiskStorage(local_path)
    if backend == "s3":
        return S3Storage(
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            bucket=bucket,
        )
    raise ValueError(f"unknown storage backend: {backend!r}")
