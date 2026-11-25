"""REST endpoints for recurring weekly grid blocks."""

from fastapi import APIRouter, Depends, Query, Response, status

from app.routers.deps import get_schedule_service
from app.schemas.schedule import GridBlockCreate, GridBlockResponse, GridBlockUpdate
from app.services.schedule_service import ScheduleService

router = APIRouter(prefix="/weekly-grid", tags=["Weekly Grid"])


@router.get("", response_model=list[GridBlockResponse])
def list_weekly_grid(
    day_of_week: int | None = Query(default=None),
    active_only: bool = Query(default=False),
    service: ScheduleService = Depends(get_schedule_service),
) -> list[GridBlockResponse]:
    """List recurring weekly grid blocks."""
    blocks = service.list_grid_blocks(day_of_week=day_of_week, active_only=active_only)
    return [GridBlockResponse.from_domain(item) for item in blocks]


@router.post("/blocks", response_model=GridBlockResponse, status_code=status.HTTP_201_CREATED)
def create_grid_block(
    payload: GridBlockCreate,
    service: ScheduleService = Depends(get_schedule_service),
) -> GridBlockResponse:
    """Create a recurring weekly grid block."""
    block = service.create_grid_block(
        title=payload.title,
        day_of_week=payload.day_of_week,
        start_minute=payload.start_minute,
        end_minute=payload.end_minute,
        category=payload.category,
        nudge_mode=payload.nudge_mode,
        active=payload.active,
        notes=payload.notes,
    )
    return GridBlockResponse.from_domain(block)


@router.patch("/blocks/{block_id}", response_model=GridBlockResponse)
def update_grid_block(
    block_id: int,
    payload: GridBlockUpdate,
    service: ScheduleService = Depends(get_schedule_service),
) -> GridBlockResponse:
    """Partially update an existing weekly grid block."""
    block = service.update_grid_block(
        block_id,
        title=payload.title,
        day_of_week=payload.day_of_week,
        start_minute=payload.start_minute,
        end_minute=payload.end_minute,
        category=payload.category,
        nudge_mode=payload.nudge_mode,
        active=payload.active,
        notes=payload.notes,
        notes_provided="notes" in payload.model_fields_set,
    )
    return GridBlockResponse.from_domain(block)


@router.delete("/blocks/{block_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_grid_block(
    block_id: int,
    service: ScheduleService = Depends(get_schedule_service),
) -> Response:
    """Delete a weekly grid block and cascade its exceptions."""
    service.delete_grid_block(block_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
