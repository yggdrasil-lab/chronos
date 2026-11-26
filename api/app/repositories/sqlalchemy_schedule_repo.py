"""SQLAlchemy implementation of the Chronos ScheduleRepository protocol."""

from collections import defaultdict
from collections.abc import Sequence
from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.db.orm_models import (
    CommitmentORM,
    FrictionNoteORM,
    GridBlockCueORM,
    GridBlockORM,
    GridExceptionORM,
    HabitCueORM,
)
from app.domain.models import (
    Commitment,
    CommitmentSource,
    FrictionNote,
    FrictionTag,
    GridBlock,
    GridException,
    GridExceptionDraft,
    HabitCue,
    NudgeMode,
)


def _to_grid_block(row: GridBlockORM) -> GridBlock:
    return GridBlock(
        id=row.id,
        title=row.title,
        day_of_week=row.day_of_week,
        start_minute=row.start_minute,
        end_minute=row.end_minute,
        category=row.category,
        nudge_mode=NudgeMode(row.nudge_mode),
        flexible=row.flexible,
        active=row.active,
        notes=row.notes,
    )


def _to_grid_exception(row: GridExceptionORM) -> GridException:
    return GridException(
        id=row.id,
        grid_block_id=row.grid_block_id,
        target_date=row.target_date,
        skipped=row.skipped,
        start_minute=row.start_minute,
        end_minute=row.end_minute,
        extension_count=row.extension_count,
        notes=row.notes,
    )


def _to_commitment(row: CommitmentORM) -> Commitment:
    return Commitment(
        id=row.id,
        title=row.title,
        target_date=row.target_date,
        start_minute=row.start_minute,
        end_minute=row.end_minute,
        all_day=row.all_day,
        source=CommitmentSource(row.source),
        external_ref=row.external_ref,
        nudge_mode=NudgeMode(row.nudge_mode),
        urgent_quiet_bypass=row.urgent_quiet_bypass,
        tentative=row.tentative,
        notes=row.notes,
    )


