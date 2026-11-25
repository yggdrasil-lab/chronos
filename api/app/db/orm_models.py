"""SQLAlchemy 2.0 ORM models for Chronos scheduling tables."""

from datetime import UTC, date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class GridBlockORM(Base):
    """Persistent recurring weekly grid block template."""

    __tablename__ = "grid_blocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    start_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    end_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False, default="general")
    nudge_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="none")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    exceptions: Mapped[list["GridExceptionORM"]] = relationship(
        back_populates="grid_block",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    cue_links: Mapped[list["GridBlockCueORM"]] = relationship(
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class GridExceptionORM(Base):
    """Persistent per-date override for a recurring weekly grid block."""

    __tablename__ = "grid_exceptions"
    __table_args__ = (
        UniqueConstraint("grid_block_id", "target_date", name="uq_grid_exceptions_block_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    grid_block_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("grid_blocks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    skipped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    start_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extension_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    grid_block: Mapped[GridBlockORM] = relationship(back_populates="exceptions")


class CommitmentORM(Base):
    """Persistent dated commitment synced from external sources or manual entry."""

    __tablename__ = "commitments"
    __table_args__ = (
        Index("ix_commitments_date_start", "target_date", "start_minute"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    target_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    start_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    all_day: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    external_ref: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    nudge_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="none")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class HabitCueORM(Base):
    """Persistent habit cue anchored to zero or more weekly grid blocks."""

    __tablename__ = "habit_cues"
    __table_args__ = (
        Index("ix_habit_cues_zone_order", "zone_chain", "chain_order", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    zone_chain: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    chain_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passive_rule: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    block_links: Mapped[list["GridBlockCueORM"]] = relationship(
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    friction_notes: Mapped[list["FrictionNoteORM"]] = relationship(
        back_populates="habit_cue",
        passive_deletes=True,
    )


class GridBlockCueORM(Base):
    """Join table linking recurring grid blocks to habit cues."""

    __tablename__ = "grid_block_cues"

    grid_block_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("grid_blocks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    habit_cue_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("habit_cues.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )


class FrictionNoteORM(Base):
    """Persistent friction note linked optionally to a habit cue and/or grid block."""

    __tablename__ = "friction_notes"
    __table_args__ = (
        Index("ix_friction_notes_date_created", "target_date", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    target_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    tag: Mapped[str] = mapped_column(String(40), nullable=False)
    note: Mapped[str] = mapped_column(String(280), nullable=False)
    habit_cue_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("habit_cues.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    grid_block_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("grid_blocks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    habit_cue: Mapped[HabitCueORM | None] = relationship(back_populates="friction_notes")
    grid_block: Mapped[GridBlockORM | None] = relationship()
