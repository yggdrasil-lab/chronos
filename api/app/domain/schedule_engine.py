"""Pure stateless schedule resolution engine for Chronos."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import date

from app.domain.errors import ScheduleValidationError
from app.domain.models import (
    Commitment,
    DaySchedule,
    GridBlock,
    GridException,
    NowNextState,
    ResolvedSlice,
    SliceKind,
)

MINUTES_PER_DAY = 1440
MIN_RESUMED_TAIL_MINUTES = 15
QUIET_HOURS_START_MINUTE = 22 * 60
QUIET_HOURS_END_MINUTE = 6 * 60 + 30


def _validate_minute_range(start_minute: int, end_minute: int, context: str) -> None:
    if start_minute < 0 or end_minute > MINUTES_PER_DAY or start_minute >= end_minute:
        raise ScheduleValidationError(
            f"Invalid minute range [{start_minute}, {end_minute}) for {context}."
        )


def _merge_intervals(intervals: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged: list[tuple[int, int]] = [ordered[0]]
    for start, end in ordered[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def _detect_commitment_conflicts(commitments: Sequence[Commitment]) -> list[Commitment]:
    ordered = sorted(commitments, key=lambda c: (c.start_minute or 0, c.end_minute or 0, c.id))
    flagged: list[Commitment] = []
    for idx, current in enumerate(ordered):
        if current.start_minute is None or current.end_minute is None:
            raise ScheduleValidationError(f"Timed commitment {current.id} missing start/end minutes.")
        _validate_minute_range(current.start_minute, current.end_minute, f"commitment:{current.id}")
        overlaps = any(
            other_idx != idx
            and (other.start_minute or 0) < current.end_minute
            and current.start_minute < (other.end_minute or 0)
            for other_idx, other in enumerate(ordered)
        )
        flagged.append(replace(current, has_conflict=overlaps))
    return flagged


def _carve_block(
    block: GridBlock,
    effective_start: int,
    effective_end: int,
    extension_count: int,
    notes: str | None,
    timed_commitments: Sequence[Commitment],
) -> list[ResolvedSlice]:
    busy = [
        (max(effective_start, item.start_minute or 0), min(effective_end, item.end_minute or 0))
        for item in timed_commitments
        if (item.start_minute or 0) < effective_end and effective_start < (item.end_minute or 0)
    ]
    merged_busy = _merge_intervals(busy)
    segments: list[tuple[int, int]] = []
    cursor = effective_start
    for busy_start, busy_end in merged_busy:
        if cursor < busy_start:
            segments.append((cursor, busy_start))
        cursor = max(cursor, busy_end)
    if cursor < effective_end:
        segments.append((cursor, effective_end))

    return [
        ResolvedSlice(
            slice_id=f"block:{block.id}:{seg_start}",
            kind=SliceKind.GRID_BLOCK,
            source_id=block.id,
            title=block.title,
            start_minute=seg_start,
            end_minute=seg_end,
            category=block.category,
            nudge_mode=block.nudge_mode,
            suppressed_tail=(seg_start > effective_start) and ((seg_end - seg_start) < MIN_RESUMED_TAIL_MINUTES),
            extension_count=extension_count,
            notes=notes if notes is not None else block.notes,
        )
        for seg_start, seg_end in segments
    ]


def _resolve_effective_blocks(
    target_date: date,
    grid_blocks: Sequence[GridBlock],
    exceptions_by_block: dict[int, GridException],
) -> list[tuple[GridBlock, int, int, int, str | None]]:
    weekday = target_date.weekday()
    resolved: list[tuple[GridBlock, int, int, int, str | None]] = []
    for block in grid_blocks:
        if not block.active or block.day_of_week != weekday:
            continue
        exc = exceptions_by_block.get(block.id)
        if exc and exc.skipped:
            continue
        eff_start = exc.start_minute if exc and exc.start_minute is not None else block.start_minute
        eff_end = exc.end_minute if exc and exc.end_minute is not None else block.end_minute
        _validate_minute_range(eff_start, eff_end, f"grid_block:{block.id}")
        ext_count = exc.extension_count if exc else 0
        notes = exc.notes if exc and exc.notes is not None else block.notes
        resolved.append((block, eff_start, eff_end, ext_count, notes))
    return resolved


def resolve_day_schedule(
    target_date: date,
    grid_blocks: Sequence[GridBlock],
    exceptions: Sequence[GridException],
    commitments: Sequence[Commitment],
) -> DaySchedule:
    """Resolve weekly grid blocks, per-date exceptions, and commitments into a DaySchedule."""
    exceptions_by_block = {
        exc.grid_block_id: exc for exc in exceptions if exc.target_date == target_date
    }
    day_commitments = [item for item in commitments if item.target_date == target_date]
    all_day = tuple(sorted((c for c in day_commitments if c.all_day), key=lambda c: (c.title, c.id)))
    timed = _detect_commitment_conflicts([c for c in day_commitments if not c.all_day])

    slices: list[ResolvedSlice] = []
    for block, eff_start, eff_end, ext_count, notes in _resolve_effective_blocks(
        target_date, grid_blocks, exceptions_by_block
    ):
        slices.extend(_carve_block(block, eff_start, eff_end, ext_count, notes, timed))

    for item in timed:
        slices.append(
            ResolvedSlice(
                slice_id=f"commitment:{item.id}",
                kind=SliceKind.COMMITMENT,
                source_id=item.id,
                title=item.title,
                start_minute=item.start_minute or 0,
                end_minute=item.end_minute or 0,
                source=item.source,
                nudge_mode=item.nudge_mode,
                has_conflict=item.has_conflict,
                notes=item.notes,
            )
        )

    ordered_slices = tuple(
        sorted(slices, key=lambda s: (s.start_minute, s.end_minute, s.kind.value, s.source_id))
    )
    return DaySchedule(
        target_date=target_date,
        slices=ordered_slices,
        all_day_commitments=all_day,
        timed_commitments=tuple(timed),
    )


def compute_now_next(schedule: DaySchedule, now_minute: int) -> NowNextState:
    """Compute active items, upcoming slices, open time, and quiet-hours flag."""
    if now_minute < 0 or now_minute > MINUTES_PER_DAY:
        raise ScheduleValidationError(f"now_minute must be within [0, 1440], got {now_minute}.")

    is_quiet_hours = (
        now_minute >= QUIET_HOURS_START_MINUTE or now_minute < QUIET_HOURS_END_MINUTE
    )
    visible = [item for item in schedule.slices if not item.suppressed_tail]
    active_items = tuple(
        sorted(
            (item for item in visible if item.start_minute <= now_minute < item.end_minute),
            key=lambda s: (0 if s.kind == SliceKind.COMMITMENT else 1, s.start_minute, s.end_minute, s.source_id),
        )
    )
    upcoming = tuple(
        sorted(
            (item for item in visible if item.start_minute > now_minute),
            key=lambda s: (s.start_minute, s.end_minute, s.source_id),
        )
    )
    active_slice = active_items[0] if active_items else None
    if active_items:
        open_time_minutes: int | None = None
    elif upcoming:
        open_time_minutes = upcoming[0].start_minute - now_minute
    else:
        open_time_minutes = MINUTES_PER_DAY - now_minute

    return NowNextState(
        target_date=schedule.target_date,
        now_minute=now_minute,
        active_slice=active_slice,
        active_items=active_items,
        upcoming=upcoming,
        open_time_minutes=open_time_minutes,
        is_quiet_hours=is_quiet_hours,
    )
