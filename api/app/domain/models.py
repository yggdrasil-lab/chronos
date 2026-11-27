"""Pure immutable domain models for Chronos schedule resolution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.domain.fasting import FastingSuggestion


class NudgeMode(str, Enum):
    """Notification strategy for a scheduled block or commitment."""

    NONE = "none"
    BOUNDARY = "boundary"
    TWO_STAGE = "two-stage"


class CommitmentSource(str, Enum):
    """Origin system for a dated commitment."""

    MANUAL = "manual"
    IRIS = "iris"
    KRATOS = "kratos"
    TRAVEL = "travel"


class TravelOverlayMode(str, Enum):
    """Grid suppression behavior while a travel overlay covers a date."""

    SUPPRESS_ALL_GRID_BLOCKS = "suppress_all_grid_blocks"
    KEEP_WORK_BLOCKS = "keep_work_blocks"


class SliceKind(str, Enum):
    """Resolved timeline slice classification."""

    GRID_BLOCK = "grid_block"
    COMMITMENT = "commitment"


class FrictionTag(str, Enum):
    """Categorical friction driver for habit cue and block misses."""

    TIRED = "tired"
    TRIGGER_MISSED = "trigger_missed"
    SCHEDULE_OVERRUN = "schedule_overrun"
    ENVIRONMENT_MISSING = "environment_missing"


@dataclass(frozen=True)
class GridBlock:
    """Recurring weekly anchor block template."""

    id: int
    title: str
    day_of_week: int
    start_minute: int
    end_minute: int
    category: str
    nudge_mode: NudgeMode = NudgeMode.NONE
    flexible: bool = False
    active: bool = True
    notes: str | None = None


@dataclass(frozen=True)
class GridException:
    """Per-date override for a recurring weekly grid block."""

    id: int
    grid_block_id: int
    target_date: date
    skipped: bool = False
    start_minute: int | None = None
    end_minute: int | None = None
    extension_count: int = 0
    notes: str | None = None


@dataclass(frozen=True)
class GridExceptionDraft:
    """Unsaved per-date grid exception produced by pure extension planning."""

    grid_block_id: int
    target_date: date
    skipped: bool = False
    start_minute: int | None = None
    end_minute: int | None = None
    extension_count: int = 0
    notes: str | None = None


@dataclass(frozen=True)
class ExtensionPlan:
    """Pure extension plan holding the extended block and any trimmed flexible blocks."""

    extended: GridExceptionDraft
    trimmed: tuple[GridExceptionDraft, ...]


@dataclass(frozen=True)
class Commitment:
    """Dated external or manual commitment that carves weekly blocks."""

    id: int
    title: str
    target_date: date
    start_minute: int | None
    end_minute: int | None
    all_day: bool
    source: CommitmentSource
    external_ref: str | None = None
    nudge_mode: NudgeMode = NudgeMode.NONE
    urgent_quiet_bypass: bool = False
    tentative: bool = False
    notes: str | None = None
    has_conflict: bool = False
    read_only: bool = False


@dataclass(frozen=True)
class HydratedWorkout:
    """Planned Kratos workout attached to a workout grid block or day header."""

    title: str
    workout_type: str | None = None
    emphasis: str | None = None
    notes: str | None = None
    attached_grid_block_id: int | None = None
    placement: Literal["grid_block", "day_header"] = "day_header"


@dataclass(frozen=True)
class CompletedKratosSession:
    """Completed Kratos workout session surfaced in daily hydration."""

    external_ref: str
    title: str
    activity_type: str | None
    start_minute: int
    end_minute: int
    started_at: str
    ended_at: str


@dataclass(frozen=True)
class DayWorkoutHydration:
    """Planned and completed Kratos workout context for a single calendar date."""

    planned_workout: HydratedWorkout | None = None
    completed_sessions: tuple[CompletedKratosSession, ...] = ()
    peer_degraded: bool = False


@dataclass(frozen=True)
class ResolvedSlice:
    """Single resolved segment on a day's timeline."""

    slice_id: str
    kind: SliceKind
    source_id: int
    title: str
    start_minute: int
    end_minute: int
    category: str | None = None
    source: CommitmentSource | None = None
    nudge_mode: NudgeMode = NudgeMode.NONE
    flexible: bool = False
    urgent_quiet_bypass: bool = False
    tentative: bool = False
    has_conflict: bool = False
    suppressed_tail: bool = False
    extension_count: int = 0
    notes: str | None = None
    hydrated_workout: HydratedWorkout | None = None
    read_only: bool = False


