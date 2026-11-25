"""Pydantic v2 request and response schemas for Chronos REST endpoints."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.domain.models import (
    Commitment,
    CommitmentSource,
    DaySchedule,
    GridBlock,
    GridException,
    NowNextState,
    NudgeMode,
    ResolvedSlice,
    SliceKind,
)


class GridBlockCreate(BaseModel):
    """Request body for creating a weekly grid block."""

    title: str
    day_of_week: int
    start_minute: int
    end_minute: int
    category: str = "general"
    nudge_mode: NudgeMode = NudgeMode.NONE
    flexible: bool = False
    active: bool = True
    notes: str | None = None


class GridBlockUpdate(BaseModel):
    """Partial update body for a weekly grid block."""

    title: str | None = None
    day_of_week: int | None = None
    start_minute: int | None = None
    end_minute: int | None = None
    category: str | None = None
    nudge_mode: NudgeMode | None = None
    flexible: bool | None = None
    active: bool | None = None
    notes: str | None = None


class GridBlockResponse(BaseModel):
    """Serialized weekly grid block."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    day_of_week: int
    start_minute: int
    end_minute: int
    category: str
    nudge_mode: NudgeMode
    flexible: bool
    active: bool
    notes: str | None = None

    @classmethod
    def from_domain(cls, block: GridBlock) -> "GridBlockResponse":
        return cls.model_validate(block)


class GridExceptionUpsert(BaseModel):
    """Request body for creating or replacing a per-date grid exception."""

    grid_block_id: int
    target_date: date
    skipped: bool = False
    start_minute: int | None = None
    end_minute: int | None = None
    extension_count: int = 0
    notes: str | None = None


class GridExceptionResponse(BaseModel):
    """Serialized per-date grid exception."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    grid_block_id: int
    target_date: date
    skipped: bool
    start_minute: int | None = None
    end_minute: int | None = None
    extension_count: int
    notes: str | None = None

    @classmethod
    def from_domain(cls, exc: GridException) -> "GridExceptionResponse":
        return cls.model_validate(exc)


class CommitmentCreate(BaseModel):
    """Request body for creating a dated commitment."""

    title: str
    target_date: date
    start_minute: int | None = None
    end_minute: int | None = None
    all_day: bool = False
    source: CommitmentSource = CommitmentSource.MANUAL
    external_ref: str | None = None
    nudge_mode: NudgeMode | None = None
    urgent_quiet_bypass: bool = False
    notes: str | None = None


class CommitmentUpdate(BaseModel):
    """Partial update body for a dated commitment."""

    title: str | None = None
    target_date: date | None = None
    start_minute: int | None = None
    end_minute: int | None = None
    all_day: bool | None = None
    source: CommitmentSource | None = None
    external_ref: str | None = None
    nudge_mode: NudgeMode | None = None
    urgent_quiet_bypass: bool | None = None
    notes: str | None = None


class CommitmentResponse(BaseModel):
    """Serialized dated commitment."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    target_date: date
    start_minute: int | None = None
    end_minute: int | None = None
    all_day: bool
    source: CommitmentSource
    external_ref: str | None = None
    nudge_mode: NudgeMode
    urgent_quiet_bypass: bool = False
    notes: str | None = None
    has_conflict: bool = False

    @classmethod
    def from_domain(cls, item: Commitment) -> "CommitmentResponse":
        return cls.model_validate(item)


class ResolvedSliceResponse(BaseModel):
    """Serialized resolved timeline slice."""

    model_config = ConfigDict(from_attributes=True)

    slice_id: str
    kind: SliceKind
    source_id: int
    title: str
    start_minute: int
    end_minute: int
    category: str | None = None
    source: CommitmentSource | None = None
    nudge_mode: NudgeMode
    flexible: bool = False
    urgent_quiet_bypass: bool = False
    has_conflict: bool
    suppressed_tail: bool
    extension_count: int
    notes: str | None = None

    @classmethod
    def from_domain(cls, item: ResolvedSlice) -> "ResolvedSliceResponse":
        return cls.model_validate(item)


class DayScheduleResponse(BaseModel):
    """Serialized resolved schedule for a single date."""

    target_date: date
    slices: list[ResolvedSliceResponse]
    all_day_commitments: list[CommitmentResponse]
    timed_commitments: list[CommitmentResponse]
    has_conflicts: bool

    @classmethod
    def from_domain(cls, schedule: DaySchedule) -> "DayScheduleResponse":
        return cls(
            target_date=schedule.target_date,
            slices=[ResolvedSliceResponse.from_domain(s) for s in schedule.slices],
            all_day_commitments=[
                CommitmentResponse.from_domain(c) for c in schedule.all_day_commitments
            ],
            timed_commitments=[
                CommitmentResponse.from_domain(c) for c in schedule.timed_commitments
            ],
            has_conflicts=schedule.has_conflicts,
        )


class NowNextResponse(BaseModel):
    """Serialized point-in-time schedule status."""

    target_date: date
    now_minute: int
    active_slice: ResolvedSliceResponse | None = None
    active_items: list[ResolvedSliceResponse]
    upcoming: list[ResolvedSliceResponse]
    open_time_minutes: int | None = None
    is_quiet_hours: bool

    @classmethod
    def from_domain(cls, state: NowNextState) -> "NowNextResponse":
        return cls(
            target_date=state.target_date,
            now_minute=state.now_minute,
            active_slice=(
                ResolvedSliceResponse.from_domain(state.active_slice)
                if state.active_slice is not None
                else None
            ),
            active_items=[ResolvedSliceResponse.from_domain(s) for s in state.active_items],
            upcoming=[ResolvedSliceResponse.from_domain(s) for s in state.upcoming],
            open_time_minutes=state.open_time_minutes,
            is_quiet_hours=state.is_quiet_hours,
        )


class ExtendScheduleRequest(BaseModel):
    """Request body for extending an active weekly grid block on a target date."""

    target_date: date
    grid_block_id: int
    minutes: Literal[15, 30]


class ExtendScheduleResponse(BaseModel):
    """Response holding the extended block exception and any trimmed flexible exceptions."""

    extended_exception: GridExceptionResponse
    trimmed_exceptions: list[GridExceptionResponse]
