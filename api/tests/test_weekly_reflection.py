"""Pure unit tests for weekly friction reflection aggregation."""

from datetime import UTC, date, datetime

from app.domain.models import FrictionNote, FrictionTag, HabitCue
from app.domain.reflection import build_weekly_reflection, current_week_monday

WEEK_START = date(2026, 11, 23)  # Monday


def _note(
    note_id: int,
    *,
    target_date: date = WEEK_START,
    tag: FrictionTag = FrictionTag.TIRED,
    note: str = "Low energy after commute",
    habit_cue_id: int | None = None,
    cue_title: str | None = None,
    cue_zone_chain: str | None = None,
    hour: int = 8,
) -> FrictionNote:
    return FrictionNote(
        id=note_id,
        target_date=target_date,
        tag=tag,
        note=note,
        created_at=datetime(2026, 11, 23, hour, 0, tzinfo=UTC),
        habit_cue_id=habit_cue_id,
        cue_title=cue_title,
        cue_zone_chain=cue_zone_chain,
    )


def test_current_week_monday() -> None:
    assert current_week_monday(date(2026, 11, 23)) == date(2026, 11, 23)
    assert current_week_monday(date(2026, 11, 25)) == date(2026, 11, 23)
    assert current_week_monday(date(2026, 11, 29)) == date(2026, 11, 23)


def test_build_weekly_reflection_empty() -> None:
    result = build_weekly_reflection([], WEEK_START)
    assert result.week_start == date(2026, 11, 23)
    assert result.week_end == date(2026, 11, 29)
    assert result.total_notes == 0
    assert result.by_tag == ()
    assert result.by_cue == ()
    assert result.unlinked_notes == ()


def test_build_weekly_reflection_filters_range_and_groups() -> None:
    cues = [
        HabitCue(id=10, title="Lay out gym kit", zone_chain="bedroom"),
        HabitCue(id=20, title="Fill water bottle", zone_chain="kitchen"),
    ]
    notes = [
        _note(1, target_date=date(2026, 11, 22), tag=FrictionTag.TIRED, habit_cue_id=10),
        _note(
            2,
            target_date=date(2026, 11, 23),
            tag=FrictionTag.TIRED,
            habit_cue_id=10,
            cue_title="Lay out gym kit",
            cue_zone_chain="bedroom",
            hour=7,
        ),
        _note(
            3,
            target_date=date(2026, 11, 25),
            tag=FrictionTag.ENVIRONMENT_MISSING,
            habit_cue_id=10,
            hour=9,
        ),
        _note(
            4,
            target_date=date(2026, 11, 26),
            tag=FrictionTag.TIRED,
            habit_cue_id=20,
            cue_title="Fill water bottle",
            cue_zone_chain="kitchen",
            hour=10,
        ),
        _note(
            5,
            target_date=date(2026, 11, 29),
            tag=FrictionTag.SCHEDULE_OVERRUN,
            habit_cue_id=None,
            hour=19,
        ),
        _note(6, target_date=date(2026, 11, 30), tag=FrictionTag.TRIGGER_MISSED),
    ]

    result = build_weekly_reflection(notes, WEEK_START, cues=cues)

    assert result.week_start == date(2026, 11, 23)
    assert result.week_end == date(2026, 11, 29)
    assert result.total_notes == 4

    assert [g.tag for g in result.by_tag] == [
        FrictionTag.TIRED,
        FrictionTag.ENVIRONMENT_MISSING,
        FrictionTag.SCHEDULE_OVERRUN,
    ]
    assert result.by_tag[0].count == 2
    assert [n.id for n in result.by_tag[0].notes] == [4, 2]

    assert [g.habit_cue_id for g in result.by_cue] == [10, 20]
    assert result.by_cue[0].count == 2
    assert result.by_cue[0].cue_title == "Lay out gym kit"
    assert result.by_cue[0].zone_chain == "bedroom"
    assert [n.id for n in result.by_cue[0].notes] == [3, 2]
    assert result.by_cue[1].count == 1

    assert [n.id for n in result.unlinked_notes] == [5]
