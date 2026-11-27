"""Repository Protocol definitions separating persistence from services."""

from collections.abc import Sequence
from datetime import date
from typing import Protocol

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
    PushDelivery,
    PushSubscription,
    TravelOverlay,
    TravelOverlayMode,
)


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
        flexible: bool,
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
        flexible: bool,
        active: bool,
        notes: str | None,
    ) -> GridBlock | None:
        """Update an existing weekly grid block, or return None if missing."""

    def delete_grid_block(self, block_id: int) -> bool:
        """Delete a weekly grid block and return True if it existed."""

    def list_exceptions_for_date(self, target_date: date) -> list[GridException]:
        """Return all per-date exceptions for the given target date."""

    def list_exceptions_in_range(
        self, start_date: date, end_date: date
    ) -> tuple[GridException, ...]:
        """Return all per-date exceptions within [start_date, end_date] inclusive."""

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

    def upsert_exceptions_batch(self, drafts: Sequence[GridExceptionDraft]) -> list[GridException]:
        """Atomically upsert multiple per-date grid exceptions in one transaction."""

    def delete_exception(self, exception_id: int) -> bool:
        """Delete a grid exception and return True if it existed."""

    def list_commitments(
        self,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        source: CommitmentSource | None = None,
        external_ref_prefix: str | None = None,
    ) -> list[Commitment]:
        """Return commitments optionally bounded by date range, source, and external_ref prefix."""

    def get_commitment(self, commitment_id: int) -> Commitment | None:
        """Return a single commitment by ID, or None if missing."""

    def get_commitment_by_source_external_ref(
        self, source: CommitmentSource, external_ref: str
    ) -> Commitment | None:
        """Return a single commitment matching (source, external_ref), or None if missing."""

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
        urgent_quiet_bypass: bool,
        tentative: bool,
        notes: str | None,
    ) -> Commitment | None:
        """Update an existing commitment, or return None if missing."""

    def delete_commitment(self, commitment_id: int) -> bool:
        """Delete a commitment and return True if it existed."""

    def upsert_commitment_by_source_external_ref(
        self,
        *,
        title: str,
        target_date: date,
        start_minute: int | None,
        end_minute: int | None,
        all_day: bool,
        source: CommitmentSource,
        external_ref: str,
        nudge_mode: NudgeMode,
        urgent_quiet_bypass: bool,
        tentative: bool = False,
        notes: str | None,
    ) -> Commitment:
        """Insert or update a commitment matched by (source, external_ref)."""

    def delete_commitment_by_source_external_ref(
        self, source: CommitmentSource, external_ref: str
    ) -> bool:
        """Delete a commitment matching (source, external_ref) and return True if deleted."""

    def list_travel_overlays(
        self,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        external_ref: str | None = None,
    ) -> list[TravelOverlay]:
        """Return travel overlays optionally overlapping [start_date, end_date] or matching ref."""

    def upsert_travel_overlay(
        self,
        *,
        external_ref: str,
        label: str,
        start_date: date,
        end_date: date,
        mode: TravelOverlayMode,
    ) -> TravelOverlay:
        """Insert or update a travel overlay keyed by unique external_ref."""

    def delete_travel_overlay_by_external_ref(self, external_ref: str) -> bool:
        """Delete a travel overlay matching external_ref and return True if deleted."""


class HabitCueRepository(Protocol):
    """Persistence contract for Chronos habit cues, block links, and friction notes."""

    def list_cues(
        self,
        *,
        active_only: bool = False,
        grid_block_id: int | None = None,
        zone_chain: str | None = None,
    ) -> list[HabitCue]:
        """Return habit cues ordered by (zone_chain ASC, chain_order ASC, id ASC)."""

    def get_cue(self, cue_id: int) -> HabitCue | None:
        """Return a single habit cue by ID, or None if missing."""

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
        """Persist and return a new habit cue with anchored grid block links."""

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
        """Update an existing habit cue and optionally replace its grid block links."""

    def delete_cue(self, cue_id: int) -> bool:
        """Delete a habit cue and return True if it existed."""

    def find_missing_grid_block_ids(self, block_ids: Sequence[int]) -> list[int]:
        """Return any IDs from block_ids that do not exist in grid_blocks."""

    def list_friction_notes(
        self,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        habit_cue_id: int | None = None,
        grid_block_id: int | None = None,
        limit: int | None = 50,
    ) -> list[FrictionNote]:
        """Return friction notes ordered by (target_date DESC, created_at DESC, id DESC)."""

    def create_friction_note(
        self,
        *,
        target_date: date,
        tag: FrictionTag,
        note: str,
        habit_cue_id: int | None,
        grid_block_id: int | None,
    ) -> FrictionNote:
        """Persist and return a new friction note."""

    def delete_friction_note(self, note_id: int) -> bool:
        """Delete a friction note and return True if it existed."""


class PushRepository(Protocol):
    """Persistence contract for Web Push subscriptions and delivery deduplication."""

    def upsert_subscription(
        self,
        *,
        endpoint: str,
        p256dh: str,
        auth: str,
        user_agent: str | None,
    ) -> PushSubscription:
        """Insert or update a push subscription keyed by endpoint."""

    def delete_subscription_by_endpoint(self, endpoint: str) -> bool:
        """Delete a push subscription by endpoint and return True if it existed."""

    def list_subscriptions(self) -> list[PushSubscription]:
        """Return all registered push subscriptions."""

    def list_sent_dedupe_keys(self, target_date: date) -> set[str]:
        """Return all dedupe keys already recorded for target_date."""

    def record_delivery(
        self,
        *,
        dedupe_key: str,
        target_date: date,
        source_kind: str,
        source_id: str,
        stage: str,
    ) -> PushDelivery:
        """Record a sent push notification for single-fire deduplication."""
