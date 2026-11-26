"""REST endpoints for dated commitments."""

from datetime import date

from fastapi import APIRouter, Depends, Query, Response, status

from app.routers.deps import get_schedule_service
from app.schemas.schedule import CommitmentCreate, CommitmentResponse, CommitmentUpdate
from app.services.schedule_service import ScheduleService

router = APIRouter(prefix="/commitments", tags=["Commitments"])


@router.get("", response_model=list[CommitmentResponse])
def list_commitments(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    service: ScheduleService = Depends(get_schedule_service),
) -> list[CommitmentResponse]:
    """List commitments optionally filtered by inclusive date range."""
    items = service.list_commitments(start_date=start_date, end_date=end_date)
    return [CommitmentResponse.from_domain(item) for item in items]


@router.post("", response_model=CommitmentResponse, status_code=status.HTTP_201_CREATED)
def create_commitment(
    payload: CommitmentCreate,
    service: ScheduleService = Depends(get_schedule_service),
) -> CommitmentResponse:
    """Create a dated commitment."""
    item = service.create_commitment(
        title=payload.title,
        target_date=payload.target_date,
        start_minute=payload.start_minute,
        end_minute=payload.end_minute,
        all_day=payload.all_day,
        source=payload.source,
        external_ref=payload.external_ref,
        nudge_mode=payload.nudge_mode,
        urgent_quiet_bypass=payload.urgent_quiet_bypass,
        tentative=payload.tentative,
        notes=payload.notes,
    )
    return CommitmentResponse.from_domain(item)


@router.patch("/{commitment_id}", response_model=CommitmentResponse)
def update_commitment(
    commitment_id: int,
    payload: CommitmentUpdate,
    service: ScheduleService = Depends(get_schedule_service),
) -> CommitmentResponse:
    """Partially update an existing commitment."""
    fields = payload.model_fields_set
    item = service.update_commitment(
        commitment_id,
        title=payload.title,
        target_date=payload.target_date,
        start_minute=payload.start_minute,
        end_minute=payload.end_minute,
        start_provided="start_minute" in fields,
        end_provided="end_minute" in fields,
        all_day=payload.all_day,
        source=payload.source,
        external_ref=payload.external_ref,
        external_ref_provided="external_ref" in fields,
        nudge_mode=payload.nudge_mode,
        urgent_quiet_bypass=payload.urgent_quiet_bypass,
        tentative=payload.tentative,
        tentative_provided="tentative" in fields,
        notes=payload.notes,
        notes_provided="notes" in fields,
    )
    return CommitmentResponse.from_domain(item)


@router.delete("/{commitment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_commitment(
    commitment_id: int,
    service: ScheduleService = Depends(get_schedule_service),
) -> Response:
    """Delete a commitment by ID."""
    service.delete_commitment(commitment_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
