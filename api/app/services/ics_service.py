"""Application service projecting Chronos schedules and Kratos workouts to ICS."""

from collections import defaultdict
from datetime import date, datetime, timedelta
import logging
from zoneinfo import ZoneInfo

import httpx

from app.domain.ics_exporter import IcsEvent, render_ics_calendar
from app.domain.models import (
    Commitment,
    CompletedKratosSession,
    DaySchedule,
    GridBlock,
    GridException,
    HydratedWorkout,
    SliceKind,
)
from app.domain.passive_signals import (
    KratosDaySnapshot,
    PeerFetchStatus,
    build_kratos_completed_commitments,
    evaluate_workout_hydration,
)
from app.domain.schedule_engine import resolve_day_schedule
from app.repositories.protocols import ScheduleRepository
from app.services.peer_gateways import KratosGateway

logger = logging.getLogger(__name__)

ICS_PAST_DAYS = 7
ICS_FUTURE_DAYS = 30


def _format_planned_workout_notes(planned: HydratedWorkout) -> str | None:
    parts: list[str] = []
    if planned.workout_type:
        parts.append(f"Type: {planned.workout_type}")
    if planned.emphasis:
        parts.append(f"Emphasis: {planned.emphasis}")
    if planned.notes:
        parts.append(planned.notes)
    return "\n".join(parts) if parts else None


def _commitment_to_ics(commitment: Commitment, uid: str, category: str) -> IcsEvent:
    return IcsEvent(
        uid=uid,
        summary=commitment.title,
        target_date=commitment.target_date,
        start_minute=commitment.start_minute,
        end_minute=commitment.end_minute,
        all_day=commitment.all_day,
        description=commitment.notes,
        categories=(category,),
        status="CONFIRMED",
    )


def _project_grid_slices(day: date, schedule: DaySchedule) -> list[IcsEvent]:
    events: list[IcsEvent] = []
    block_slice_counts: dict[int, int] = defaultdict(int)
    for item in schedule.slices:
        if item.kind != SliceKind.GRID_BLOCK or item.suppressed_tail:
            continue
        seen_count = block_slice_counts[item.source_id]
        block_slice_counts[item.source_id] = seen_count + 1
        uid = (
            f"grid-{item.source_id}-{day.isoformat()}@chronos.local"
            if seen_count == 0
            else f"grid-{item.source_id}-{day.isoformat()}-{item.start_minute}@chronos.local"
        )
        events.append(
            IcsEvent(
                uid=uid,
                summary=item.title,
                target_date=day,
                start_minute=item.start_minute,
                end_minute=item.end_minute,
                all_day=False,
                description=item.notes,
                categories=(f"chronos:grid:{item.category or 'general'}",),
                status="CONFIRMED",
            )
        )
    return events


