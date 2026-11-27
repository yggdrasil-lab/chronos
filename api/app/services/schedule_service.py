"""Application service layer coordinating validation, persistence, and schedule resolution."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime
import logging
from zoneinfo import ZoneInfo

import httpx

from app.domain.errors import EntityNotFoundError, ScheduleValidationError
from app.domain.fasting import (
    FRIDAY_WEEKDAY,
    EirDailyVitalsSnapshot,
    evaluate_fasting_suggestion,
)
from app.domain.models import (
    Commitment,
    CommitmentSource,
    DaySchedule,
    GridBlock,
    GridException,
    NowNextState,
    NudgeMode,
)
from app.domain.passive_signals import (
    KratosDaySnapshot,
    PeerFetchStatus,
    attach_workout_hydration_to_schedule,
    build_kratos_completed_commitments,
    evaluate_workout_hydration,
)
from app.domain.schedule_engine import (
    MINUTES_PER_DAY,
    compute_now_next,
    plan_block_extension,
    resolve_day_schedule,
)
from app.repositories.protocols import ScheduleRepository
from app.services.peer_gateways import EirVitalsGateway, KratosGateway

logger = logging.getLogger(__name__)


def _require_non_empty(value: str, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ScheduleValidationError(f"{field_name} must not be empty.")
    return cleaned


def _validate_day_of_week(day_of_week: int) -> None:
    if day_of_week < 0 or day_of_week > 6:
        raise ScheduleValidationError(f"day_of_week must be in [0, 6], got {day_of_week}.")


def _validate_time_window(start_minute: int, end_minute: int) -> None:
    if start_minute < 0 or end_minute > MINUTES_PER_DAY or start_minute >= end_minute:
        raise ScheduleValidationError(
            f"Time window must satisfy 0 <= start_minute < end_minute <= 1440, got [{start_minute}, {end_minute})."
        )


def _validate_commitment_timing(
    all_day: bool, start_minute: int | None, end_minute: int | None
) -> None:
    if all_day:
        if start_minute is not None or end_minute is not None:
            raise ScheduleValidationError(
                "all_day commitments must not specify start_minute or end_minute."
            )
        return
    if start_minute is None or end_minute is None:
        raise ScheduleValidationError(
            "Timed commitments require both start_minute and end_minute."
        )
    _validate_time_window(start_minute, end_minute)


class ScheduleService:
    """Use-case coordinator for weekly grid blocks, exceptions, commitments, and schedules."""

    def __init__(
        self,
        repo: ScheduleRepository,
        timezone_name: str = "Pacific/Auckland",
        kratos_gateway: KratosGateway | None = None,
        eir_vitals_gateway: EirVitalsGateway | None = None,
    ) -> None:
        self._repo = repo
        self._tz = ZoneInfo(timezone_name)
        self._kratos_gateway = kratos_gateway
        self._eir_vitals_gateway = eir_vitals_gateway

    def list_grid_blocks(
        self, *, day_of_week: int | None = None, active_only: bool = False
    ) -> list[GridBlock]:
        if day_of_week is not None:
            _validate_day_of_week(day_of_week)
        return self._repo.list_grid_blocks(day_of_week=day_of_week, active_only=active_only)

    def create_grid_block(
        self,
        *,
        title: str,
        day_of_week: int,
        start_minute: int,
        end_minute: int,
        category: str,
        nudge_mode: NudgeMode,
        flexible: bool = False,
        active: bool = True,
        notes: str | None = None,
    ) -> GridBlock:
        clean_title = _require_non_empty(title, "title")
        clean_category = _require_non_empty(category, "category")
        _validate_day_of_week(day_of_week)
        _validate_time_window(start_minute, end_minute)
        return self._repo.create_grid_block(
            title=clean_title,
            day_of_week=day_of_week,
            start_minute=start_minute,
            end_minute=end_minute,
            category=clean_category,
            nudge_mode=nudge_mode,
            flexible=flexible,
            active=active,
            notes=notes,
        )

    def update_grid_block(
        self,
        block_id: int,
        *,
        title: str | None = None,
        day_of_week: int | None = None,
        start_minute: int | None = None,
        end_minute: int | None = None,
        category: str | None = None,
        nudge_mode: NudgeMode | None = None,
        flexible: bool | None = None,
        active: bool | None = None,
        notes: str | None = None,
        notes_provided: bool = False,
    ) -> GridBlock:
        existing = self._repo.get_grid_block(block_id)
        if existing is None:
            raise EntityNotFoundError(f"GridBlock {block_id} not found.")

        next_title = _require_non_empty(title, "title") if title is not None else existing.title
        next_category = (
            _require_non_empty(category, "category") if category is not None else existing.category
        )
        next_day = day_of_week if day_of_week is not None else existing.day_of_week
        next_start = start_minute if start_minute is not None else existing.start_minute
        next_end = end_minute if end_minute is not None else existing.end_minute
        _validate_day_of_week(next_day)
        _validate_time_window(next_start, next_end)

        updated = self._repo.update_grid_block(
            block_id,
            title=next_title,
            day_of_week=next_day,
            start_minute=next_start,
            end_minute=next_end,
            category=next_category,
            nudge_mode=nudge_mode if nudge_mode is not None else existing.nudge_mode,
            flexible=flexible if flexible is not None else existing.flexible,
            active=active if active is not None else existing.active,
            notes=notes if notes_provided else existing.notes,
        )
        if updated is None:
            raise EntityNotFoundError(f"GridBlock {block_id} not found.")
        return updated

    def delete_grid_block(self, block_id: int) -> None:
        if not self._repo.delete_grid_block(block_id):
            raise EntityNotFoundError(f"GridBlock {block_id} not found.")

    def list_exceptions(self, target_date: date) -> list[GridException]:
        return self._repo.list_exceptions_for_date(target_date)

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
        block = self._repo.get_grid_block(grid_block_id)
        if block is None:
            raise EntityNotFoundError(f"GridBlock {grid_block_id} not found.")
        if extension_count < 0:
            raise ScheduleValidationError("extension_count must be >= 0.")
        if not skipped:
            eff_start = start_minute if start_minute is not None else block.start_minute
            eff_end = end_minute if end_minute is not None else block.end_minute
            _validate_time_window(eff_start, eff_end)
        return self._repo.upsert_exception(
            grid_block_id=grid_block_id,
            target_date=target_date,
            skipped=skipped,
            start_minute=start_minute,
            end_minute=end_minute,
            extension_count=extension_count,
            notes=notes,
        )

    def delete_exception(self, exception_id: int) -> None:
        if not self._repo.delete_exception(exception_id):
            raise EntityNotFoundError(f"GridException {exception_id} not found.")

    def list_commitments(
        self, *, start_date: date | None = None, end_date: date | None = None
    ) -> list[Commitment]:
        if start_date is not None and end_date is not None and start_date > end_date:
            raise ScheduleValidationError("start_date must be <= end_date.")
        return self._repo.list_commitments(start_date=start_date, end_date=end_date)

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
        nudge_mode: NudgeMode | None,
        urgent_quiet_bypass: bool = False,
        tentative: bool = False,
        notes: str | None = None,
    ) -> Commitment:
        clean_title = _require_non_empty(title, "title")
        _validate_commitment_timing(all_day, start_minute, end_minute)
        resolved_nudge = (
            nudge_mode
            if nudge_mode is not None
            else (NudgeMode.NONE if all_day else NudgeMode.TWO_STAGE)
        )
        return self._repo.create_commitment(
            title=clean_title,
            target_date=target_date,
            start_minute=start_minute,
            end_minute=end_minute,
            all_day=all_day,
            source=source,
            external_ref=external_ref,
            nudge_mode=resolved_nudge,
            urgent_quiet_bypass=urgent_quiet_bypass,
            tentative=tentative,
            notes=notes,
        )

    def update_commitment(
        self,
        commitment_id: int,
        *,
        title: str | None = None,
        target_date: date | None = None,
        start_minute: int | None = None,
        end_minute: int | None = None,
        start_provided: bool = False,
        end_provided: bool = False,
        all_day: bool | None = None,
        source: CommitmentSource | None = None,
        external_ref: str | None = None,
        external_ref_provided: bool = False,
        nudge_mode: NudgeMode | None = None,
        urgent_quiet_bypass: bool | None = None,
        tentative: bool | None = None,
        tentative_provided: bool = False,
        notes: str | None = None,
        notes_provided: bool = False,
    ) -> Commitment:
        existing = self._repo.get_commitment(commitment_id)
        if existing is None:
            raise EntityNotFoundError(f"Commitment {commitment_id} not found.")

        next_title = _require_non_empty(title, "title") if title is not None else existing.title
        next_date = target_date if target_date is not None else existing.target_date
        next_all_day = all_day if all_day is not None else existing.all_day
        next_start = start_minute if start_provided else existing.start_minute
        next_end = end_minute if end_provided else existing.end_minute
        if next_all_day and all_day is True and not start_provided and not end_provided:
            next_start = None
            next_end = None
        _validate_commitment_timing(next_all_day, next_start, next_end)
        if tentative_provided:
            if tentative is None:
                raise ScheduleValidationError("tentative must be a boolean.")
            next_tentative = tentative
        else:
            next_tentative = existing.tentative

        updated = self._repo.update_commitment(
            commitment_id,
            title=next_title,
            target_date=next_date,
            start_minute=next_start,
            end_minute=next_end,
            all_day=next_all_day,
            source=source if source is not None else existing.source,
            external_ref=external_ref if external_ref_provided else existing.external_ref,
            nudge_mode=nudge_mode if nudge_mode is not None else existing.nudge_mode,
            urgent_quiet_bypass=(
                urgent_quiet_bypass
                if urgent_quiet_bypass is not None
                else existing.urgent_quiet_bypass
            ),
            tentative=next_tentative,
            notes=notes if notes_provided else existing.notes,
        )
        if updated is None:
            raise EntityNotFoundError(f"Commitment {commitment_id} not found.")
        return updated

    def delete_commitment(self, commitment_id: int) -> None:
        if not self._repo.delete_commitment(commitment_id):
            raise EntityNotFoundError(f"Commitment {commitment_id} not found.")

    def _fetch_kratos_snapshot(self, target_date: date) -> KratosDaySnapshot:
        assert self._kratos_gateway is not None
        try:
            return self._kratos_gateway.fetch_day_snapshot(target_date)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            logger.warning("Kratos gateway exception during schedule resolution: %s", exc)
            return KratosDaySnapshot(status=PeerFetchStatus(available=False, error=str(exc)))

    def _fetch_eir_daily_vitals(self, target_date: date) -> EirDailyVitalsSnapshot:
        assert self._eir_vitals_gateway is not None
        try:
            return self._eir_vitals_gateway.fetch_daily_vitals(target_date)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            logger.warning("Eir vitals gateway exception during schedule resolution: %s", exc)
            return EirDailyVitalsSnapshot(
                status=PeerFetchStatus(available=False, error=str(exc))
            )

    def _fetch_schedule_peers(
        self, resolved_date: date
    ) -> tuple[KratosDaySnapshot | None, EirDailyVitalsSnapshot | None]:
        is_friday = resolved_date.weekday() == FRIDAY_WEEKDAY
        if not is_friday or self._eir_vitals_gateway is None:
            kratos = (
                self._fetch_kratos_snapshot(resolved_date)
                if self._kratos_gateway is not None
                else None
            )
            return kratos, None

        if self._kratos_gateway is None:
            return None, self._fetch_eir_daily_vitals(resolved_date)

        with ThreadPoolExecutor(max_workers=2) as executor:
            kratos_future = executor.submit(self._fetch_kratos_snapshot, resolved_date)
            eir_future = executor.submit(self._fetch_eir_daily_vitals, resolved_date)
            return kratos_future.result(), eir_future.result()

    def get_day_schedule(self, target_date: date | None = None) -> DaySchedule:
        now_dt = datetime.now(self._tz)
        resolved_date = target_date or now_dt.date()
        grid_blocks = self._repo.list_grid_blocks(
            day_of_week=resolved_date.weekday(), active_only=True
        )
        exceptions = self._repo.list_exceptions_for_date(resolved_date)
        commitments = self._repo.list_commitments(start_date=resolved_date, end_date=resolved_date)

        kratos_snapshot, eir_vitals = self._fetch_schedule_peers(resolved_date)
        if kratos_snapshot is None:
            schedule = resolve_day_schedule(resolved_date, grid_blocks, exceptions, commitments)
        else:
            completed_commitments, completed_sessions = build_kratos_completed_commitments(
                resolved_date,
                kratos_snapshot,
                existing_commitments=commitments,
                tz=self._tz,
            )
            base_schedule = resolve_day_schedule(
                resolved_date,
                grid_blocks,
                exceptions,
                [*commitments, *completed_commitments],
            )
            hydration = evaluate_workout_hydration(
                resolved_date,
                kratos_snapshot,
                base_schedule,
                completed_sessions=completed_sessions,
                tz=self._tz,
            )
            schedule = attach_workout_hydration_to_schedule(base_schedule, hydration)

        fasting = evaluate_fasting_suggestion(
            resolved_date,
            now_dt.time(),
            kratos_snapshot=kratos_snapshot,
            eir_vitals=eir_vitals,
        )
        return replace(schedule, fasting_suggestion=fasting)

    def get_now_next(
        self, target_date: date | None = None, now_minute: int | None = None
    ) -> NowNextState:
        now_dt = datetime.now(self._tz)
        resolved_date = target_date or now_dt.date()
        resolved_minute = (
            now_minute if now_minute is not None else now_dt.hour * 60 + now_dt.minute
        )
        schedule = self.get_day_schedule(resolved_date)
        return compute_now_next(schedule, resolved_minute)

    def extend_grid_block(
        self,
        *,
        target_date: date,
        grid_block_id: int,
        minutes: int,
    ) -> tuple[GridException, list[GridException]]:
        target_block = self._repo.get_grid_block(grid_block_id)
        if target_block is None:
            raise EntityNotFoundError(f"GridBlock {grid_block_id} not found.")
        day_blocks = self._repo.list_grid_blocks(
            day_of_week=target_date.weekday(), active_only=True
        )
        exceptions = self._repo.list_exceptions_for_date(target_date)
        plan = plan_block_extension(target_block, target_date, minutes, day_blocks, exceptions)
        saved = self._repo.upsert_exceptions_batch([plan.extended, *plan.trimmed])
        return saved[0], saved[1:]
