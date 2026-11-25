"""SQLAlchemy implementation of the Chronos ScheduleRepository protocol."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.orm_models import CommitmentORM, GridBlockORM, GridExceptionORM
from app.domain.models import Commitment, CommitmentSource, GridBlock, GridException, NudgeMode


def _to_grid_block(row: GridBlockORM) -> GridBlock:
    return GridBlock(
        id=row.id,
        title=row.title,
        day_of_week=row.day_of_week,
        start_minute=row.start_minute,
        end_minute=row.end_minute,
        category=row.category,
        nudge_mode=NudgeMode(row.nudge_mode),
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
        else:
            row.skipped = skipped
            row.start_minute = start_minute
            row.end_minute = end_minute
            row.extension_count = extension_count
            row.notes = notes
        self._db.commit()
        self._db.refresh(row)
        return _to_grid_exception(row)

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
            notes=notes,
        )
        self._db.add(row)
        self._db.commit()
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
