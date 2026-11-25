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
