"""REST endpoints for resolved daily schedules and now-next snapshots."""

from datetime import date

from fastapi import APIRouter, Depends, Query

from app.routers.deps import get_schedule_service
from app.schemas.schedule import DayScheduleResponse, NowNextResponse
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
