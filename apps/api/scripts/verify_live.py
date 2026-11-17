"""Prove the live path works before a demo. Spends real Boltz credits.

A fold of KPC-2 (~$0.025) and a small screen against it (~$0.125). Runs the
real Lab service, so it exercises exactly the code the app runs.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path

from motor.motor_asyncio import AsyncIOMotorClient

from biostruct.db import ensure_indexes
from biostruct.engines import BoltzHostedEngine
from biostruct.lab.models import JobState
from biostruct.lab.service import Lab
from biostruct.storage import LocalDiskStorage

KPC = (
    "MSIQHFRVALIPFFAAFCLPVFAHPETLVKVKDAEDQLGARVGYIELDLNSGKILESFRPEERFPMMSTFKVLLCGAVLSR"
    "IDAGQEQLGRRIHYSQNDLVEYSPVTEKHLTDGMTVRELCSAAITMSDNTAANLLLTTIGGPKELTAFLHNMGDHVTRLDR"
    "WEPELNEAIPNDERDTTMPVAMATTLRKLLTGELLTLASRQQLIDWMEADKVAGPLLRSALPAGWFIADKSGAGERGSRGI"
    "IAALGPDGKPSRIVVIYTTGSQATMDERNRQIAEIGASLIKHW"
)
USER = "live-check"


async def wait(get, label, limit=1500):  # type: ignore[no-untyped-def]
    waited = 0
    while waited < limit:
        await asyncio.sleep(10)
        waited += 10
        item = await get()
        print(f"   [{waited:4}s] {label}: {item.state.value}")
        if item.state.is_terminal:
            return item
    raise SystemExit(f"{label} never finished")


def _live_key() -> str:
    """The live key, from the environment or straight out of .env.

    Read here rather than by sourcing the shell: .env contains an unquoted "&",
    so `source .env` stops partway through and leaves half the settings unset.
    """
    key = os.environ.get("BOLTZ_LIVE_API_KEY", "")
    if "_live_" in key:
        return key
    env = Path(__file__).resolve().parents[2] / ".env"
    if env.exists():
        found = re.search(r"^BOLTZ_LIVE_API_KEY=(.+)$", env.read_text(), re.M)
        return found.group(1).strip() if found else ""
    return ""


async def main() -> int:
    # Read the same way the app does. The .env contains an unquoted "&", so
    # shell sourcing it silently stops partway through.
    from biostruct.config import Settings

    cfg = Settings()  # type: ignore[call-arg]
    key = _live_key()
    if "_live_" not in key:
        print("No live key found in BOLTZ_LIVE_API_KEY")
        return 1
    uri = cfg.mongodb_uri
    client = AsyncIOMotorClient(uri, tz_aware=True)
    db = client["antidote_livecheck"]
    for c in ("lab_predictions", "lab_screens"):
        await db[c].delete_many({})
    await ensure_indexes(db)

    engine = BoltzHostedEngine(key)
    lab = Lab(
        db=db,
        engine=engine,
        storage=LocalDiskStorage(Path(tempfile.gettempdir()) / "antidote-livecheck"),
    )

    print("STEP 2 — folding KPC-2 for real (~$0.025)")
    pred = await lab.start_prediction(user_id=USER, sequence=KPC, name="KPC-2 live")
    pred = await wait(lambda: lab.get_prediction(pred.id, user_id=USER), "fold")
    if pred.state is not JobState.complete:
        print("FOLD FAILED:", pred.error)
        return 1
    cif = await lab.structure_bytes(pred.id, user_id=USER)
    print(f"   livemode={pred.livemode} confidence={pred.structure_confidence} ptm={pred.ptm}")
    print(f"   structure: {len(cif or b'')} bytes, starts {(cif or b'')[:24]!r}")
    if not cif or b"data_" not in cif[:40]:
        print("   WARNING: structure does not look like a CIF")

    if "--fold-only" in sys.argv:
        await engine.aclose()
        client.close()
        return 0

    print("\nSTEP 3 — screening against it for real")
    scr = await lab.start_screen(user_id=USER, prediction_id=pred.id, ambler_class="A")
    scr = await wait(lambda: lab.get_screen(scr.id, user_id=USER), "screen", limit=2400)
    if scr.state is not JobState.complete:
        print("SCREEN FAILED:", scr.error)
        return 1
    print(f"   livemode={scr.livemode} submitted={scr.submitted} returned={scr.returned}")
    print(f"   control: {scr.control_name} at #{scr.control_rank} -> ok={scr.control_looks_right}")
    for h in scr.hits[:10]:
        tag = "  <-- CONTROL" if h.is_control else ""
        ki = f"{h.known_potency_nm} nM" if h.known_potency_nm else "-"
        label = (h.name or h.external_id)[:26]
        binding = h.binding_confidence
        print(f"   #{h.rank:2} {label:26} binding={binding} ki={ki}{tag}")

    await engine.aclose()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
