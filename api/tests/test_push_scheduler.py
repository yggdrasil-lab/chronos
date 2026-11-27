"""Pure unit tests for Chronos Web Push transition nudge scheduler."""

from datetime import date

import pytest

from app.domain.errors import ScheduleValidationError
from app.domain.models import (
    Commitment,
    CommitmentSource,
    GridBlock,
    NudgeMode,
    TravelOverlay,
    TravelOverlayMode,
)
from app.domain.push_scheduler import evaluate_due_nudges
from app.domain.schedule_engine import resolve_day_schedule

TARGET_MONDAY = date(2026, 11, 23)


def _block(
    block_id: int,
    *,
    title: str = "Deep Work",
    start_minute: int = 540,
    end_minute: int = 660,
    nudge_mode: NudgeMode = NudgeMode.TWO_STAGE,
) -> GridBlock:
    return GridBlock(
        id=block_id,
        title=title,
        day_of_week=0,
        start_minute=start_minute,
        end_minute=end_minute,
        category="focus",
        nudge_mode=nudge_mode,
    )


def _commitment(
    commitment_id: int,
    *,
    title: str = "Doctor Visit",
    start_minute: int | None = 600,
    end_minute: int | None = 660,
    all_day: bool = False,
    nudge_mode: NudgeMode = NudgeMode.TWO_STAGE,
    urgent_quiet_bypass: bool = False,
    tentative: bool = False,
) -> Commitment:
    return Commitment(
        id=commitment_id,
        title=title,
        target_date=TARGET_MONDAY,
        start_minute=start_minute,
        end_minute=end_minute,
        all_day=all_day,
        source=CommitmentSource.MANUAL,
        nudge_mode=nudge_mode,
        urgent_quiet_bypass=urgent_quiet_bypass,
        tentative=tentative,
    )


def test_two_stage_and_boundary_nudge_windows_and_deduplication() -> None:
    two_stage = _block(1, title="Deep Work", start_minute=540, end_minute=660, nudge_mode=NudgeMode.TWO_STAGE)
    boundary_only = _block(2, title="Lunch Walk", start_minute=720, end_minute=750, nudge_mode=NudgeMode.BOUNDARY)
    silent = _block(3, title="Silent Reading", start_minute=780, end_minute=840, nudge_mode=NudgeMode.NONE)
    short_two_stage = _block(4, title="Quick Check", start_minute=840, end_minute=850, nudge_mode=NudgeMode.TWO_STAGE)

    schedule = resolve_day_schedule(
        TARGET_MONDAY, [two_stage, boundary_only, silent, short_two_stage], [], []
    )

    assert evaluate_due_nudges(schedule, 649, set()) == ()

    pre = evaluate_due_nudges(schedule, 650, set())
    assert len(pre) == 1
    assert pre[0].stage == "pre_end_10m"
    assert pre[0].source_kind == "grid_block"
    assert pre[0].to_payload()["url"] == "/?tab=now"

    # Single-fire deduplication skips already sent key
    assert evaluate_due_nudges(schedule, 655, {pre[0].dedupe_key}) == ()

    # At boundary [660, 675), boundary fires once
    boundary = evaluate_due_nudges(schedule, 660, {pre[0].dedupe_key})
    assert len(boundary) == 1
    assert boundary[0].stage == "boundary"
    assert evaluate_due_nudges(schedule, 675, set()) == ()

    # Boundary-only block does not emit pre_end_10m
    assert evaluate_due_nudges(schedule, 745, set()) == ()
    assert [n.stage for n in evaluate_due_nudges(schedule, 750, set())] == ["boundary"]

    # Silent block never emits
    assert evaluate_due_nudges(schedule, 840, set()) == ()

    # 10m block skips pre_end_10m because duration <= 10m, only fires boundary at 850
    assert evaluate_due_nudges(schedule, 845, set()) == ()
    assert [n.stage for n in evaluate_due_nudges(schedule, 850, set())] == ["boundary"]


