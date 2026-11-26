"""Application service assembling the internal daily summary payload for Odin."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.domain.models import (
    Commitment,
    DaySchedule,
    FrictionNote,
    GridException,
    HabitCue,
    SliceKind,
)
from app.domain.passive_signals import (
    WEEKDAY_SHORT_NAMES,
    PassiveSignalsReport,
    canonicalize_rule_key,
)
from app.schemas.integrations import (
    DailySummaryCommitment,
    DailySummaryFrictionNote,
    DailySummaryGridException,
    DailySummaryPassiveSignal,
    DailySummaryResponse,
    DailySummaryWorkoutHydration,
)
from app.schemas.schedule import CompletedKratosSessionResponse
from app.services.cue_service import CueService
from app.services.passive_signal_service import PassiveSignalService
from app.services.schedule_service import ScheduleService

DAILY_SUMMARY_FRICTION_LIMIT = 200


def _exception_action(exc: GridException) -> str:
    if exc.skipped:
        return "skip"
    if exc.extension_count > 0:
        return "extend"
    return "modify"


def _to_summary_commitment(
    item: Commitment, conflicting_ids: set[int]
) -> DailySummaryCommitment:
    return DailySummaryCommitment(
        id=item.id,
        title=item.title,
        target_date=item.target_date,
        start_minute=item.start_minute,
        end_minute=item.end_minute,
        all_day=item.all_day,
        source=item.source.value,
        tentative=item.tentative,
        has_conflict=item.has_conflict or (item.id in conflicting_ids),
        notes=item.notes,
    )


def _to_summary_exception(exc: GridException) -> DailySummaryGridException:
    return DailySummaryGridException(
        id=exc.id,
        grid_block_id=exc.grid_block_id,
        target_date=exc.target_date,
        action=_exception_action(exc),
        new_start_minute=exc.start_minute,
        new_end_minute=exc.end_minute,
        extension_count=exc.extension_count,
        notes=exc.notes,
    )


def _to_summary_hydration(
    schedule: DaySchedule, resolved_date: date
) -> DailySummaryWorkoutHydration | None:
    hydration = schedule.workout_hydration
    if hydration is None:
        return None
    planned = hydration.planned_workout
    return DailySummaryWorkoutHydration(
        status="degraded" if hydration.peer_degraded else "ok",
        planned_routine_name=planned.title if planned is not None else None,
        planned_day_of_week=(
            WEEKDAY_SHORT_NAMES[resolved_date.weekday()] if planned is not None else None
        ),
        completed_sessions=[
            CompletedKratosSessionResponse.model_validate(s)
            for s in hydration.completed_sessions
        ],
        attached_grid_block_id=(
            planned.attached_grid_block_id if planned is not None else None
        ),
        error="kratos peer degraded" if hydration.peer_degraded else None,
    )


def _to_summary_friction_note(note: FrictionNote) -> DailySummaryFrictionNote:
    return DailySummaryFrictionNote(
        id=note.id,
        target_date=note.target_date,
        tag=note.tag.value,
        note=note.note,
        habit_cue_id=note.habit_cue_id,
        grid_block_id=note.grid_block_id,
    )


def _build_summary_passive_signals(
    cues: list[HabitCue], report: PassiveSignalsReport
) -> list[DailySummaryPassiveSignal]:
    summaries: list[DailySummaryPassiveSignal] = []
    for cue in cues:
        if not cue.passive_rule:
            continue
        canonical_key = canonicalize_rule_key(cue.passive_rule)
        signal = report.signals.get(canonical_key)
        if signal is None:
            continue
        summaries.append(
            DailySummaryPassiveSignal(
                cue_id=cue.id,
                cue_title=cue.title,
                rule_key=signal.rule_key,
                observed=signal.observed,
                fact_label=signal.fact_label,
                peer_status="ok" if signal.peer_available else "degraded",
            )
        )
    return summaries


def _build_summary_commitments(
    day_schedule: DaySchedule,
) -> list[DailySummaryCommitment]:
    conflicting_ids = {
        s.source_id
        for s in day_schedule.slices
        if s.kind == SliceKind.COMMITMENT and s.has_conflict
    }
    all_commitments = [
        *day_schedule.all_day_commitments,
        *day_schedule.timed_commitments,
    ]
    return [_to_summary_commitment(item, conflicting_ids) for item in all_commitments]


def _collect_degraded_peers(
    passive_report: PassiveSignalsReport, day_schedule: DaySchedule
) -> list[str]:
    degraded = list(passive_report.degraded_peers)
    if (
        day_schedule.workout_hydration is not None
        and day_schedule.workout_hydration.peer_degraded
        and "kratos" not in degraded
    ):
        degraded.append("kratos")
    return degraded


class DailySummaryService:
    """Aggregates today's schedule/hydration and yesterday's cues/friction for Odin."""

    def __init__(
        self,
        *,
        schedule_service: ScheduleService,
        cue_service: CueService,
        passive_signal_service: PassiveSignalService,
        timezone_name: str = "Pacific/Auckland",
    ) -> None:
        self._schedule_service = schedule_service
        self._cue_service = cue_service
        self._passive_signal_service = passive_signal_service
        self._tz = ZoneInfo(timezone_name)

    def build_daily_summary(
        self, target_date: date | None = None
    ) -> DailySummaryResponse:
        """Compile today's commitments/exceptions/workouts and yesterday's signals/friction."""
        resolved_date = target_date or datetime.now(self._tz).date()
        yesterday = resolved_date - timedelta(days=1)

        day_schedule = self._schedule_service.get_day_schedule(resolved_date)
        exceptions = self._schedule_service.list_exceptions(resolved_date)
        yesterday_notes = self._cue_service.list_friction_notes(
            start_date=yesterday,
            end_date=yesterday,
            limit=DAILY_SUMMARY_FRICTION_LIMIT,
        )
        active_cues = self._cue_service.list_cues(active_only=True)
        passive_report = self._passive_signal_service.get_passive_signals(
            target_date=yesterday
        )
        commitments = _build_summary_commitments(day_schedule)

        return DailySummaryResponse(
            date=resolved_date,
            yesterday=yesterday,
            commitments=commitments,
            tentative_count=sum(1 for item in commitments if item.tentative),
            conflict_count=sum(1 for item in commitments if item.has_conflict),
            grid_exceptions=[_to_summary_exception(exc) for exc in exceptions],
            workout_hydration=_to_summary_hydration(day_schedule, resolved_date),
            yesterday_friction_notes=[
                _to_summary_friction_note(note) for note in yesterday_notes
            ],
            yesterday_passive_signals=_build_summary_passive_signals(
                active_cues, passive_report
            ),
            degraded_peers=_collect_degraded_peers(passive_report, day_schedule),
        )