@dataclass(frozen=True)
class TravelOverlay:
    """Date-bounded travel mode overlay altering grid block visibility and nudges."""

    id: int
    external_ref: str
    label: str
    start_date: date
    end_date: date
    mode: TravelOverlayMode


@dataclass(frozen=True)
class DaySchedule:
    """Resolved schedule output for a single calendar date."""

    target_date: date
    slices: tuple[ResolvedSlice, ...]
    all_day_commitments: tuple[Commitment, ...]
    timed_commitments: tuple[Commitment, ...]
    workout_hydration: DayWorkoutHydration | None = None
    fasting_suggestion: FastingSuggestion | None = None
    travel_overlay: TravelOverlay | None = None

    @property
    def has_conflicts(self) -> bool:
        """Return True if any timed commitment overlaps another timed commitment."""
        return any(item.has_conflict for item in self.timed_commitments)


@dataclass(frozen=True)
class NowNextState:
    """Point-in-time operational snapshot derived from a DaySchedule."""

    target_date: date
    now_minute: int
    active_slice: ResolvedSlice | None
    active_items: tuple[ResolvedSlice, ...]
    upcoming: tuple[ResolvedSlice, ...]
    open_time_minutes: int | None
    is_quiet_hours: bool
    workout_hydration: DayWorkoutHydration | None = None
    fasting_suggestion: FastingSuggestion | None = None


@dataclass(frozen=True)
class FrictionNote:
    """Captured friction reflection tied optionally to a habit cue or grid block."""

    id: int
    target_date: date
    tag: FrictionTag
    note: str
    created_at: datetime
    habit_cue_id: int | None = None
    grid_block_id: int | None = None
    cue_title: str | None = None
    block_title: str | None = None
    cue_zone_chain: str | None = None


@dataclass(frozen=True)
class HabitCue:
    """Habit cue entry anchored to zero or more weekly grid blocks."""

    id: int
    title: str
    zone_chain: str
    chain_order: int = 0
    passive_rule: str | None = None
    active: bool = True
    notes: str | None = None
    grid_block_ids: tuple[int, ...] = ()
    recent_friction: tuple[FrictionNote, ...] = ()


@dataclass(frozen=True)
class TagReflectionGroup:
    """Weekly friction summary bucket grouped by FrictionTag."""

    tag: FrictionTag
    count: int
    notes: tuple[FrictionNote, ...]


@dataclass(frozen=True)
class CueReflectionGroup:
    """Weekly friction summary bucket grouped by HabitCue."""

    habit_cue_id: int
    cue_title: str
    zone_chain: str
    count: int
    notes: tuple[FrictionNote, ...]


@dataclass(frozen=True)
class WeeklyReflection:
    """7-day friction reflection rollup grouped by tag, cue, and unlinked notes."""

    week_start: date
    week_end: date
    total_notes: int
    by_tag: tuple[TagReflectionGroup, ...]
    by_cue: tuple[CueReflectionGroup, ...]
    unlinked_notes: tuple[FrictionNote, ...]


@dataclass(frozen=True)
class PushSubscription:
    """Registered Web Push browser subscription."""

    id: str
    endpoint: str
    p256dh: str
    auth: str
    user_agent: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class PushDelivery:
    """Recorded single-fire Web Push delivery receipt."""

    id: str
    dedupe_key: str
    target_date: date
    source_kind: str
    source_id: str
    stage: str
    sent_at: datetime


@dataclass(frozen=True)
class DueNudge:
    """Pure transition nudge scheduled for immediate Web Push dispatch."""

    dedupe_key: str
    target_date: date
    source_kind: str
    source_id: str
    stage: str
    title: str
    body: str
    tag: str
    url: str = "/?tab=now"

    def to_payload(self) -> dict[str, str]:
        """Serialize push payload expected by Kairos /sw.js."""
        return {
            "title": self.title,
            "body": self.body,
            "tag": self.tag,
            "url": self.url,
            "source_kind": self.source_kind,
            "stage": self.stage,
        }
