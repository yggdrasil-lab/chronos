"""Pure unit tests for Chronos schedule engine and production config validation."""

from datetime import date
from pathlib import Path

import pytest

from app.config import Settings
from app.domain.errors import ScheduleConflictError, ScheduleValidationError
from app.domain.models import (
    Commitment,
    CommitmentSource,
    GridBlock,
    GridException,
    NudgeMode,
    SliceKind,
)
from app.domain.schedule_engine import (
    compute_now_next,
    plan_block_extension,
    resolve_day_schedule,
)

TARGET_MONDAY = date(2026, 11, 23)  # Monday -> weekday() == 0


def _block(
    block_id: int = 1,
    *,
    title: str = "Deep Work",
    day_of_week: int = 0,
    start_minute: int = 540,
    end_minute: int = 660,
    active: bool = True,
) -> GridBlock:
    return GridBlock(
        id=block_id,
        title=title,
        day_of_week=day_of_week,
        start_minute=start_minute,
        end_minute=end_minute,
        category="focus",
        nudge_mode=NudgeMode.BOUNDARY,
        active=active,
    )


def _commitment(
    commitment_id: int,
    *,
    title: str = "Sync",
    target_date: date = TARGET_MONDAY,
    start_minute: int | None = 570,
    end_minute: int | None = 600,
    all_day: bool = False,
    source: CommitmentSource = CommitmentSource.MANUAL,
) -> Commitment:
    return Commitment(
        id=commitment_id,
        title=title,
        target_date=target_date,
        start_minute=start_minute,
        end_minute=end_minute,
        all_day=all_day,
        source=source,
    )


def test_carve_out_splits_grid_block_around_timed_commitment() -> None:
    block = _block(start_minute=540, end_minute=660)  # 09:00-11:00
    meeting = _commitment(10, title="Design Review", start_minute=570, end_minute=630)

    schedule = resolve_day_schedule(TARGET_MONDAY, [block], [], [meeting])

    assert [(s.kind, s.start_minute, s.end_minute, s.suppressed_tail) for s in schedule.slices] == [
        (SliceKind.GRID_BLOCK, 540, 570, False),
        (SliceKind.COMMITMENT, 570, 630, False),
        (SliceKind.GRID_BLOCK, 630, 660, False),
    ]
    assert block.start_minute == 540
    assert block.end_minute == 660


def test_resumed_tail_under_15_minutes_is_suppressed_and_excluded_from_now_next() -> None:
    block = _block(start_minute=540, end_minute=660)  # 09:00-11:00
    # Ends at 10:46 -> 14m tail (suppressed_tail=True)
    meeting = _commitment(11, start_minute=570, end_minute=646)

    schedule = resolve_day_schedule(TARGET_MONDAY, [block], [], [meeting])
    tails = [s for s in schedule.slices if s.kind == SliceKind.GRID_BLOCK and s.start_minute == 646]
    assert len(tails) == 1
    assert tails[0].end_minute == 660
    assert tails[0].suppressed_tail is True

    # At 10:50 (650), suppressed tail is excluded -> Open Time until end of day
    state = compute_now_next(schedule, 650)
    assert state.active_slice is None
    assert state.active_items == ()
    assert state.upcoming == ()
    assert state.open_time_minutes == 1440 - 650

    # At 09:15 (555), suppressed tail does not appear in upcoming
    morning_state = compute_now_next(schedule, 555)
    assert [s.slice_id for s in morning_state.upcoming] == ["commitment:11"]


def test_resumed_tail_of_exactly_15_minutes_is_preserved() -> None:
    block = _block(start_minute=540, end_minute=660)
    meeting = _commitment(12, start_minute=570, end_minute=645)  # 15m resumed tail

    schedule = resolve_day_schedule(TARGET_MONDAY, [block], [], [meeting])
    tails = [s for s in schedule.slices if s.kind == SliceKind.GRID_BLOCK and s.start_minute == 645]
    assert len(tails) == 1
    assert tails[0].suppressed_tail is False

    state = compute_now_next(schedule, 650)
    assert state.active_slice is not None
    assert state.active_slice.start_minute == 645
    assert state.open_time_minutes is None


def test_overlapping_commitments_preserve_both_and_mark_conflict() -> None:
    block = _block(start_minute=540, end_minute=660)
    first = _commitment(1, title="Call A", start_minute=570, end_minute=630)
    second = _commitment(2, title="Call B", start_minute=600, end_minute=645)
    adjacent = _commitment(3, title="Call C", start_minute=645, end_minute=660)

    schedule = resolve_day_schedule(TARGET_MONDAY, [block], [], [first, second, adjacent])

    by_id = {c.id: c for c in schedule.timed_commitments}
    assert set(by_id.keys()) == {1, 2, 3}
    assert by_id[1].has_conflict is True
    assert by_id[2].has_conflict is True
    assert by_id[3].has_conflict is False
    assert schedule.has_conflicts is True

    state = compute_now_next(schedule, 615)
    assert [s.source_id for s in state.active_items] == [1, 2]
    assert all(s.has_conflict for s in state.active_items)


def test_all_day_commitment_does_not_carve_grid_blocks() -> None:
    block = _block(start_minute=540, end_minute=660)
    holiday = _commitment(
        20,
        title="Team Offsite Day",
        start_minute=None,
        end_minute=None,
        all_day=True,
        source=CommitmentSource.IRIS,
    )

    schedule = resolve_day_schedule(TARGET_MONDAY, [block], [], [holiday])

    assert len(schedule.all_day_commitments) == 1
    assert schedule.all_day_commitments[0].title == "Team Offsite Day"
    assert len(schedule.timed_commitments) == 0
    assert len(schedule.slices) == 1
    assert schedule.slices[0].start_minute == 540
    assert schedule.slices[0].end_minute == 660


