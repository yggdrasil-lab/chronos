"""Application service layer for habit cues, friction notes, and weekly reflections."""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.domain.errors import EntityNotFoundError, ScheduleValidationError
from app.domain.models import FrictionNote, FrictionTag, HabitCue, WeeklyReflection
from app.domain.reflection import build_weekly_reflection, current_week_monday
from app.repositories.protocols import HabitCueRepository

FRICTION_NOTE_MAX_LENGTH = 280
MAX_FRICTION_QUERY_LIMIT = 500


def _require_non_empty(value: str, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ScheduleValidationError(f"{field_name} must not be empty.")
    return cleaned


def _validate_positive_id(value: int, field_name: str) -> int:
    if value <= 0:
        raise ScheduleValidationError(f"{field_name} must be a positive integer, got {value}.")
    return value


class CueService:
    """Use-case coordinator for habit cue catalog, block anchoring, and friction reflection."""

    def __init__(self, repo: HabitCueRepository, timezone_name: str = "Pacific/Auckland") -> None:
        self._repo = repo
        self._tz = ZoneInfo(timezone_name)

    def _validate_grid_block_ids(self, grid_block_ids: Sequence[int]) -> list[int]:
        unique_ids = sorted(dict.fromkeys(grid_block_ids))
        for block_id in unique_ids:
            _validate_positive_id(block_id, "grid_block_ids")
        missing = self._repo.find_missing_grid_block_ids(unique_ids)
        if missing:
            raise EntityNotFoundError(f"GridBlock(s) not found: {missing}.")
        return unique_ids

    def list_cues(
        self,
        *,
        active_only: bool = False,
        grid_block_id: int | None = None,
        zone_chain: str | None = None,
    ) -> list[HabitCue]:
        if grid_block_id is not None:
            _validate_positive_id(grid_block_id, "grid_block_id")
        clean_zone = _require_non_empty(zone_chain, "zone_chain") if zone_chain is not None else None
        return self._repo.list_cues(
            active_only=active_only,
            grid_block_id=grid_block_id,
            zone_chain=clean_zone,
        )

    def create_cue(
        self,
        *,
        title: str,
        zone_chain: str,
        chain_order: int = 0,
        passive_rule: str | None = None,
        active: bool = True,
        notes: str | None = None,
        grid_block_ids: Sequence[int] = (),
    ) -> HabitCue:
        clean_title = _require_non_empty(title, "title")
        clean_zone = _require_non_empty(zone_chain, "zone_chain")
        if chain_order < 0:
            raise ScheduleValidationError("chain_order must be >= 0.")
        validated_blocks = self._validate_grid_block_ids(grid_block_ids)
        return self._repo.create_cue(
            title=clean_title,
            zone_chain=clean_zone,
            chain_order=chain_order,
            passive_rule=passive_rule,
            active=active,
            notes=notes,
            grid_block_ids=validated_blocks,
        )

    def update_cue(
        self,
        cue_id: int,
        *,
        title: str | None = None,
        zone_chain: str | None = None,
        chain_order: int | None = None,
        passive_rule: str | None = None,
        passive_rule_provided: bool = False,
        active: bool | None = None,
        notes: str | None = None,
        notes_provided: bool = False,
        grid_block_ids: Sequence[int] | None = None,
    ) -> HabitCue:
        existing = self._repo.get_cue(cue_id)
        if existing is None:
            raise EntityNotFoundError(f"HabitCue {cue_id} not found.")

        next_title = _require_non_empty(title, "title") if title is not None else existing.title
        next_zone = (
            _require_non_empty(zone_chain, "zone_chain")
            if zone_chain is not None
            else existing.zone_chain
        )
        next_order = chain_order if chain_order is not None else existing.chain_order
        if next_order < 0:
            raise ScheduleValidationError("chain_order must be >= 0.")
        next_blocks = (
            self._validate_grid_block_ids(grid_block_ids) if grid_block_ids is not None else None
        )

        updated = self._repo.update_cue(
            cue_id,
            title=next_title,
            zone_chain=next_zone,
            chain_order=next_order,
            passive_rule=passive_rule if passive_rule_provided else existing.passive_rule,
            active=active if active is not None else existing.active,
            notes=notes if notes_provided else existing.notes,
            grid_block_ids=next_blocks,
        )
        if updated is None:
            raise EntityNotFoundError(f"HabitCue {cue_id} not found.")
        return updated

    def delete_cue(self, cue_id: int) -> None:
        if not self._repo.delete_cue(cue_id):
            raise EntityNotFoundError(f"HabitCue {cue_id} not found.")

    def list_friction_notes(
        self,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        habit_cue_id: int | None = None,
        grid_block_id: int | None = None,
        limit: int = 50,
    ) -> list[FrictionNote]:
        if start_date is not None and end_date is not None and start_date > end_date:
            raise ScheduleValidationError("start_date must be <= end_date.")
        if habit_cue_id is not None:
            _validate_positive_id(habit_cue_id, "habit_cue_id")
        if grid_block_id is not None:
            _validate_positive_id(grid_block_id, "grid_block_id")
        if limit < 1 or limit > MAX_FRICTION_QUERY_LIMIT:
            raise ScheduleValidationError(
                f"limit must be in [1, {MAX_FRICTION_QUERY_LIMIT}], got {limit}."
            )
        return self._repo.list_friction_notes(
            start_date=start_date,
            end_date=end_date,
            habit_cue_id=habit_cue_id,
            grid_block_id=grid_block_id,
            limit=limit,
        )

    def create_friction_note(
        self,
        *,
        target_date: date,
        tag: FrictionTag,
        note: str,
        habit_cue_id: int | None = None,
        grid_block_id: int | None = None,
    ) -> FrictionNote:
        clean_note = _require_non_empty(note, "note")
        if len(clean_note) > FRICTION_NOTE_MAX_LENGTH:
            raise ScheduleValidationError(
                f"note must be <= {FRICTION_NOTE_MAX_LENGTH} characters after stripping."
            )
        if habit_cue_id is not None:
            _validate_positive_id(habit_cue_id, "habit_cue_id")
            if self._repo.get_cue(habit_cue_id) is None:
                raise EntityNotFoundError(f"HabitCue {habit_cue_id} not found.")
        if grid_block_id is not None:
            _validate_positive_id(grid_block_id, "grid_block_id")
            if self._repo.find_missing_grid_block_ids([grid_block_id]):
                raise EntityNotFoundError(f"GridBlock {grid_block_id} not found.")
        return self._repo.create_friction_note(
            target_date=target_date,
            tag=tag,
            note=clean_note,
            habit_cue_id=habit_cue_id,
            grid_block_id=grid_block_id,
        )

    def delete_friction_note(self, note_id: int) -> None:
        if not self._repo.delete_friction_note(note_id):
            raise EntityNotFoundError(f"FrictionNote {note_id} not found.")

    def get_weekly_reflection(self, *, week_start: date | None = None) -> WeeklyReflection:
        effective_start = week_start
        if effective_start is None:
            effective_start = current_week_monday(datetime.now(self._tz).date())
        effective_end = effective_start + timedelta(days=6)
        notes = self._repo.list_friction_notes(
            start_date=effective_start,
            end_date=effective_end,
            limit=None,
        )
        return build_weekly_reflection(notes, effective_start)
