"""REST endpoints for friction reflection notes and weekly summaries."""

from datetime import date

from fastapi import APIRouter, Depends, Query, Response, status

from app.routers.deps import get_cue_service
from app.schemas.cues import FrictionNoteCreate, FrictionNoteResponse, WeeklyReflectionResponse
from app.services.cue_service import CueService

router = APIRouter(prefix="/friction-notes", tags=["Friction Notes"])


@router.get("", response_model=list[FrictionNoteResponse])
def list_friction_notes(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    habit_cue_id: int | None = Query(default=None),
    grid_block_id: int | None = Query(default=None),
    limit: int = Query(default=50),
    service: CueService = Depends(get_cue_service),
) -> list[FrictionNoteResponse]:
    """List friction notes ordered newest-first by (target_date, created_at, id)."""
    notes = service.list_friction_notes(
        start_date=start_date,
        end_date=end_date,
        habit_cue_id=habit_cue_id,
        grid_block_id=grid_block_id,
        limit=limit,
    )
    return [FrictionNoteResponse.from_domain(item) for item in notes]


@router.post("", response_model=FrictionNoteResponse, status_code=status.HTTP_201_CREATED)
def create_friction_note(
    payload: FrictionNoteCreate,
    service: CueService = Depends(get_cue_service),
) -> FrictionNoteResponse:
    """Record a friction reflection note."""
    item = service.create_friction_note(
        target_date=payload.target_date,
        tag=payload.tag,
        note=payload.note,
        habit_cue_id=payload.habit_cue_id,
        grid_block_id=payload.grid_block_id,
    )
    return FrictionNoteResponse.from_domain(item)


@router.delete("/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_friction_note(
    note_id: int,
    service: CueService = Depends(get_cue_service),
) -> Response:
    """Delete a friction reflection note."""
    service.delete_friction_note(note_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/reflection", response_model=WeeklyReflectionResponse)
def get_weekly_reflection(
    week_start: date | None = Query(default=None),
    service: CueService = Depends(get_cue_service),
) -> WeeklyReflectionResponse:
    """Summarize 7 days of friction notes grouped by tag, cue, and unlinked notes."""
    reflection = service.get_weekly_reflection(week_start=week_start)
    return WeeklyReflectionResponse.from_domain(reflection)
