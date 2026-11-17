"""The whole API: fold a sequence, then screen against it.

Six endpoints. Everything a researcher can do here is one of them.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, ConfigDict, Field

from biostruct import errors
from biostruct.deps import CsrfDep, CurrentUser, LabDep
from biostruct.lab.models import Hit, JobState, Prediction, PredictionCreate, Screen
from biostruct.lab.service import LabError

router = APIRouter(tags=["lab"])


class PredictionOut(BaseModel):
    id: str
    name: str
    length: int
    state: JobState
    error: str
    livemode: bool
    structure_confidence: float | None
    ptm: float | None
    has_structure: bool
    created_at: str

    @classmethod
    def of(cls, p: Prediction) -> PredictionOut:
        return cls(
            id=p.id,
            name=p.display_name(),
            length=p.length,
            state=p.state,
            error=p.error,
            livemode=p.livemode,
            structure_confidence=p.structure_confidence,
            ptm=p.ptm,
            has_structure=p.has_structure,
            created_at=p.created_at.isoformat(),
        )


class ScreenOut(BaseModel):
    id: str
    prediction_id: str
    state: JobState
    error: str
    livemode: bool
    submitted: int
    returned: int
    control_rank: int | None
    control_name: str
    control_looks_right: bool | None
    hits: list[Hit]
    created_at: str

    @classmethod
    def of(cls, s: Screen) -> ScreenOut:
        return cls(
            id=s.id,
            prediction_id=s.prediction_id,
            state=s.state,
            error=s.error,
            livemode=s.livemode,
            submitted=s.submitted,
            returned=s.returned,
            control_rank=s.control_rank,
            control_name=s.control_name,
            control_looks_right=s.control_looks_right,
            hits=s.hits,
            created_at=s.created_at.isoformat(),
        )


class ScreenCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    #: Ambler class of the enzyme, which decides the positive control. A is
    #: KPC/CTX-M/TEM/SHV, B the metallo-enzymes NDM/VIM/IMP, C AmpC/CMY, D the
    #: OXA enzymes.
    ambler_class: str = Field(default="A", max_length=1)


@router.post("/predictions", status_code=status.HTTP_202_ACCEPTED, dependencies=[CsrfDep])
async def create_prediction(
    data: PredictionCreate, user: CurrentUser, lab: LabDep
) -> PredictionOut:
    """Step 2: a pasted sequence becomes a predicted structure.

    Returns immediately. Folding takes minutes, and an HTTP request held open
    for them times out in every proxy in between.
    """
    try:
        prediction = await lab.start_prediction(
            user_id=user.id, sequence=data.sequence, name=data.name
        )
    except LabError as exc:
        raise errors.validation_problem(str(exc)) from exc
    return PredictionOut.of(prediction)


@router.get("/predictions")
async def list_predictions(user: CurrentUser, lab: LabDep) -> list[PredictionOut]:
    return [PredictionOut.of(p) for p in await lab.list_predictions(user.id)]


@router.get("/predictions/{prediction_id}")
async def get_prediction(prediction_id: str, user: CurrentUser, lab: LabDep) -> PredictionOut:
    prediction = await lab.get_prediction(prediction_id, user_id=user.id)
    if prediction is None:
        raise errors.not_found("Structure")
    return PredictionOut.of(prediction)


@router.get("/predictions/{prediction_id}/structure.cif")
async def get_structure(prediction_id: str, user: CurrentUser, lab: LabDep) -> Response:
    """Served from our own storage, never the engine's presigned URL — theirs
    expires, and a viewer that works for a few days then stops is worse than
    one that never worked."""
    data = await lab.structure_bytes(prediction_id, user_id=user.id)
    if data is None:
        raise errors.not_found("Structure file")
    return Response(
        content=data,
        media_type="chemical/x-cif",
        headers={
            "content-disposition": f'inline; filename="{prediction_id[:8]}.cif"',
            "cache-control": "private, max-age=3600",
        },
    )


@router.post(
    "/predictions/{prediction_id}/screens",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[CsrfDep],
)
async def create_screen(
    prediction_id: str, data: ScreenCreate, user: CurrentUser, lab: LabDep
) -> ScreenOut:
    """Step 3: rank molecules against the structure."""
    try:
        screen = await lab.start_screen(
            user_id=user.id, prediction_id=prediction_id, ambler_class=data.ambler_class
        )
    except LabError as exc:
        raise errors.validation_problem(str(exc)) from exc
    return ScreenOut.of(screen)


@router.get("/predictions/{prediction_id}/screens")
async def list_screens(prediction_id: str, user: CurrentUser, lab: LabDep) -> list[ScreenOut]:
    return [ScreenOut.of(s) for s in await lab.screens_for(prediction_id, user_id=user.id)]


#: Columns in the order a bench scientist reads them. Predicted and measured
#: are separated and labelled, because a spreadsheet outlives the page that
#: produced it and nobody will remember which number came from where.
CSV_COLUMNS: tuple[tuple[str, str], ...] = (
    ("rank", "rank"),
    ("name", "name"),
    ("external_id", "chembl_id"),
    ("smiles", "smiles"),
    ("is_control", "is_positive_control"),
    ("binding_confidence", "binding_confidence_PREDICTED"),
    ("structure_confidence", "structure_confidence_PREDICTED"),
    ("known_potency_nm", "ki_nM_MEASURED"),
    ("lipophilicity", "lipophilicity_PREDICTED"),
    ("permeability", "permeability_PREDICTED"),
    ("solubility", "solubility_PREDICTED"),
)


@router.get("/screens/{screen_id}/shortlist.csv")
async def export_shortlist(screen_id: str, user: CurrentUser, lab: LabDep) -> Response:
    """The shortlist, as a file to take to a laboratory.

    It carries its own caveats in a header comment, because the moment it
    leaves the screen nothing else is there to explain it.
    """
    screen = await lab.get_screen(screen_id, user_id=user.id)
    if screen is None:
        raise errors.not_found("Screen")

    buffer = io.StringIO()
    buffer.write(f"# Antidote shortlist, exported {datetime.now(UTC).isoformat()}\n")
    buffer.write(
        "# Columns marked PREDICTED are model output, not measurements. "
        "Columns marked MEASURED come from ChEMBL assay records.\n"
    )
    buffer.write(
        "# Computational predictions for laboratory testing, not evidence of "
        "therapeutic effect. Nothing here assesses toxicity or safety.\n"
    )
    if screen.control_rank:
        buffer.write(
            f"# {screen.control_name}, a known inhibitor of this enzyme class, "
            f"ranked #{screen.control_rank}. If that is not near the top, treat "
            "this ranking as unreliable.\n"
        )
    if not screen.livemode:
        buffer.write(
            "# TEST MODE: the engine substituted its own molecules. These rows are "
            "NOT the submitted library and the scores are meaningless.\n"
        )

    writer = csv.writer(buffer)
    writer.writerow([header for _, header in CSV_COLUMNS])
    for hit in screen.hits:
        row: dict[str, Any] = hit.model_dump()
        writer.writerow([row.get(key, "") for key, _ in CSV_COLUMNS])

    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"content-disposition": f'attachment; filename="antidote-{screen_id[:8]}.csv"'},
    )
