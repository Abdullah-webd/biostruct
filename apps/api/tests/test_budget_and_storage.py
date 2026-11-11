"""Budget arithmetic and artifact mirroring.

These two are what stand between a bug and either an empty account or lost
structures, so they get tested to their edges.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from biostruct.budget import BudgetExceeded, BudgetService, BudgetState
from biostruct.storage import (
    LocalDiskStorage,
    StorageError,
    artifact_key,
    mirror_url,
)


class TestBudgetState:
    def test_remaining_accounts_for_reservations(self) -> None:
        state = BudgetState(
            limit_usd=Decimal("25"),
            spent_usd=Decimal("5"),
            reserved_usd=Decimal("10"),
        )
        assert state.committed_usd == Decimal("15")
        assert state.remaining_usd == Decimal("10")

    def test_decimal_arithmetic_is_exact(self) -> None:
        """0.05 x 3 must be 0.15, not 0.15000000000000002 (ADR-017)."""
        state = BudgetState(
            limit_usd=Decimal("1"),
            spent_usd=Decimal("0.05") * 3,
            reserved_usd=Decimal("0"),
        )
        assert state.spent_usd == Decimal("0.15")
        assert str(state.remaining_usd) == "0.85"

    def test_warns_past_eighty_percent(self) -> None:
        assert BudgetState(Decimal("10"), Decimal("8"), Decimal("0")).should_warn is True
        assert BudgetState(Decimal("10"), Decimal("7.99"), Decimal("0")).should_warn is False

    def test_reservations_count_towards_the_warning(self) -> None:
        """Otherwise a project with everything reserved looks healthy."""
        assert BudgetState(Decimal("10"), Decimal("0"), Decimal("9")).should_warn is True

    def test_zero_limit_is_treated_as_fully_used(self) -> None:
        assert BudgetState(Decimal("0"), Decimal("0"), Decimal("0")).fraction_used == 1


class TestBudgetService:
    async def test_reserve_then_settle(self, client, settings) -> None:
        from biostruct.db import database

        db = database.db
        assert db is not None
        service = BudgetService(db)
        await db.projects.insert_one(
            {
                "_id": "p1",
                "budget": {"limit_usd": "25", "spent_usd": "0", "reserved_usd": "0"},
            }
        )
        after = await service.reserve("p1", Decimal("5"))
        assert after.reserved_usd == Decimal("5")
        assert after.remaining_usd == Decimal("20")

        settled = await service.settle("p1", reserved_usd=Decimal("5"), actual_usd=Decimal("4.75"))
        assert settled is not None
        assert settled.reserved_usd == Decimal("0")
        assert settled.spent_usd == Decimal("4.75")
        assert settled.remaining_usd == Decimal("20.25")

    async def test_reserve_refuses_beyond_the_limit(self, client, settings) -> None:
        from biostruct.db import database

        db = database.db
        assert db is not None
        await db.projects.insert_one(
            {
                "_id": "p2",
                "budget": {"limit_usd": "5", "spent_usd": "0", "reserved_usd": "0"},
            }
        )
        service = BudgetService(db)
        with pytest.raises(BudgetExceeded) as exc:
            await service.reserve("p2", Decimal("5.01"))
        assert exc.value.remaining == Decimal("5")

    async def test_exact_limit_is_allowed(self, client, settings) -> None:
        from biostruct.db import database

        db = database.db
        assert db is not None
        await db.projects.insert_one(
            {
                "_id": "p3",
                "budget": {"limit_usd": "5", "spent_usd": "0", "reserved_usd": "0"},
            }
        )
        state = await BudgetService(db).reserve("p3", Decimal("5"))
        assert state.remaining_usd == Decimal("0")

    async def test_concurrent_reservations_cannot_both_fit(self, client, settings) -> None:
        """The reason reserve is a compare-and-set rather than read-then-write."""
        import asyncio

        from biostruct.db import database

        db = database.db
        assert db is not None
        await db.projects.insert_one(
            {
                "_id": "p4",
                "budget": {"limit_usd": "10", "spent_usd": "0", "reserved_usd": "0"},
            }
        )
        service = BudgetService(db)
        outcomes = await asyncio.gather(
            service.reserve("p4", Decimal("6")),
            service.reserve("p4", Decimal("6")),
            return_exceptions=True,
        )
        successes = [o for o in outcomes if isinstance(o, BudgetState)]
        assert len(successes) == 1, "two 6-dollar holds must not both fit in 10"

        final = await service.get("p4")
        assert final is not None
        assert final.reserved_usd == Decimal("6")

    async def test_release_charges_nothing(self, client, settings) -> None:
        """A failed run must not bill the researcher."""
        from biostruct.db import database

        db = database.db
        assert db is not None
        await db.projects.insert_one(
            {
                "_id": "p5",
                "budget": {"limit_usd": "25", "spent_usd": "0", "reserved_usd": "0"},
            }
        )
        service = BudgetService(db)
        await service.reserve("p5", Decimal("5"))
        released = await service.release("p5", Decimal("5"))
        assert released is not None
        assert released.spent_usd == Decimal("0")
        assert released.reserved_usd == Decimal("0")
        assert released.remaining_usd == Decimal("25")

    async def test_settle_never_drives_a_reservation_negative(self, client, settings) -> None:
        """Defensive: bookkeeping drift must not corrupt the budget."""
        from biostruct.db import database

        db = database.db
        assert db is not None
        await db.projects.insert_one(
            {
                "_id": "p6",
                "budget": {"limit_usd": "25", "spent_usd": "0", "reserved_usd": "1"},
            }
        )
        settled = await BudgetService(db).settle(
            "p6", reserved_usd=Decimal("5"), actual_usd=Decimal("1")
        )
        assert settled is not None
        assert settled.reserved_usd == Decimal("0")

    async def test_negative_amounts_are_refused(self, client, settings) -> None:
        from biostruct.budget import BudgetError
        from biostruct.db import database

        db = database.db
        assert db is not None
        await db.projects.insert_one(
            {
                "_id": "p7",
                "budget": {"limit_usd": "25", "spent_usd": "0", "reserved_usd": "0"},
            }
        )
        with pytest.raises(BudgetError):
            await BudgetService(db).reserve("p7", Decimal("-1"))

    async def test_unknown_project_is_an_error_not_a_free_pass(self, client, settings) -> None:
        from biostruct.budget import BudgetError
        from biostruct.db import database

        db = database.db
        assert db is not None
        with pytest.raises(BudgetError, match="no such project"):
            await BudgetService(db).reserve("missing", Decimal("1"))


class TestArtifactKeys:
    def test_keys_are_deterministic(self) -> None:
        """So a retried mirror overwrites rather than duplicating."""
        a = artifact_key("run1", "res1", "structure", "cif")
        b = artifact_key("run1", "res1", "structure", "cif")
        assert a == b == "runs/run1/structure/res1.cif"

    def test_keys_are_namespaced_by_run(self) -> None:
        assert artifact_key("run1", "r", "structure", "cif") != artifact_key(
            "run2", "r", "structure", "cif"
        )

    def test_slashes_in_result_ids_do_not_create_directories(self) -> None:
        key = artifact_key("run1", "a/b", "structure", "cif")
        assert key == "runs/run1/structure/a_b.cif"


class TestLocalDiskStorage:
    async def test_round_trip(self, tmp_path) -> None:
        storage = LocalDiskStorage(tmp_path)
        stored = await storage.put_bytes("a/b.cif", b"data_test\n")
        assert stored.size_bytes == 10
        assert await storage.get_bytes("a/b.cif") == b"data_test\n"
        assert await storage.exists("a/b.cif") is True

    async def test_missing_key_raises(self, tmp_path) -> None:
        with pytest.raises(StorageError, match="not stored"):
            await LocalDiskStorage(tmp_path).get_bytes("nope")

    async def test_path_traversal_is_refused(self, tmp_path) -> None:
        """Keys are built from ids, but they are still strings."""
        with pytest.raises(StorageError, match="outside the storage root"):
            await LocalDiskStorage(tmp_path).put_bytes("../escaped.cif", b"x")

    async def test_overwrite_is_clean(self, tmp_path) -> None:
        storage = LocalDiskStorage(tmp_path)
        await storage.put_bytes("k", b"longer original content")
        await storage.put_bytes("k", b"short")
        assert await storage.get_bytes("k") == b"short"

    async def test_no_partial_files_are_left_behind(self, tmp_path) -> None:
        """Writes land via a temp file and a rename, so a crash cannot leave a
        truncated file that later looks like a successful mirror."""
        storage = LocalDiskStorage(tmp_path)
        await storage.put_bytes("k", b"content")
        assert list(tmp_path.rglob("*.partial")) == []

    async def test_delete_and_delete_prefix(self, tmp_path) -> None:
        storage = LocalDiskStorage(tmp_path)
        await storage.put_bytes("runs/r1/structure/a.cif", b"a")
        await storage.put_bytes("runs/r1/structure/b.cif", b"b")
        assert await storage.delete_prefix("runs/r1") == 2
        assert await storage.exists("runs/r1/structure/a.cif") is False

    async def test_delete_of_a_missing_key_is_harmless(self, tmp_path) -> None:
        await LocalDiskStorage(tmp_path).delete("not-there")


class TestMirroring:
    """The 30-minute deadline is the whole reason this exists."""

    async def test_mirrors_a_presigned_url_into_storage(self, tmp_path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="data_biostruct\n_atom_site.id\n")

        storage = LocalDiskStorage(tmp_path)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            stored = await mirror_url(
                storage=storage,
                url="https://example.org/presigned?sig=x",
                key="runs/r/structure/c.cif",
                client=http,
            )
        assert stored.size_bytes > 0
        body = await storage.get_bytes("runs/r/structure/c.cif")
        assert body.startswith(b"data_")

    async def test_expired_url_is_reported_clearly(self, tmp_path) -> None:
        """An expired link is the failure this system is designed around."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="<Error>AccessDenied</Error>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(StorageError, match="expired"):
                await mirror_url(
                    storage=LocalDiskStorage(tmp_path),
                    url="https://example.org/stale",
                    key="k",
                    client=http,
                )

    async def test_transport_failure_is_a_storage_error(self, tmp_path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(StorageError, match="download failed"):
                await mirror_url(
                    storage=LocalDiskStorage(tmp_path),
                    url="https://example.org/x",
                    key="k",
                    client=http,
                )

    async def test_empty_response_is_refused(self, tmp_path) -> None:
        """Storing an empty file would look like a successful mirror."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(StorageError, match="no data"):
                await mirror_url(
                    storage=LocalDiskStorage(tmp_path),
                    url="https://example.org/x",
                    key="k",
                    client=http,
                )

    async def test_oversized_artifact_is_refused(self, tmp_path, monkeypatch) -> None:
        import biostruct.storage as storage_module

        monkeypatch.setattr(storage_module, "MAX_ARTIFACT_BYTES", 10)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"x" * 100)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(StorageError, match="over the"):
                await storage_module.mirror_url(
                    storage=LocalDiskStorage(tmp_path),
                    url="https://example.org/big",
                    key="k",
                    client=http,
                )