class SqlAlchemyScheduleRepository:
    """SQLAlchemy-backed repository translating ORM entities into frozen domain objects."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def list_grid_blocks(
        self, *, day_of_week: int | None = None, active_only: bool = False
    ) -> list[GridBlock]:
        stmt = select(GridBlockORM).order_by(
            GridBlockORM.day_of_week, GridBlockORM.start_minute, GridBlockORM.id
        )
        if day_of_week is not None:
            stmt = stmt.where(GridBlockORM.day_of_week == day_of_week)
        if active_only:
            stmt = stmt.where(GridBlockORM.active.is_(True))
        return [_to_grid_block(row) for row in self._db.scalars(stmt).all()]

    def get_grid_block(self, block_id: int) -> GridBlock | None:
        row = self._db.get(GridBlockORM, block_id)
        return _to_grid_block(row) if row is not None else None

    def create_grid_block(
        self,
        *,
        title: str,
        day_of_week: int,
        start_minute: int,
        end_minute: int,
        category: str,
        nudge_mode: NudgeMode,
        flexible: bool,
        active: bool,
        notes: str | None,
    ) -> GridBlock:
        row = GridBlockORM(
            title=title,
            day_of_week=day_of_week,
            start_minute=start_minute,
            end_minute=end_minute,
            category=category,
            nudge_mode=nudge_mode.value,
            flexible=flexible,
            active=active,
            notes=notes,
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return _to_grid_block(row)

    def update_grid_block(
        self,
        block_id: int,
        *,
        title: str,
        day_of_week: int,
        start_minute: int,
        end_minute: int,
        category: str,
        nudge_mode: NudgeMode,
        flexible: bool,
        active: bool,
        notes: str | None,
    ) -> GridBlock | None:
        row = self._db.get(GridBlockORM, block_id)
        if row is None:
            return None
        row.title = title
        row.day_of_week = day_of_week
        row.start_minute = start_minute
        row.end_minute = end_minute
        row.category = category
        row.nudge_mode = nudge_mode.value
        row.flexible = flexible
        row.active = active
        row.notes = notes
        self._db.commit()
        self._db.refresh(row)
        return _to_grid_block(row)

    def delete_grid_block(self, block_id: int) -> bool:
        row = self._db.get(GridBlockORM, block_id)
        if row is None:
            return False
        self._db.delete(row)
        self._db.commit()
        return True

    def list_exceptions_for_date(self, target_date: date) -> list[GridException]:
        stmt = (
            select(GridExceptionORM)
            .where(GridExceptionORM.target_date == target_date)
            .order_by(GridExceptionORM.grid_block_id, GridExceptionORM.id)
        )
        return [_to_grid_exception(row) for row in self._db.scalars(stmt).all()]

    def get_exception(self, exception_id: int) -> GridException | None:
        row = self._db.get(GridExceptionORM, exception_id)
        return _to_grid_exception(row) if row is not None else None

    def upsert_exception(
        self,
        *,
        grid_block_id: int,
        target_date: date,
        skipped: bool,
        start_minute: int | None,
        end_minute: int | None,
        extension_count: int,
        notes: str | None,
    ) -> GridException:
        row = self._stage_exception_upsert(
            grid_block_id=grid_block_id,
            target_date=target_date,
            skipped=skipped,
            start_minute=start_minute,
            end_minute=end_minute,
            extension_count=extension_count,
            notes=notes,
        )
        self._db.commit()
        self._db.refresh(row)
        return _to_grid_exception(row)

    def upsert_exceptions_batch(self, drafts: Sequence[GridExceptionDraft]) -> list[GridException]:
        rows = [
            self._stage_exception_upsert(
                grid_block_id=draft.grid_block_id,
                target_date=draft.target_date,
                skipped=draft.skipped,
                start_minute=draft.start_minute,
                end_minute=draft.end_minute,
                extension_count=draft.extension_count,
                notes=draft.notes,
            )
            for draft in drafts
        ]
        self._db.commit()
        for row in rows:
            self._db.refresh(row)
        return [_to_grid_exception(row) for row in rows]

    def _stage_exception_upsert(
        self,
        *,
        grid_block_id: int,
        target_date: date,
        skipped: bool,
        start_minute: int | None,
        end_minute: int | None,
        extension_count: int,
        notes: str | None,
    ) -> GridExceptionORM:
        stmt = select(GridExceptionORM).where(
            GridExceptionORM.grid_block_id == grid_block_id,
            GridExceptionORM.target_date == target_date,
        )
        row = self._db.scalar(stmt)
        if row is None:
            row = GridExceptionORM(
                grid_block_id=grid_block_id,
                target_date=target_date,
                skipped=skipped,
                start_minute=start_minute,
                end_minute=end_minute,
                extension_count=extension_count,
                notes=notes,
            )
            self._db.add(row)
            return row
        row.skipped = skipped
        row.start_minute = start_minute
        row.end_minute = end_minute
        row.extension_count = extension_count
        row.notes = notes
        return row

    def delete_exception(self, exception_id: int) -> bool:
        row = self._db.get(GridExceptionORM, exception_id)
        if row is None:
            return False
        self._db.delete(row)
        self._db.commit()
        return True

    def list_commitments(
        self, *, start_date: date | None = None, end_date: date | None = None
    ) -> list[Commitment]:
        stmt = select(CommitmentORM).order_by(
            CommitmentORM.target_date,
            CommitmentORM.all_day.desc(),
            CommitmentORM.start_minute,
            CommitmentORM.id,
        )
        if start_date is not None:
            stmt = stmt.where(CommitmentORM.target_date >= start_date)
        if end_date is not None:
            stmt = stmt.where(CommitmentORM.target_date <= end_date)
        return [_to_commitment(row) for row in self._db.scalars(stmt).all()]

    def get_commitment(self, commitment_id: int) -> Commitment | None:
        row = self._db.get(CommitmentORM, commitment_id)
        return _to_commitment(row) if row is not None else None

    def get_commitment_by_source_external_ref(
        self, source: CommitmentSource, external_ref: str
    ) -> Commitment | None:
        stmt = select(CommitmentORM).where(
            CommitmentORM.source == source.value,
            CommitmentORM.external_ref == external_ref,
        )
        row = self._db.scalar(stmt)
        return _to_commitment(row) if row is not None else None

    def create_commitment(
        self,
        *,
        title: str,
        target_date: date,
        start_minute: int | None,
        end_minute: int | None,
        all_day: bool,
        source: CommitmentSource,
        external_ref: str | None,
        nudge_mode: NudgeMode,
        urgent_quiet_bypass: bool,
        tentative: bool = False,
        notes: str | None,
    ) -> Commitment:
        row = CommitmentORM(
            title=title,
            target_date=target_date,
            start_minute=start_minute,
            end_minute=end_minute,
            all_day=all_day,
            source=source.value,
            external_ref=external_ref,
            nudge_mode=nudge_mode.value,
            urgent_quiet_bypass=urgent_quiet_bypass,
            tentative=tentative,
            notes=notes,
        )
        self._db.add(row)
        try:
            self._db.commit()
        except IntegrityError:
            self._db.rollback()
            raise
        self._db.refresh(row)
        return _to_commitment(row)

    def update_commitment(
        self,
        commitment_id: int,
        *,
        title: str,
        target_date: date,
        start_minute: int | None,
        end_minute: int | None,
        all_day: bool,
        source: CommitmentSource,
        external_ref: str | None,
        nudge_mode: NudgeMode,
        urgent_quiet_bypass: bool,
        tentative: bool,
        notes: str | None,
    ) -> Commitment | None:
        row = self._db.get(CommitmentORM, commitment_id)
        if row is None:
            return None
        row.title = title
        row.target_date = target_date
        row.start_minute = start_minute
        row.end_minute = end_minute
        row.all_day = all_day
        row.source = source.value
        row.external_ref = external_ref
        row.nudge_mode = nudge_mode.value
        row.urgent_quiet_bypass = urgent_quiet_bypass
        row.tentative = tentative
        row.notes = notes
        self._db.commit()
        self._db.refresh(row)
        return _to_commitment(row)

    def delete_commitment(self, commitment_id: int) -> bool:
        row = self._db.get(CommitmentORM, commitment_id)
        if row is None:
            return False
        self._db.delete(row)
        self._db.commit()
        return True


def _to_friction_note(row: FrictionNoteORM) -> FrictionNote:
    return FrictionNote(
        id=row.id,
        target_date=row.target_date,
        tag=FrictionTag(row.tag),
        note=row.note,
        created_at=row.created_at,
        habit_cue_id=row.habit_cue_id,
        grid_block_id=row.grid_block_id,
        cue_title=row.habit_cue.title if row.habit_cue is not None else None,
        block_title=row.grid_block.title if row.grid_block is not None else None,
        cue_zone_chain=row.habit_cue.zone_chain if row.habit_cue is not None else None,
    )


def _to_habit_cue(
    row: HabitCueORM,
    block_ids: Sequence[int],
    recent_friction: Sequence[FrictionNote],
) -> HabitCue:
    return HabitCue(
        id=row.id,
        title=row.title,
        zone_chain=row.zone_chain,
        chain_order=row.chain_order,
        passive_rule=row.passive_rule,
        active=row.active,
        notes=row.notes,
        grid_block_ids=tuple(block_ids),
        recent_friction=tuple(recent_friction),
    )


class SqlAlchemyHabitCueRepo:
    """SQLAlchemy-backed repository for habit cues, block anchors, and friction notes."""

    RECENT_FRICTION_LIMIT = 3

    def __init__(self, db: Session) -> None:
        self._db = db

    def _load_block_links(self, cue_ids: Sequence[int]) -> dict[int, list[int]]:
        stmt = (
            select(GridBlockCueORM)
            .where(GridBlockCueORM.habit_cue_id.in_(cue_ids))
            .order_by(GridBlockCueORM.grid_block_id)
        )
        links: dict[int, list[int]] = defaultdict(list)
        for link in self._db.scalars(stmt).all():
            links[link.habit_cue_id].append(link.grid_block_id)
        return links

    def _load_recent_friction(self, cue_ids: Sequence[int]) -> dict[int, list[FrictionNote]]:
        ranked = (
            select(
                FrictionNoteORM.id.label("note_id"),
                func.row_number()
                .over(
                    partition_by=FrictionNoteORM.habit_cue_id,
                    order_by=[
                        FrictionNoteORM.target_date.desc(),
                        FrictionNoteORM.created_at.desc(),
                        FrictionNoteORM.id.desc(),
                    ],
                )
                .label("rn"),
            )
            .where(FrictionNoteORM.habit_cue_id.in_(cue_ids))
            .subquery()
        )
        stmt = (
            select(FrictionNoteORM)
            .join(ranked, FrictionNoteORM.id == ranked.c.note_id)
            .where(ranked.c.rn <= self.RECENT_FRICTION_LIMIT)
            .options(
                joinedload(FrictionNoteORM.habit_cue),
                joinedload(FrictionNoteORM.grid_block),
            )
            .order_by(
                FrictionNoteORM.target_date.desc(),
                FrictionNoteORM.created_at.desc(),
                FrictionNoteORM.id.desc(),
            )
        )
        notes_by_cue: dict[int, list[FrictionNote]] = defaultdict(list)
        for row in self._db.scalars(stmt).all():
            if row.habit_cue_id is not None:
                notes_by_cue[row.habit_cue_id].append(_to_friction_note(row))
        return notes_by_cue

    def _hydrate_cues(self, rows: Sequence[HabitCueORM]) -> list[HabitCue]:
        if not rows:
            return []
        cue_ids = [row.id for row in rows]
        links = self._load_block_links(cue_ids)
        recent = self._load_recent_friction(cue_ids)
        return [
            _to_habit_cue(row, links.get(row.id, []), recent.get(row.id, []))
            for row in rows
        ]

    def list_cues(
        self,
        *,
        active_only: bool = False,
        grid_block_id: int | None = None,
        zone_chain: str | None = None,
    ) -> list[HabitCue]:
        stmt = select(HabitCueORM).order_by(
            HabitCueORM.zone_chain.asc(),
            HabitCueORM.chain_order.asc(),
            HabitCueORM.id.asc(),
        )
        if active_only:
            stmt = stmt.where(HabitCueORM.active.is_(True))
        if zone_chain is not None:
            stmt = stmt.where(HabitCueORM.zone_chain == zone_chain)
        if grid_block_id is not None:
            stmt = stmt.join(
                GridBlockCueORM, GridBlockCueORM.habit_cue_id == HabitCueORM.id
            ).where(GridBlockCueORM.grid_block_id == grid_block_id)
        rows = self._db.scalars(stmt).all()
        return self._hydrate_cues(rows)

    def get_cue(self, cue_id: int) -> HabitCue | None:
        row = self._db.get(HabitCueORM, cue_id)
        if row is None:
            return None
        return self._hydrate_cues([row])[0]

    def create_cue(
        self,
        *,
        title: str,
        zone_chain: str,
        chain_order: int,
        passive_rule: str | None,
        active: bool,
        notes: str | None,
        grid_block_ids: Sequence[int],
    ) -> HabitCue:
        row = HabitCueORM(
            title=title,
            zone_chain=zone_chain,
            chain_order=chain_order,
            passive_rule=passive_rule,
            active=active,
            notes=notes,
        )
        self._db.add(row)
        self._db.flush()
        for block_id in grid_block_ids:
            self._db.add(GridBlockCueORM(grid_block_id=block_id, habit_cue_id=row.id))
        self._db.commit()
        self._db.refresh(row)
        return self._hydrate_cues([row])[0]

    def update_cue(
        self,
        cue_id: int,
        *,
        title: str,
        zone_chain: str,
        chain_order: int,
        passive_rule: str | None,
        active: bool,
        notes: str | None,
        grid_block_ids: Sequence[int] | None,
    ) -> HabitCue | None:
        row = self._db.get(HabitCueORM, cue_id)
        if row is None:
            return None
        row.title = title
        row.zone_chain = zone_chain
        row.chain_order = chain_order
        row.passive_rule = passive_rule
        row.active = active
        row.notes = notes
        if grid_block_ids is not None:
            self._db.execute(
                delete(GridBlockCueORM).where(GridBlockCueORM.habit_cue_id == cue_id)
            )
            for block_id in grid_block_ids:
                self._db.add(GridBlockCueORM(grid_block_id=block_id, habit_cue_id=cue_id))
        self._db.commit()
        self._db.refresh(row)
        return self._hydrate_cues([row])[0]

    def delete_cue(self, cue_id: int) -> bool:
        row = self._db.get(HabitCueORM, cue_id)
        if row is None:
            return False
        self._db.delete(row)
        self._db.commit()
        return True

    def find_missing_grid_block_ids(self, block_ids: Sequence[int]) -> list[int]:
        if not block_ids:
            return []
        found = set(
            self._db.scalars(
                select(GridBlockORM.id).where(GridBlockORM.id.in_(block_ids))
            ).all()
        )
        return [block_id for block_id in block_ids if block_id not in found]

    def list_friction_notes(
        self,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        habit_cue_id: int | None = None,
        grid_block_id: int | None = None,
        limit: int | None = 50,
    ) -> list[FrictionNote]:
        stmt = (
            select(FrictionNoteORM)
            .options(
                joinedload(FrictionNoteORM.habit_cue),
                joinedload(FrictionNoteORM.grid_block),
            )
            .order_by(
                FrictionNoteORM.target_date.desc(),
                FrictionNoteORM.created_at.desc(),
                FrictionNoteORM.id.desc(),
            )
        )
        if start_date is not None:
            stmt = stmt.where(FrictionNoteORM.target_date >= start_date)
        if end_date is not None:
            stmt = stmt.where(FrictionNoteORM.target_date <= end_date)
        if habit_cue_id is not None:
            stmt = stmt.where(FrictionNoteORM.habit_cue_id == habit_cue_id)
        if grid_block_id is not None:
            stmt = stmt.where(FrictionNoteORM.grid_block_id == grid_block_id)
        if limit is not None:
            stmt = stmt.limit(limit)
        return [_to_friction_note(row) for row in self._db.scalars(stmt).all()]

    def create_friction_note(
        self,
        *,
        target_date: date,
        tag: FrictionTag,
        note: str,
        habit_cue_id: int | None,
        grid_block_id: int | None,
    ) -> FrictionNote:
        row = FrictionNoteORM(
            target_date=target_date,
            tag=tag.value,
            note=note,
            habit_cue_id=habit_cue_id,
            grid_block_id=grid_block_id,
        )
        self._db.add(row)
        self._db.commit()
        stmt = (
            select(FrictionNoteORM)
            .where(FrictionNoteORM.id == row.id)
            .options(
                joinedload(FrictionNoteORM.habit_cue),
                joinedload(FrictionNoteORM.grid_block),
            )
        )
        loaded = self._db.scalar(stmt)
        if loaded is None:
            raise RuntimeError(f"Inserted FrictionNote {row.id} disappeared after commit.")
        return _to_friction_note(loaded)

    def delete_friction_note(self, note_id: int) -> bool:
        row = self._db.get(FrictionNoteORM, note_id)
        if row is None:
            return False
        self._db.delete(row)
        self._db.commit()
        return True
