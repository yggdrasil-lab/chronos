"""Repository Protocol definitions separating persistence from services."""

from datetime import date
from typing import Protocol

from app.domain.models import Commitment, CommitmentSource, GridBlock, GridException, NudgeMode


class ScheduleRepository(Protocol):
    """Persistence contract for Chronos weekly grid blocks, exceptions, and commitments."""

    def list_grid_blocks(
        self, *, day_of_week: int | None = None, active_only: bool = False
    ) -> list[GridBlock]:
        """Return weekly grid blocks filtered by weekday and active state."""

    def get_grid_block(self, block_id: int) -> GridBlock | None:
        """Return a single grid block by ID, or None if missing."""

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
        """Persist and return a new weekly grid block."""

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
        """Update an existing weekly grid block, or return None if missing."""

    def delete_grid_block(self, block_id: int) -> bool:
        """Delete a weekly grid block and return True if it existed."""

    def list_exceptions_for_date(self, target_date: date) -> list[GridException]:
        """Return all per-date exceptions for the given target date."""

    def get_exception(self, exception_id: int) -> GridException | None:
        """Return a single grid exception by ID, or None if missing."""

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
        """Insert or update the exception for (grid_block_id, target_date)."""

    def delete_exception(self, exception_id: int) -> bool:
        """Delete a grid exception and return True if it existed."""

    def list_commitments(
        self, *, start_date: date | None = None, end_date: date | None = None
    ) -> list[Commitment]:
        """Return commitments optionally bounded by inclusive start_date and end_date."""

    def get_commitment(self, commitment_id: int) -> Commitment | None:
        """Return a single commitment by ID, or None if missing."""

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
        """Persist and return a new dated commitment."""

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
        """Update an existing commitment, or return None if missing."""

    def delete_commitment(self, commitment_id: int) -> bool:
        """Delete a commitment and return True if it existed."""