def test_grid_exceptions_skip_move_and_resize_without_mutating_base_block() -> None:
    skipped_block = _block(1, title="Morning Walk", start_minute=420, end_minute=480)
    moved_block = _block(2, title="Deep Work", start_minute=540, end_minute=660)
    other_day_block = _block(3, title="Tuesday Review", day_of_week=1, start_minute=540, end_minute=600)

    exceptions = [
        GridException(id=10, grid_block_id=1, target_date=TARGET_MONDAY, skipped=True),
        GridException(
            id=11,
            grid_block_id=2,
            target_date=TARGET_MONDAY,
            skipped=False,
            start_minute=600,
            end_minute=750,
            extension_count=2,
            notes="Extended session",
        ),
    ]

    schedule = resolve_day_schedule(
        TARGET_MONDAY, [skipped_block, moved_block, other_day_block], exceptions, []
    )

    assert len(schedule.slices) == 1
    resolved = schedule.slices[0]
    assert resolved.source_id == 2
    assert resolved.start_minute == 600
    assert resolved.end_minute == 750
    assert resolved.extension_count == 2
    assert resolved.notes == "Extended session"
    assert moved_block.start_minute == 540
    assert moved_block.end_minute == 660


def test_open_time_and_quiet_hours_boundaries() -> None:
    block = _block(start_minute=540, end_minute=600)  # 09:00-10:00
    schedule = resolve_day_schedule(TARGET_MONDAY, [block], [], [])

    early_quiet = compute_now_next(schedule, 389)  # 06:29
    assert early_quiet.is_quiet_hours is True
    assert early_quiet.open_time_minutes == 540 - 389

    morning_active = compute_now_next(schedule, 390)  # 06:30
    assert morning_active.is_quiet_hours is False
    assert morning_active.open_time_minutes == 540 - 390

    evening_active = compute_now_next(schedule, 1319)  # 21:59
    assert evening_active.is_quiet_hours is False
    assert evening_active.open_time_minutes == 1440 - 1319

    night_quiet = compute_now_next(schedule, 1320)  # 22:00
    assert night_quiet.is_quiet_hours is True
    assert night_quiet.open_time_minutes == 1440 - 1320


def test_compute_now_next_rejects_out_of_range_minute() -> None:
    schedule = resolve_day_schedule(TARGET_MONDAY, [], [], [])
    with pytest.raises(ScheduleValidationError):
        compute_now_next(schedule, 1441)


def test_production_settings_fail_fast_when_secrets_missing(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="CHRONOS_DB_PASSWORD"):
        Settings(environment="production", db_password="", db_password_file="")

    secret_file = tmp_path / "chronos_db_password"
    secret_file.write_text("s3cr3t-pass\n", encoding="utf-8")

    with pytest.raises(ValueError, match="CHRONOS_VAPID_PRIVATE_KEY"):
        Settings(
            environment="production",
            db_password="",
            db_password_file=str(secret_file),
        )

    vapid_file = tmp_path / "chronos_vapid_private_key"
    vapid_file.write_text("synthetic-vapid-key\n", encoding="utf-8")
    loaded = Settings(
        environment="production",
        db_host="chronos-db",
        db_user="chronos",
        db_name="chronos",
        db_password="",
        db_password_file=str(secret_file),
        vapid_private_key="",
        vapid_private_key_file=str(vapid_file),
        vapid_public_key="synthetic-public-key",
        vapid_subject="mailto:chronos@example.invalid",
    )
    assert loaded.db_password == "s3cr3t-pass"
    assert loaded.vapid_private_key == "synthetic-vapid-key"
    assert loaded.vapid_configured is True
    assert loaded.database_url == "postgresql+psycopg://chronos:s3cr3t-pass@chronos-db:5432/chronos"


def test_plan_block_extension_trims_flexible_and_enforces_2x_cap() -> None:
    deep_work = _block(1, title="Deep Work", start_minute=540, end_minute=660)
    flex_short = GridBlock(
        id=2,
        title="Inbox Triage",
        day_of_week=0,
        start_minute=660,
        end_minute=675,
        category="admin",
        flexible=True,
    )
    flex_later = GridBlock(
        id=3,
        title="Reading",
        day_of_week=0,
        start_minute=675,
        end_minute=720,
        category="learning",
        flexible=True,
    )
    rigid = GridBlock(
        id=4,
        title="Rigid Block",
        day_of_week=0,
        start_minute=660,
        end_minute=720,
        category="focus",
        flexible=False,
    )

    plan = plan_block_extension(
        deep_work,
        TARGET_MONDAY,
        30,
        [deep_work, flex_short, flex_later, rigid],
        [],
    )
    assert plan.extended.end_minute == 690
    assert plan.extended.extension_count == 1
    assert len(plan.trimmed) == 2
    by_block = {item.grid_block_id: item for item in plan.trimmed}
    assert by_block[2].skipped is True
    assert by_block[3].skipped is False
    assert by_block[3].start_minute == 690

    capped = GridException(
        id=1,
        grid_block_id=1,
        target_date=TARGET_MONDAY,
        end_minute=720,
        extension_count=2,
    )
    with pytest.raises(ScheduleConflictError, match="2x"):
        plan_block_extension(deep_work, TARGET_MONDAY, 15, [deep_work], [capped])
