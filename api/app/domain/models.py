"""Pure immutable domain models for Chronos schedule resolution."""

from dataclasses import dataclass
from datetime import date
from enum import Enum


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


class SliceKind(str, Enum):
    """Resolved timeline slice classification."""

    GRID_BLOCK = "grid_block"
    COMMITMENT = "commitment"


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
    notes: str | None = None
    has_conflict: bool = False


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
    has_conflict: bool = False
    suppressed_tail: bool = False
    extension_count: int = 0
    notes: str | None = None


@dataclass(frozen=True)
class DaySchedule:
    """Resolved schedule output for a single calendar date."""

    target_date: date
    slices: tuple[ResolvedSlice, ...]
    all_day_commitments: tuple[Commitment, ...]
    timed_commitments: tuple[Commitment, ...]

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
