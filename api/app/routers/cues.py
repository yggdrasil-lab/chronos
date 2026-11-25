"""REST endpoints for habit cues and weekly grid block anchoring."""

from datetime import date

from fastapi import APIRouter, Depends, Query, Response, status

from app.routers.deps import get_cue_service, get_passive_signal_service
from app.schemas.cues import (
    HabitCueCreate,
    HabitCueResponse,
    HabitCueUpdate,
    PassiveSignalsResponse,
)
from app.services.cue_service import CueService
from app.services.passive_signal_service import PassiveSignalService

router = APIRouter(prefix="/cues", tags=["Habit Cues"])


@router.get("/passive-signals", response_model=PassiveSignalsResponse)
def get_passive_signals(
    target_date: date | None = Query(default=None),
    service: PassiveSignalService = Depends(get_passive_signal_service),
) -> PassiveSignalsResponse:
    """Evaluate silent passive habit signals across Eir, Kratos, and Plutus peers."""
    report = service.get_passive_signals(target_date=target_date)
    return PassiveSignalsResponse.from_domain(report)


@router.get("", response_model=list[HabitCueResponse])
def list_cues(
    active_only: bool = Query(default=False),
    grid_block_id: int | None = Query(default=None),
    zone_chain: str | None = Query(default=None),
    service: CueService = Depends(get_cue_service),
) -> list[HabitCueResponse]:
    """List habit cues ordered by (zone_chain, chain_order, id)."""
    cues = service.list_cues(
        active_only=active_only,
        grid_block_id=grid_block_id,
        zone_chain=zone_chain,
    )
    return [HabitCueResponse.from_domain(item) for item in cues]


@router.post("", response_model=HabitCueResponse, status_code=status.HTTP_201_CREATED)
def create_cue(
    payload: HabitCueCreate,
    service: CueService = Depends(get_cue_service),
) -> HabitCueResponse:
    """Create a habit cue optionally anchored to weekly grid blocks."""
    cue = service.create_cue(
        title=payload.title,
        zone_chain=payload.zone_chain,
        chain_order=payload.chain_order,
        passive_rule=payload.passive_rule,
        active=payload.active,
        notes=payload.notes,
        grid_block_ids=payload.grid_block_ids,
    )
    return HabitCueResponse.from_domain(cue)


@router.patch("/{cue_id}", response_model=HabitCueResponse)
def update_cue(
    cue_id: int,
    payload: HabitCueUpdate,
    service: CueService = Depends(get_cue_service),
) -> HabitCueResponse:
    """Partially update a habit cue and optionally replace anchored grid blocks."""
    cue = service.update_cue(
        cue_id,
        title=payload.title,
        zone_chain=payload.zone_chain,
        chain_order=payload.chain_order,
        passive_rule=payload.passive_rule,
        passive_rule_provided="passive_rule" in payload.model_fields_set,
        active=payload.active,
        notes=payload.notes,
        notes_provided="notes" in payload.model_fields_set,
        grid_block_ids=payload.grid_block_ids,
    )
    return HabitCueResponse.from_domain(cue)


@router.delete("/{cue_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cue(
    cue_id: int,
    service: CueService = Depends(get_cue_service),
) -> Response:
    """Delete a habit cue, cascading block links and detaching friction notes."""
    service.delete_cue(cue_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
