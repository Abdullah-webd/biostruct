"""Seed the demo account.

A demo should not be a live wait. Folding takes two minutes and screening
another one, which is fine for a researcher and fatal in front of an audience,
so this pre-computes both against the real engine and leaves the results
sitting in the account, finished.

What it leaves behind:

* a user you can sign in as;
* a **finished** KPC-2 structure with its real CIF already in storage, so the
  viewer paints the moment the page opens;
* a **finished** screen against it, ranked, with avibactam in place as the
  positive control.

The live buttons still work — pasting a new sequence does a real fold. This
only guarantees there is something to show if the Wi-Fi is bad, the engine is
slow, or the room is not patient.

Run it against whatever database the app is pointed at::

    .venv/bin/python scripts/seed_demo.py              # test key: fast, synthetic
    BOLTZ_API_KEY=sk_bc_ws_live_… .venv/bin/python scripts/seed_demo.py

Re-running replaces the account's seeded work rather than piling up copies.
"""

from __future__ import annotations

import argparse
import asyncio

from motor.motor_asyncio import AsyncIOMotorClient

from biostruct.config import Settings
from biostruct.db import ensure_indexes
from biostruct.engines import BoltzHostedEngine
from biostruct.lab.models import JobState
from biostruct.lab.service import PREDICTIONS, SCREENS, Lab
from biostruct.repositories.users import UserRepository
from biostruct.storage import build_storage

DEMO_EMAIL = "demo@antidote.bio"
DEMO_PASSWORD = "antidote-demo-2026"
DEMO_NAME = "Demo"

#: KPC-2, the carbapenemase behind much of the carbapenem resistance in
#: Klebsiella pneumoniae. The thing the whole pitch is about.
KPC2 = (
    "MSIQHFRVALIPFFAAFCLPVFAHPETLVKVKDAEDQLGARVGYIELDLNSGKILESFRPEERFPMMSTFKVLLCGAVLSR"
    "IDAGQEQLGRRIHYSQNDLVEYSPVTEKHLTDGMTVRELCSAAITMSDNTAANLLLTTIGGPKELTAFLHNMGDHVTRLDR"
    "WEPELNEAIPNDERDTTMPVAMATTLRKLLTGELLTLASRQQLIDWMEADKVAGPLLRSALPAGWFIADKSGAGERGSRGI"
    "IAALGPDGKPSRIVVIYTTGSQATMDERNRQIAEIGASLIKHW"
)
TITLE = "KPC-2 carbapenemase (Klebsiella pneumoniae)"

#: Generous: a live fold has taken us up to ~140s, a screen up to ~130s.
TIMEOUT_SECONDS = 900


async def wait_for(get, label: str) -> object:  # type: ignore[no-untyped-def]
    waited = 0
    while waited < TIMEOUT_SECONDS:
        await asyncio.sleep(5)
        waited += 5
        item = await get()
        if item is None:
            raise RuntimeError(f"{label} disappeared")
        if waited % 20 == 0:
            print(f"   [{waited:4}s] {label}: {item.state.value}")
        if item.state.is_terminal:
            return item
    raise RuntimeError(f"{label} did not finish within {TIMEOUT_SECONDS}s")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep",
        action="store_true",
        help="add to the demo account instead of replacing its seeded work",
    )
    args = parser.parse_args()

    cfg = Settings()  # type: ignore[call-arg]
    client = AsyncIOMotorClient(cfg.mongodb_uri, tz_aware=True)
    db = client[cfg.mongodb_db]
    await ensure_indexes(db)

    users = UserRepository(db)
    user = await users.get_by_email(DEMO_EMAIL)
    if user is None:
        user = await users.create(email=DEMO_EMAIL, name=DEMO_NAME, password=DEMO_PASSWORD)
        if user is None:
            print(f"Could not create {DEMO_EMAIL}")
            return 1
        print(f"created {DEMO_EMAIL}")
    else:
        print(f"{DEMO_EMAIL} already exists")

    if not args.keep:
        removed = await db[PREDICTIONS].delete_many({"user_id": user.id})
        await db[SCREENS].delete_many({"user_id": user.id})
        print(f"cleared {removed.deleted_count} earlier seeded structure(s)")

    engine = BoltzHostedEngine(
        cfg.boltz_api_key.get_secret_value() or "sk_bc_ws_test_unset",
        base_url=cfg.boltz_base_url,
    )
    lab = Lab(
        db=db,
        engine=engine,
        storage=build_storage(
            backend=cfg.storage_backend,
            local_path=cfg.storage_local_path,
            db=db,
            endpoint_url=cfg.s3_endpoint_url,
            access_key_id=cfg.s3_access_key_id.get_secret_value(),
            secret_access_key=cfg.s3_secret_access_key.get_secret_value(),
            bucket=cfg.s3_bucket,
        ),
    )

    print(f"\nmode: {cfg.boltz_key_mode()}")
    print("folding KPC-2 …")
    prediction = await lab.start_prediction(user_id=user.id, sequence=KPC2, name=TITLE)
    prediction = await wait_for(  # type: ignore[assignment]
        lambda: lab.get_prediction(prediction.id, user_id=user.id), "fold"
    )
    if prediction.state is not JobState.complete:  # type: ignore[attr-defined]
        print(f"FOLD FAILED: {prediction.error}")  # type: ignore[attr-defined]
        return 1
    cif = await lab.structure_bytes(prediction.id, user_id=user.id)  # type: ignore[attr-defined]
    print(
        f"   confidence {prediction.structure_confidence} · "  # type: ignore[attr-defined]
        f"structure {len(cif or b'')} bytes"
    )

    print("\nscreening against it …")
    screen = await lab.start_screen(
        user_id=user.id,
        prediction_id=prediction.id,  # type: ignore[attr-defined]
        ambler_class="A",
    )
    screen = await wait_for(  # type: ignore[assignment]
        lambda: lab.get_screen(screen.id, user_id=user.id), "screen"
    )
    if screen.state is not JobState.complete:  # type: ignore[attr-defined]
        print(f"SCREEN FAILED: {screen.error}")  # type: ignore[attr-defined]
        return 1

    print(
        f"   {screen.returned}/{screen.submitted} returned · "  # type: ignore[attr-defined]
        f"control {screen.control_name} at #{screen.control_rank}"  # type: ignore[attr-defined]
    )
    for hit in screen.hits[:5]:  # type: ignore[attr-defined]
        mark = "  <-- control" if hit.is_control else ""
        print(f"   #{hit.rank} {hit.name or hit.external_id}{mark}")

    if not screen.livemode:  # type: ignore[attr-defined]
        print(
            "\nNOTE: this was seeded with a TEST key, so the demo account shows "
            "synthetic molecules named mol_N. Re-run with a live key before the "
            "real demo."
        )

    print(f"\ndemo account ready — {DEMO_EMAIL} / {DEMO_PASSWORD}")
    await engine.aclose()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
