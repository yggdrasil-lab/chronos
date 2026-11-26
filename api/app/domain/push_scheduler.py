"""Pure stateless Web Push transition nudge scheduler for Chronos."""

from collections.abc import Set as AbstractSet

from app.domain.errors import ScheduleValidationError
from app.domain.models import DaySchedule, DueNudge, NudgeMode, ResolvedSlice, SliceKind
from app.domain.schedule_engine import (
    MINUTES_PER_DAY,
    QUIET_HOURS_END_MINUTE,
    QUIET_HOURS_START_MINUTE,
)

SCREENS_OFF_WINDOW_END_MINUTE = QUIET_HOURS_START_MINUTE + 15
BOUNDARY_WINDOW_MINUTES = 15
PRE_END_LEAD_MINUTES = 10


def _format_hhmm(minute_of_day: int) -> str:
    hours, minutes = divmod(minute_of_day, 60)
    return f"{hours:02d}:{minutes:02d}"


def _due_stage_for_slice(item: ResolvedSlice, now_minute: int) -> str | None:
    if item.nudge_mode == NudgeMode.NONE:
        return None
    duration = item.end_minute - item.start_minute
    if (
        item.nudge_mode == NudgeMode.TWO_STAGE
        and duration > PRE_END_LEAD_MINUTES
        and (item.end_minute - PRE_END_LEAD_MINUTES) <= now_minute < item.end_minute
    ):
        return "pre_end_10m"
    if item.end_minute <= now_minute < item.end_minute + BOUNDARY_WINDOW_MINUTES:
        return "boundary"
    return None


def _build_slice_nudge(schedule: DaySchedule, item: ResolvedSlice, stage: str) -> DueNudge:
    source_kind = item.kind.value
    source_id = str(item.source_id)
    date_iso = schedule.target_date.isoformat()
    dedupe_key = f"{date_iso}:{source_kind}:{source_id}:{item.end_minute}:{stage}"
    end_hhmm = _format_hhmm(item.end_minute)
    if stage == "pre_end_10m":
        title = f"{item.title} ends in 10m"
        body = f"Wraps up at {end_hhmm}. Open Now to transition or extend."
    else:
        title = f"{item.title} ended"
        body = f"Transition at {end_hhmm}. Open Now for your next block."
    return DueNudge(
        dedupe_key=dedupe_key,
        target_date=schedule.target_date,
        source_kind=source_kind,
        source_id=source_id,
        stage=stage,
        title=title,
        body=body,
        tag=f"chronos-{source_kind}-{source_id}-{stage}",
        url="/?tab=now",
    )


def evaluate_due_nudges(
    schedule: DaySchedule,
    now_minute: int,
    already_sent_keys: AbstractSet[str],
) -> tuple[DueNudge, ...]:
    """Return deduplicated due transition nudges for the resolved DaySchedule at now_minute."""
    if now_minute < 0 or now_minute > MINUTES_PER_DAY:
        raise ScheduleValidationError(f"now_minute must be within [0, 1440], got {now_minute}.")

    quiet_hours = now_minute >= QUIET_HOURS_START_MINUTE or now_minute < QUIET_HOURS_END_MINUTE
    due: list[DueNudge] = []

    for item in schedule.slices:
        if item.suppressed_tail or item.tentative:
            continue
        if quiet_hours and not (item.kind == SliceKind.COMMITMENT and item.urgent_quiet_bypass):
            continue
        stage = _due_stage_for_slice(item, now_minute)
        if stage is None:
            continue
        nudge = _build_slice_nudge(schedule, item, stage)
        if nudge.dedupe_key not in already_sent_keys:
            due.append(nudge)

    if QUIET_HOURS_START_MINUTE <= now_minute < SCREENS_OFF_WINDOW_END_MINUTE:
        screens_off_key = f"{schedule.target_date.isoformat()}:quiet_hours:screens_off:screens_off"
        if screens_off_key not in already_sent_keys:
            due.append(
                DueNudge(
                    dedupe_key=screens_off_key,
                    target_date=schedule.target_date,
                    source_kind="quiet_hours",
                    source_id="screens_off",
                    stage="screens_off",
                    title="Screens Off - Quiet Hours",
                    body="It is 22:00. Wind down for sleep; non-urgent nudges are paused until 06:30.",
                    tag="chronos-quiet-hours-screens-off",
                    url="/?tab=now",
                )
            )

    return tuple(due)
