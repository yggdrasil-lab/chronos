"""REST endpoints for resolved daily schedules and now-next snapshots."""

from datetime import date

from fastapi import APIRouter, Depends, Query

from app.routers.deps import get_schedule_service
from app.schemas.schedule import (
    DayScheduleResponse,
    ExtendScheduleRequest,
    ExtendScheduleResponse,
    GridExceptionResponse,
    NowNextResponse,
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
