"""REST endpoints for resolved daily schedules and now-next snapshots."""

from datetime import date

from fastapi import APIRouter, Depends, Query, Response, status

from app.routers.deps import get_schedule_service
from app.schemas.schedule import (
    DayScheduleResponse,
    ExtendScheduleRequest,
    ExtendScheduleResponse,
    GridExceptionResponse,
    NowNextResponse,
    TravelModeUpsertRequest,
    TravelOverlayResponse,
)
from app.services.schedule_service import ScheduleService

router = APIRouter(prefix="/schedule", tags=["Schedule"])


@router.get("/day", response_model=DayScheduleResponse)
def get_day_schedule(
    target_date: date | None = Query(default=None),
    service: ScheduleService = Depends(get_schedule_service),
) -> DayScheduleResponse:
    """Resolve the full timeline for a target date (defaults to Pacific/Auckland today)."""
    schedule = service.get_day_schedule(target_date=target_date)
    return DayScheduleResponse.from_domain(schedule)


@router.get("/now-next", response_model=NowNextResponse)
def get_now_next(
    target_date: date | None = Query(default=None),
    now_minute: int | None = Query(default=None),
    service: ScheduleService = Depends(get_schedule_service),
) -> NowNextResponse:
    """Compute active, upcoming, open-time, and quiet-hours state."""
    state = service.get_now_next(target_date=target_date, now_minute=now_minute)
    return NowNextResponse.from_domain(state)


@router.post("/extend", response_model=ExtendScheduleResponse)
def extend_schedule_block(
    payload: ExtendScheduleRequest,
    service: ScheduleService = Depends(get_schedule_service),
) -> ExtendScheduleResponse:
    """Extend a recurring grid block by 15m or 30m (2x cap) and trim flexible blocks."""
    extended, trimmed = service.extend_grid_block(
        target_date=payload.target_date,
        grid_block_id=payload.grid_block_id,
        minutes=payload.minutes,
    )
    return ExtendScheduleResponse(
        extended_exception=GridExceptionResponse.from_domain(extended),
        trimmed_exceptions=[GridExceptionResponse.from_domain(item) for item in trimmed],
    )


@router.get("/travel-mode", response_model=list[TravelOverlayResponse])
def list_travel_overlays(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    external_ref: str | None = Query(default=None),
    service: ScheduleService = Depends(get_schedule_service),
) -> list[TravelOverlayResponse]:
    """List date-bounded travel mode overlays."""
    items = service.list_travel_overlays(
        start_date=start_date,
        end_date=end_date,
        external_ref=external_ref,
    )
    return [TravelOverlayResponse.from_domain(item) for item in items]


@router.post("/travel-mode", response_model=TravelOverlayResponse)
def upsert_travel_mode(
    payload: TravelModeUpsertRequest,
    service: ScheduleService = Depends(get_schedule_service),
) -> TravelOverlayResponse:
    """Activate/upsert or idempotently clear a travel mode overlay by external_ref."""
    overlay = service.upsert_travel_overlay(
        external_ref=payload.external_ref,
        label=payload.label,
        start_date=payload.start_date,
        end_date=payload.end_date,
        mode=payload.mode,
        active=payload.active,
    )
    if overlay is None:
        return TravelOverlayResponse(
            id=None,
            external_ref=payload.external_ref.strip(),
            label=payload.label.strip(),
            start_date=payload.start_date,
            end_date=payload.end_date,
            mode=payload.mode,
            active=False,
        )
    return TravelOverlayResponse.from_domain(overlay)


@router.delete("/travel-mode/{external_ref}", status_code=status.HTTP_204_NO_CONTENT)
def delete_travel_mode(
    external_ref: str,
    service: ScheduleService = Depends(get_schedule_service),
) -> Response:
    """Idempotently delete a travel mode overlay by external_ref."""
    service.delete_travel_overlay(external_ref)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
