"""Pure domain helpers for weekly friction reflection aggregation."""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, timedelta

from app.domain.models import (
    CueReflectionGroup,
    FrictionNote,
    FrictionTag,
    HabitCue,
    TagReflectionGroup,
    WeeklyReflection,
)


def current_week_monday(reference_date: date) -> date:
    """Return the Monday of the ISO week containing reference_date."""
    return reference_date - timedelta(days=reference_date.weekday())


def _sort_notes_desc(notes: Sequence[FrictionNote]) -> list[FrictionNote]:
    return sorted(notes, key=lambda n: (n.target_date, n.created_at, n.id), reverse=True)


def _group_by_tag(notes: Sequence[FrictionNote]) -> tuple[TagReflectionGroup, ...]:
    buckets: dict[FrictionTag, list[FrictionNote]] = defaultdict(list)
    for note in notes:
        buckets[note.tag].append(note)

    groups = [
        TagReflectionGroup(
            tag=tag,
            count=len(items),
            notes=tuple(_sort_notes_desc(items)),
        )
        for tag, items in buckets.items()
    ]
    groups.sort(key=lambda g: (-g.count, g.tag.value))
    return tuple(groups)


def _group_by_cue(
    notes: Sequence[FrictionNote], cue_map: Mapping[int, HabitCue]
) -> tuple[CueReflectionGroup, ...]:
    buckets: dict[int, list[FrictionNote]] = defaultdict(list)
    for note in notes:
        if note.habit_cue_id is not None:
            buckets[note.habit_cue_id].append(note)

    groups: list[CueReflectionGroup] = []
    for cue_id, items in buckets.items():
        ordered = _sort_notes_desc(items)
        cue = cue_map.get(cue_id)
        cue_title = ordered[0].cue_title or (cue.title if cue is not None else f"Cue #{cue_id}")
        zone_chain = ordered[0].cue_zone_chain or (cue.zone_chain if cue is not None else "")
        groups.append(
            CueReflectionGroup(
                habit_cue_id=cue_id,
                cue_title=cue_title,
                zone_chain=zone_chain,
                count=len(ordered),
                notes=tuple(ordered),
            )
        )
    groups.sort(key=lambda g: (-g.count, g.zone_chain, g.cue_title.lower(), g.habit_cue_id))
    return tuple(groups)


def build_weekly_reflection(
    notes: Sequence[FrictionNote],
    week_start: date,
    *,
    cues: Sequence[HabitCue] | None = None,
) -> WeeklyReflection:
    """Group 7 days of friction notes (week_start..week_start+6d) by tag, cue, and unlinked."""
    week_end = week_start + timedelta(days=6)
    in_range = _sort_notes_desc(
        [item for item in notes if week_start <= item.target_date <= week_end]
    )
    cue_map: dict[int, HabitCue] = {cue.id: cue for cue in cues} if cues else {}
    unlinked = tuple(item for item in in_range if item.habit_cue_id is None)

    return WeeklyReflection(
        week_start=week_start,
        week_end=week_end,
        total_notes=len(in_range),
        by_tag=_group_by_tag(in_range),
        by_cue=_group_by_cue(in_range, cue_map),
        unlinked_notes=unlinked,
    )
