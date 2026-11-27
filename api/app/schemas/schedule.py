"""Pydantic v2 request and response schemas for Chronos REST endpoints."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.fasting import FastingPhase, FastingProtocol, FastingSuggestion
from app.domain.sleep_recovery import NapRecoveryBlock, SleepPressureNudge
from app.domain.models import (
    Commitment,
    CommitmentSource,
    DaySchedule,
    DayWorkoutHydration,
    GridBlock,
    GridException,
    NowNextState,
    NudgeMode,
    ResolvedSlice,
    SliceKind,
    TravelOverlay,
    TravelOverlayMode,
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
    tentative: bool = False
    notes: str | None = None
    upsert_by_external_ref: bool = False


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
    tentative: bool | None = None
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
    tentative: bool = False
    notes: str | None = None
    upsert_by_external_ref: bool = False
    has_conflict: bool = False
    read_only: bool = False

    @classmethod
    def from_domain(cls, item: Commitment) -> "CommitmentResponse":
        return cls.model_validate(item)


class HydratedWorkoutResponse(BaseModel):
    """Serialized planned Kratos workout hydrated onto a slice or day header."""

    model_config = ConfigDict(from_attributes=True)

    title: str
    workout_type: str | None = None
    emphasis: str | None = None
    notes: str | None = None
    attached_grid_block_id: int | None = None
    placement: Literal["grid_block", "day_header"]


class CompletedKratosSessionResponse(BaseModel):
    """Serialized completed Kratos workout session."""

    model_config = ConfigDict(from_attributes=True)

    external_ref: str
    title: str
    activity_type: str | None = None
    start_minute: int
    end_minute: int
    started_at: str
    ended_at: str


class DayWorkoutHydrationResponse(BaseModel):
    """Serialized daily Kratos workout hydration summary."""

    model_config = ConfigDict(from_attributes=True)

    planned_workout: HydratedWorkoutResponse | None = None
    completed_sessions: list[CompletedKratosSessionResponse]
    peer_degraded: bool

    @classmethod
    def from_domain(cls, hydration: DayWorkoutHydration) -> "DayWorkoutHydrationResponse":
        return cls.model_validate(hydration)


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
    tentative: bool = False
    has_conflict: bool
    suppressed_tail: bool
    extension_count: int
    notes: str | None = None
    hydrated_workout: HydratedWorkoutResponse | None = None
    read_only: bool = False

    @classmethod
    def from_domain(cls, item: ResolvedSlice) -> "ResolvedSliceResponse":
        return cls.model_validate(item)


class FastingSuggestionResponse(BaseModel):
    """Serialized deterministic fasting window and live phase suggestion."""

    model_config = ConfigDict(from_attributes=True)

    protocol: FastingProtocol
    window_start: str
    window_end: str
    phase: FastingPhase
    is_optional_reset: bool
    reason: str

    @classmethod
    def from_domain(cls, suggestion: FastingSuggestion) -> "FastingSuggestionResponse":
        return cls.model_validate(suggestion)


class NapRecoveryBlockResponse(BaseModel):
    """Serialized Eir daytime nap recovery block (non-carving overlay)."""

    model_config = ConfigDict(from_attributes=True)

    start_at: str
    end_at: str
    start_minute: int
    end_minute: int
    duration_hours: float

    @classmethod
    def from_domain(cls, nap: NapRecoveryBlock) -> "NapRecoveryBlockResponse":
        return cls.model_validate(nap)


class SleepPressureNudgeResponse(BaseModel):
    """Serialized deterministic evening sleep-pressure guidance."""

    model_config = ConfigDict(from_attributes=True)

    triggered: bool
    reason: str
    suggestion: str
    latest_nap_end: str
    total_nap_hours: float
    nap_count: int

    @classmethod
    def from_domain(cls, nudge: SleepPressureNudge) -> "SleepPressureNudgeResponse":
        return cls.model_validate(nudge)


class TravelModeUpsertRequest(BaseModel):
    """Request body for activating or clearing a dated travel mode overlay."""

    external_ref: str
    label: str = ""
    start_date: date | None = None
    end_date: date | None = None
    mode: TravelOverlayMode = TravelOverlayMode.SUPPRESS_ALL_GRID_BLOCKS
    active: bool = True


class TravelOverlayResponse(BaseModel):
    """Serialized travel mode overlay."""

    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    external_ref: str
    label: str
    start_date: date | None = None
    end_date: date | None = None
    mode: TravelOverlayMode
    active: bool = True

    @classmethod
    def from_domain(cls, overlay: TravelOverlay) -> "TravelOverlayResponse":
        return cls(
            id=overlay.id,
            external_ref=overlay.external_ref,
            label=overlay.label,
            start_date=overlay.start_date,
            end_date=overlay.end_date,
            mode=overlay.mode,
            active=True,
        )


class DayScheduleResponse(BaseModel):
    """Serialized resolved schedule for a single date."""

    target_date: date
    slices: list[ResolvedSliceResponse]
    all_day_commitments: list[CommitmentResponse]
    timed_commitments: list[CommitmentResponse]
    has_conflicts: bool
    workout_hydration: DayWorkoutHydrationResponse | None = None
    fasting_suggestion: FastingSuggestionResponse | None = None
    travel_overlay: TravelOverlayResponse | None = None
    naps: list[NapRecoveryBlockResponse] = Field(default_factory=list)
    sleep_pressure_nudge: SleepPressureNudgeResponse | None = None

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
            workout_hydration=(
                DayWorkoutHydrationResponse.from_domain(schedule.workout_hydration)
                if schedule.workout_hydration is not None
                else None
            ),
            fasting_suggestion=(
                FastingSuggestionResponse.from_domain(schedule.fasting_suggestion)
                if schedule.fasting_suggestion is not None
                else None
            ),
            travel_overlay=(
                TravelOverlayResponse.from_domain(schedule.travel_overlay)
                if schedule.travel_overlay is not None
                else None
            ),
            naps=[NapRecoveryBlockResponse.from_domain(n) for n in schedule.naps],
            sleep_pressure_nudge=(
                SleepPressureNudgeResponse.from_domain(schedule.sleep_pressure_nudge)
                if schedule.sleep_pressure_nudge is not None
                else None
            ),
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
    workout_hydration: DayWorkoutHydrationResponse | None = None
    fasting_suggestion: FastingSuggestionResponse | None = None
    sleep_pressure_nudge: SleepPressureNudgeResponse | None = None

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
            workout_hydration=(
                DayWorkoutHydrationResponse.from_domain(state.workout_hydration)
                if state.workout_hydration is not None
                else None
            ),
            fasting_suggestion=(
                FastingSuggestionResponse.from_domain(state.fasting_suggestion)
                if state.fasting_suggestion is not None
                else None
            ),
            sleep_pressure_nudge=(
                SleepPressureNudgeResponse.from_domain(state.sleep_pressure_nudge)
                if state.sleep_pressure_nudge is not None
                else None
            ),
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
