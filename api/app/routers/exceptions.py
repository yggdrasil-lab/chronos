"""REST endpoints for per-date weekly grid exceptions."""

from datetime import date

from fastapi import APIRouter, Depends, Query, Response, status

from app.routers.deps import get_schedule_service
from app.schemas.schedule import GridExceptionResponse, GridExceptionUpsert
from app.services.schedule_service import ScheduleService

router = APIRouter(prefix="/exceptions", tags=["Grid Exceptions"])


@router.get("", response_model=list[GridExceptionResponse])
def list_exceptions(
    target_date: date = Query(...),
    service: ScheduleService = Depends(get_schedule_service),
) -> list[GridExceptionResponse]:
    """List per-date grid exceptions for a target date."""
    items = service.list_exceptions(target_date)
    return [GridExceptionResponse.from_domain(item) for item in items]


@router.put("", response_model=GridExceptionResponse)
def upsert_exception(
    payload: GridExceptionUpsert,
    service: ScheduleService = Depends(get_schedule_service),
) -> GridExceptionResponse:
    """Create or update a per-date exception for a weekly grid block."""
    item = service.upsert_exception(
        grid_block_id=payload.grid_block_id,
        target_date=payload.target_date,
        skipped=payload.skipped,
        start_minute=payload.start_minute,
        end_minute=payload.end_minute,
        extension_count=payload.extension_count,
        notes=payload.notes,
    )
    return GridExceptionResponse.from_domain(item)


@router.delete("/{exception_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_exception(
    exception_id: int,
    service: ScheduleService = Depends(get_schedule_service),
) -> Response:
    """Delete a per-date grid exception."""
    service.delete_exception(exception_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