class IcsService:
    """Builds the rolling [-7d, +30d] iCalendar feed from Chronos and Kratos."""

    def __init__(
        self,
        repo: ScheduleRepository,
        timezone_name: str = "Pacific/Auckland",
        kratos_gateway: KratosGateway | None = None,
    ) -> None:
        self._repo = repo
        self._timezone_name = timezone_name
        self._tz = ZoneInfo(timezone_name)
        self._kratos_gateway = kratos_gateway

    def _fetch_kratos_snapshots_by_week(
        self, dates: list[date]
    ) -> dict[tuple[int, int], KratosDaySnapshot]:
        snapshots: dict[tuple[int, int], KratosDaySnapshot] = {}
        if self._kratos_gateway is None:
            return snapshots
        for day in dates:
            iso_year, iso_week, _ = day.isocalendar()
            key = (iso_year, iso_week)
            if key in snapshots:
                continue
            try:
                snapshots[key] = self._kratos_gateway.fetch_day_snapshot(day)
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                logger.warning("Kratos gateway exception during ICS build: %s", exc)
                snapshots[key] = KratosDaySnapshot(
                    status=PeerFetchStatus(available=False, error=str(exc))
                )
        return snapshots

    def _project_kratos_plan(
        self,
        day: date,
        kratos_snapshot: KratosDaySnapshot,
        schedule: DaySchedule,
        completed_sessions: tuple[CompletedKratosSession, ...],
    ) -> IcsEvent | None:
        hydration = evaluate_workout_hydration(
            day,
            kratos_snapshot,
            schedule,
            completed_sessions=completed_sessions,
            tz=self._tz,
        )
        if hydration.planned_workout is None:
            return None
        return IcsEvent(
            uid=f"kratos-plan-{day.isoformat()}@chronos.local",
            summary=hydration.planned_workout.title,
            target_date=day,
            all_day=True,
            description=_format_planned_workout_notes(hydration.planned_workout),
            categories=("chronos:kratos-plan",),
            status="CONFIRMED",
        )

    def _project_kratos_completed(
        self,
        day: date,
        confirmed_commitments: list[Commitment],
        kratos_snapshot: KratosDaySnapshot | None,
    ) -> tuple[tuple[Commitment, ...], tuple[CompletedKratosSession, ...], list[IcsEvent]]:
        if kratos_snapshot is None:
            return (), (), []
        completed_commitments, completed_sessions = build_kratos_completed_commitments(
            day,
            kratos_snapshot,
            existing_commitments=confirmed_commitments,
            tz=self._tz,
        )
        events = [
            _commitment_to_ics(
                item,
                uid=f"kratos-session-{item.external_ref}@chronos.local",
                category="chronos:kratos",
            )
            for item in completed_commitments
        ]
        return completed_commitments, completed_sessions, events

    def _project_day(
        self,
        day: date,
        *,
        confirmed_commitments: list[Commitment],
        kratos_snapshot: KratosDaySnapshot | None,
        grid_blocks: list[GridBlock],
        day_exceptions: list[GridException],
        include_grid: bool,
    ) -> list[IcsEvent]:
        events = [
            _commitment_to_ics(
                item,
                uid=f"commitment-{item.id}@chronos.local",
                category=f"chronos:{item.source.value}",
            )
            for item in confirmed_commitments
        ]
        completed_commitments, completed_sessions, kratos_events = (
            self._project_kratos_completed(day, confirmed_commitments, kratos_snapshot)
        )
        events.extend(kratos_events)

        schedule = resolve_day_schedule(
            day,
            grid_blocks if include_grid else (),
            day_exceptions if include_grid else (),
            [*confirmed_commitments, *completed_commitments],
        )
        if kratos_snapshot is not None:
            planned = self._project_kratos_plan(
                day, kratos_snapshot, schedule, completed_sessions
            )
            if planned is not None:
                events.append(planned)
        if include_grid:
            events.extend(_project_grid_slices(day, schedule))
        return events

    def _load_grid_context(
        self, start_date: date, end_date: date, include_grid: bool
    ) -> tuple[list[GridBlock], dict[date, list[GridException]]]:
        if not include_grid:
            return [], {}
        grid_blocks = self._repo.list_grid_blocks(active_only=True)
        exceptions_by_date: dict[date, list[GridException]] = defaultdict(list)
        for exc in self._repo.list_exceptions_in_range(start_date, end_date):
            exceptions_by_date[exc.target_date].append(exc)
        return grid_blocks, exceptions_by_date

    def build_feed(
        self, *, include_grid: bool = False, reference_date: date | None = None
    ) -> str:
        """Build the rolling [-7d, +30d] RFC 5545 calendar feed."""
        today = reference_date or datetime.now(self._tz).date()
        start_date = today - timedelta(days=ICS_PAST_DAYS)
        end_date = today + timedelta(days=ICS_FUTURE_DAYS)
        dates = [
            start_date + timedelta(days=offset)
            for offset in range((end_date - start_date).days + 1)
        ]

        commitments_by_date: dict[date, list[Commitment]] = defaultdict(list)
        for item in self._repo.list_commitments(start_date=start_date, end_date=end_date):
            if not item.tentative:
                commitments_by_date[item.target_date].append(item)

        grid_blocks, exceptions_by_date = self._load_grid_context(
            start_date, end_date, include_grid
        )
        kratos_by_week = self._fetch_kratos_snapshots_by_week(dates)
        events: list[IcsEvent] = []
        for day in dates:
            iso_year, iso_week, _ = day.isocalendar()
            events.extend(
                self._project_day(
                    day,
                    confirmed_commitments=commitments_by_date.get(day, []),
                    kratos_snapshot=kratos_by_week.get((iso_year, iso_week)),
                    grid_blocks=grid_blocks,
                    day_exceptions=exceptions_by_date.get(day, []),
                    include_grid=include_grid,
                )
            )
        return render_ics_calendar(
            events, calendar_name="Chronos", timezone=self._timezone_name
        )