def test_quiet_hours_screens_off_and_urgent_bypass() -> None:
    evening_block = _block(1, title="Wind Down", start_minute=1260, end_minute=1320, nudge_mode=NudgeMode.TWO_STAGE)
    regular_late = _commitment(
        10, title="Late Call", start_minute=1320, end_minute=1350, nudge_mode=NudgeMode.TWO_STAGE
    )
    urgent_late = _commitment(
        11,
        title="On-Call Page",
        start_minute=1320,
        end_minute=1360,
        nudge_mode=NudgeMode.TWO_STAGE,
        urgent_quiet_bypass=True,
    )
    all_day = _commitment(
        12,
        title="All Day Note",
        start_minute=None,
        end_minute=None,
        all_day=True,
        nudge_mode=NudgeMode.TWO_STAGE,
        urgent_quiet_bypass=True,
    )
    schedule = resolve_day_schedule(
        TARGET_MONDAY, [evening_block], [], [regular_late, urgent_late, all_day]
    )

    # At 22:00 (1320), evening_block boundary is suppressed by Quiet Hours, screens_off fires
    at_2200 = evaluate_due_nudges(schedule, 1320, set())
    assert [(n.source_kind, n.stage) for n in at_2200] == [("quiet_hours", "screens_off")]

    # At 22:30 (1350): regular_late boundary suppressed, urgent_late pre_end_10m allowed
    at_2230 = evaluate_due_nudges(schedule, 1350, set())
    assert [(n.source_kind, n.source_id, n.stage) for n in at_2230] == [
        ("commitment", "11", "pre_end_10m")
    ]

    # Early morning quiet hours (06:00 = 360) suppresses non-bypass commitments
    early = _commitment(13, title="Early Prep", start_minute=330, end_minute=360)
    early_schedule = resolve_day_schedule(TARGET_MONDAY, [], [], [early])
    assert evaluate_due_nudges(early_schedule, 360, set()) == ()


def test_suppressed_tail_skipped_and_invalid_minute_rejected() -> None:
    block = _block(1, start_minute=540, end_minute=600, nudge_mode=NudgeMode.BOUNDARY)
    meeting = _commitment(2, start_minute=540, end_minute=588, nudge_mode=NudgeMode.NONE)
    schedule = resolve_day_schedule(TARGET_MONDAY, [block], [], [meeting])

    # Remaining 12m tail [588, 600) is suppressed_tail=True -> no nudge at 600
    assert evaluate_due_nudges(schedule, 600, set()) == ()

    with pytest.raises(ScheduleValidationError):
        evaluate_due_nudges(schedule, 1441, set())


def test_tentative_commitment_never_triggers_push_nudges_until_confirmed() -> None:
    tentative = _commitment(
        20,
        title="Dentist Checkup",
        start_minute=600,
        end_minute=660,
        nudge_mode=NudgeMode.TWO_STAGE,
        urgent_quiet_bypass=True,
        tentative=True,
    )
    confirmed = _commitment(
        20,
        title="Dentist Checkup",
        start_minute=600,
        end_minute=660,
        nudge_mode=NudgeMode.TWO_STAGE,
        urgent_quiet_bypass=True,
        tentative=False,
    )

    tentative_schedule = resolve_day_schedule(TARGET_MONDAY, [], [], [tentative])
    assert evaluate_due_nudges(tentative_schedule, 650, set()) == ()
    assert evaluate_due_nudges(tentative_schedule, 660, set()) == ()

    confirmed_schedule = resolve_day_schedule(TARGET_MONDAY, [], [], [confirmed])
    assert [n.stage for n in evaluate_due_nudges(confirmed_schedule, 650, set())] == ["pre_end_10m"]
    assert [n.stage for n in evaluate_due_nudges(confirmed_schedule, 660, set())] == ["boundary"]


def test_travel_overlay_suppresses_regular_nudges_and_screens_off_but_keeps_urgent_bypass() -> None:
    work_block = _block(
        1,
        title="Deep Work",
        start_minute=540,
        end_minute=600,
        nudge_mode=NudgeMode.TWO_STAGE,
    )
    regular_travel = _commitment(
        30,
        title="Museum Entry",
        start_minute=540,
        end_minute=600,
        nudge_mode=NudgeMode.TWO_STAGE,
        urgent_quiet_bypass=False,
    )
    urgent_flight = _commitment(
        31,
        title="Airport Check-in",
        start_minute=1300,
        end_minute=1330,
        nudge_mode=NudgeMode.TWO_STAGE,
        urgent_quiet_bypass=True,
    )
    overlay = TravelOverlay(
        id=1,
        external_ref="odysseus:tokyo-2027",
        label="Tokyo Trip",
        start_date=TARGET_MONDAY,
        end_date=TARGET_MONDAY,
        mode=TravelOverlayMode.KEEP_WORK_BLOCKS,
    )
    schedule = resolve_day_schedule(
        TARGET_MONDAY,
        [work_block],
        [],
        [regular_travel, urgent_flight],
        travel_overlays=[overlay],
    )

    # Regular work block + non-urgent commitment suppressed even during daytime
    assert evaluate_due_nudges(schedule, 590, set()) == ()
    assert evaluate_due_nudges(schedule, 600, set()) == ()

    # At 22:00 (1320): screens_off is suppressed during travel, urgent_quiet_bypass still fires
    at_2200 = evaluate_due_nudges(schedule, 1320, set())
    assert [(n.source_kind, n.source_id, n.stage) for n in at_2200] == [
        ("commitment", "31", "pre_end_10m")
    ]
