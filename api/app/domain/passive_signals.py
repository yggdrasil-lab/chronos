"""Pure stateless evaluator for Chronos passive habit signals and Kratos workout hydration."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Literal
from zoneinfo import ZoneInfo

from app.domain.fasting import WEEKDAY_SHORT_NAMES, match_planned_day
from app.domain.models import (
    Commitment,
    CommitmentSource,
    CompletedKratosSession,
    DaySchedule,
    DayWorkoutHydration,
    HydratedWorkout,
    NudgeMode,
    SliceKind,
)
from app.domain.schedule_engine import MINUTES_PER_DAY

RULE_EIR_MORNING_WEIGH_IN = "eir_morning_weigh_in"
RULE_EIR_FASTED_WALK_40M = "eir_fasted_walk_40m"
RULE_KRATOS_WORKOUT_COMPLETED = "kratos_workout_completed"
RULE_PLUTUS_MONTHLY_CHECKIN = "plutus_monthly_checkin"

PASSIVE_RULE_ALIASES: dict[str, str] = {
    "kratos_workout_logged": RULE_KRATOS_WORKOUT_COMPLETED,
}

RULE_LABELS: dict[str, str] = {
    RULE_EIR_MORNING_WEIGH_IN: "Morning Weigh-in (Eir)",
    RULE_EIR_FASTED_WALK_40M: "Fasted Walk 40m (Eir)",
    RULE_KRATOS_WORKOUT_COMPLETED: "Workouts / Swimming (Kratos)",
    RULE_PLUTUS_MONTHLY_CHECKIN: "Monthly Financial Check-in (Plutus)",
}

WORKOUT_TITLE_WORDS = frozenset(
    {"workout", "workouts", "gym", "lift", "lifting", "swim", "swimming", "training"}
)
DEFAULT_TIMEZONE = ZoneInfo("Pacific/Auckland")
MIN_FASTED_WALK_MINUTES = 40
PLUTUS_FRESHNESS_DAYS = 31


@dataclass(frozen=True)
class PeerFetchStatus:
    """Availability outcome of an internal peer HTTP fetch."""

    available: bool
    error: str | None = None


@dataclass(frozen=True)
class EirDetectedWorkout:
    """Frozen snapshot of a detected workout returned by Eir."""

    started_at: str | None = None
    ended_at: str | None = None
    duration_minutes: float | int | None = None
    activity_type: str | None = None
    classification: str | None = None


@dataclass(frozen=True)
class EirDaySnapshot:
    """Frozen input snapshot from Eir peer endpoints for a target date."""

    status: PeerFetchStatus
    latest_measured_at: str | None = None
    latest_weight_kg: Decimal | float | int | str | None = None
    detected_workouts: tuple[EirDetectedWorkout, ...] = ()


@dataclass(frozen=True)
class KratosPlannedDay:
    """Frozen snapshot of a single day entry in Kratos weekly plan."""

    day: str | None = None
    day_index: int | None = None
    session_name: str | None = None
    activity: str | None = None
    workout_type: str | None = None
    emphasis: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class KratosSessionSnapshot:
    """Frozen snapshot of a logged Kratos session."""

    session_id: str
    routine_id: str | int | None = None
    routine_name: str | None = None
    activity_type: str | None = None
    classification: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    deleted_at: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class KratosDaySnapshot:
    """Frozen input snapshot from Kratos peer endpoints."""

    status: PeerFetchStatus
    planned_days: tuple[KratosPlannedDay, ...] = ()
    sessions: tuple[KratosSessionSnapshot, ...] = ()
    routine_names_by_id: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class PlutusAccountSnapshot:
    """Frozen snapshot of an active Plutus account and its latest snapshot metadata."""

    account_id: str
    name: str | None = None
    last_synced_at: str | None = None
    updated_at: str | None = None
    latest_snapshot_date: str | None = None
    latest_snapshot_created_at: str | None = None


@dataclass(frozen=True)
class PlutusCheckinSnapshot:
    """Frozen input snapshot from Plutus peer endpoints."""

    status: PeerFetchStatus
    accounts: tuple[PlutusAccountSnapshot, ...] = ()
    general_category_count: int = 0
    needs_review_count: int = 0


@dataclass(frozen=True)
class PassiveSignalResult:
    """Evaluated passive signal state for a single canonical rule key."""

    rule_key: str
    label: str
    observed: bool
    fact_label: str | None
    observed_at: str | None
    source: Literal["eir", "kratos", "plutus"]
    peer_available: bool


@dataclass(frozen=True)
class PassiveSignalsReport:
    """Complete passive signal evaluation report for a target date."""

    target_date: date
    signals: dict[str, PassiveSignalResult]
    degraded_peers: tuple[str, ...]


def canonicalize_rule_key(rule_key: str) -> str:
    """Normalize a passive signal rule key, resolving legacy aliases."""
    cleaned = rule_key.strip()
    return PASSIVE_RULE_ALIASES.get(cleaned, cleaned)


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _parse_iso_datetime(value: str | None, tz: ZoneInfo) -> datetime | None:
    cleaned = _clean_text(value)
    if cleaned is None:
        return None
    normalized = f"{cleaned[:-1]}+00:00" if cleaned.endswith("Z") else cleaned
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def _parse_iso_date(value: str | None, tz: ZoneInfo) -> date | None:
    cleaned = _clean_text(value)
    if cleaned is None:
        return None
    if "T" in cleaned or " " in cleaned:
        dt = _parse_iso_datetime(cleaned, tz)
        return dt.date() if dt is not None else None
    try:
        return date.fromisoformat(cleaned)
    except ValueError:
        return None


def _parse_decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    try:
        parsed = Decimal(cleaned)
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def _unobserved(
    rule_key: str, source: Literal["eir", "kratos", "plutus"], *, peer_available: bool
) -> PassiveSignalResult:
    return PassiveSignalResult(
        rule_key=rule_key,
        label=RULE_LABELS[rule_key],
        observed=False,
        fact_label=None,
        observed_at=None,
        source=source,
        peer_available=peer_available,
    )


def _evaluate_eir_weigh_in(
    target_date: date, eir: EirDaySnapshot, tz: ZoneInfo
) -> PassiveSignalResult:
    if not eir.status.available:
        return _unobserved(RULE_EIR_MORNING_WEIGH_IN, "eir", peer_available=False)
    weight = _parse_decimal(eir.latest_weight_kg)
    measured_dt = _parse_iso_datetime(eir.latest_measured_at, tz)
    if weight is None or measured_dt is None or measured_dt.date() != target_date:
        return _unobserved(RULE_EIR_MORNING_WEIGH_IN, "eir", peer_available=True)
    return PassiveSignalResult(
        rule_key=RULE_EIR_MORNING_WEIGH_IN,
        label=RULE_LABELS[RULE_EIR_MORNING_WEIGH_IN],
        observed=True,
        fact_label=f"Weighed in {measured_dt.strftime('%H:%M')}",
        observed_at=measured_dt.isoformat(),
        source="eir",
        peer_available=True,
    )


def _is_qualifying_walk(workout: EirDetectedWorkout) -> bool:
    kind_matches = any(
        (_clean_text(field) or "").lower() == "walk"
        for field in (workout.classification, workout.activity_type)
    )
    if not kind_matches or workout.duration_minutes is None:
        return False
    return float(workout.duration_minutes) >= MIN_FASTED_WALK_MINUTES


def _evaluate_eir_walk(
    target_date: date, eir: EirDaySnapshot, tz: ZoneInfo
) -> PassiveSignalResult:
    if not eir.status.available:
        return _unobserved(RULE_EIR_FASTED_WALK_40M, "eir", peer_available=False)
    qualifying = [w for w in eir.detected_workouts if _is_qualifying_walk(w)]
    if not qualifying:
        return _unobserved(RULE_EIR_FASTED_WALK_40M, "eir", peer_available=True)
    best = max(qualifying, key=lambda w: float(w.duration_minutes or 0))
    duration_mins = int(round(float(best.duration_minutes or 0)))
    obs_dt = _parse_iso_datetime(best.ended_at or best.started_at, tz)
    observed_at = obs_dt.isoformat() if obs_dt is not None else _clean_text(best.ended_at or best.started_at)
    return PassiveSignalResult(
        rule_key=RULE_EIR_FASTED_WALK_40M,
        label=RULE_LABELS[RULE_EIR_FASTED_WALK_40M],
        observed=True,
        fact_label=f"Walk {duration_mins}m observed",
        observed_at=observed_at,
        source="eir",
        peer_available=True,
    )


def _resolve_session_title(
    session: KratosSessionSnapshot, routine_names: dict[str, str]
) -> str:
    if cleaned_name := _clean_text(session.routine_name):
        return cleaned_name
    if session.routine_id is not None:
        routine_name = _clean_text(routine_names.get(str(session.routine_id)))
        if routine_name:
            return routine_name
    for fallback in (session.activity_type, session.classification):
        if cleaned := _clean_text(fallback):
            return cleaned[0].upper() + cleaned[1:]
    return "Workout"


def _extract_completed_kratos_sessions(
    target_date: date, kratos: KratosDaySnapshot, tz: ZoneInfo
) -> tuple[CompletedKratosSession, ...]:
    if not kratos.status.available:
        return ()
    routine_names = dict(kratos.routine_names_by_id)
    extracted: list[CompletedKratosSession] = []
    for item in kratos.sessions:
        if item.deleted_at is not None:
            continue
        start_dt = _parse_iso_datetime(item.started_at, tz)
        end_dt = _parse_iso_datetime(item.ended_at, tz)
        if start_dt is None or end_dt is None or start_dt.date() != target_date or end_dt < start_dt:
            continue
        start_minute = start_dt.hour * 60 + start_dt.minute
        end_minute = MINUTES_PER_DAY if end_dt.date() > target_date else end_dt.hour * 60 + end_dt.minute
        if end_minute <= start_minute:
            start_minute = min(start_minute, MINUTES_PER_DAY - 1)
            end_minute = min(MINUTES_PER_DAY, start_minute + 1)
        extracted.append(
            CompletedKratosSession(
                external_ref=str(item.session_id),
                title=_resolve_session_title(item, routine_names),
                activity_type=_clean_text(item.activity_type) or _clean_text(item.classification),
                start_minute=start_minute,
                end_minute=end_minute,
                started_at=start_dt.isoformat(),
                ended_at=end_dt.isoformat(),
            )
        )
    extracted.sort(key=lambda s: (s.start_minute, s.end_minute, s.external_ref))
    return tuple(extracted)


def _evaluate_kratos_completed(
    target_date: date, kratos: KratosDaySnapshot, tz: ZoneInfo
) -> PassiveSignalResult:
    if not kratos.status.available:
        return _unobserved(RULE_KRATOS_WORKOUT_COMPLETED, "kratos", peer_available=False)
    completed = _extract_completed_kratos_sessions(target_date, kratos, tz)
    if not completed:
        return _unobserved(RULE_KRATOS_WORKOUT_COMPLETED, "kratos", peer_available=True)
    latest = max(completed, key=lambda s: (s.end_minute, s.start_minute, s.external_ref))
    return PassiveSignalResult(
        rule_key=RULE_KRATOS_WORKOUT_COMPLETED,
        label=RULE_LABELS[RULE_KRATOS_WORKOUT_COMPLETED],
        observed=True,
        fact_label=f"{latest.title} logged",
        observed_at=latest.ended_at,
        source="kratos",
        peer_available=True,
    )


def _account_freshness(
    account: PlutusAccountSnapshot, target_date: date, tz: ZoneInfo
) -> tuple[date, str] | None:
    candidates: list[tuple[date, str]] = []
    for raw_ts in (account.latest_snapshot_created_at, account.last_synced_at, account.updated_at):
        if parsed := _parse_iso_datetime(raw_ts, tz):
            candidates.append((parsed.date(), parsed.isoformat()))
    if snap_date := _parse_iso_date(account.latest_snapshot_date, tz):
        candidates.append((snap_date, snap_date.isoformat()))
    if not candidates:
        return None
    latest_date, latest_ts = max(candidates, key=lambda pair: (pair[0], pair[1]))
    if abs((target_date - latest_date).days) > PLUTUS_FRESHNESS_DAYS:
        return None
    return latest_date, latest_ts


def _evaluate_plutus_checkin(
    target_date: date, plutus: PlutusCheckinSnapshot, tz: ZoneInfo
) -> PassiveSignalResult:
    if not plutus.status.available:
        return _unobserved(RULE_PLUTUS_MONTHLY_CHECKIN, "plutus", peer_available=False)
    fresh = [
        outcome
        for account in plutus.accounts
        if (outcome := _account_freshness(account, target_date, tz)) is not None
    ]
    if not fresh or plutus.general_category_count != 0 or plutus.needs_review_count != 0:
        return _unobserved(RULE_PLUTUS_MONTHLY_CHECKIN, "plutus", peer_available=True)
    _, latest_ts = max(fresh, key=lambda pair: (pair[0], pair[1]))
    return PassiveSignalResult(
        rule_key=RULE_PLUTUS_MONTHLY_CHECKIN,
        label=RULE_LABELS[RULE_PLUTUS_MONTHLY_CHECKIN],
        observed=True,
        fact_label="Accounts fresh, queue clear",
        observed_at=latest_ts,
        source="plutus",
        peer_available=True,
    )


def evaluate_passive_signals(
    *,
    target_date: date,
    eir: EirDaySnapshot,
    kratos: KratosDaySnapshot,
    plutus: PlutusCheckinSnapshot,
    tz: ZoneInfo = DEFAULT_TIMEZONE,
) -> PassiveSignalsReport:
    """Evaluate all four canonical passive signal rules from immutable peer snapshots."""
    signals = {
        RULE_EIR_MORNING_WEIGH_IN: _evaluate_eir_weigh_in(target_date, eir, tz),
        RULE_EIR_FASTED_WALK_40M: _evaluate_eir_walk(target_date, eir, tz),
        RULE_KRATOS_WORKOUT_COMPLETED: _evaluate_kratos_completed(target_date, kratos, tz),
        RULE_PLUTUS_MONTHLY_CHECKIN: _evaluate_plutus_checkin(target_date, plutus, tz),
    }
    degraded: list[str] = []
    if not eir.status.available:
        degraded.append("eir")
    if not kratos.status.available:
        degraded.append("kratos")
    if not plutus.status.available:
        degraded.append("plutus")
    return PassiveSignalsReport(
        target_date=target_date,
        signals=signals,
        degraded_peers=tuple(degraded),
    )


def evaluate_passive_rule(
    rule_key: str,
    *,
    target_date: date,
    eir: EirDaySnapshot,
    kratos: KratosDaySnapshot,
    plutus: PlutusCheckinSnapshot,
    tz: ZoneInfo = DEFAULT_TIMEZONE,
) -> PassiveSignalResult:
    """Evaluate a single passive rule key, accepting canonical keys and legacy aliases."""
    canonical = canonicalize_rule_key(rule_key)
    if canonical not in RULE_LABELS:
        raise ValueError(f"Unknown passive rule key: {rule_key}")
    report = evaluate_passive_signals(
        target_date=target_date, eir=eir, kratos=kratos, plutus=plutus, tz=tz
    )
    return report.signals[canonical]


def build_kratos_completed_commitments(
    target_date: date,
    kratos: KratosDaySnapshot,
    *,
    existing_commitments: Sequence[Commitment] = (),
    tz: ZoneInfo = DEFAULT_TIMEZONE,
) -> tuple[tuple[Commitment, ...], tuple[CompletedKratosSession, ...]]:
    """Convert completed Kratos sessions into deterministic read-only Commitments."""
    completed = _extract_completed_kratos_sessions(target_date, kratos, tz)
    existing_refs = {
        c.external_ref
        for c in existing_commitments
        if c.source == CommitmentSource.KRATOS and c.external_ref is not None
    }
    commitments: list[Commitment] = []
    next_id = -1
    for session in completed:
        if session.external_ref in existing_refs:
            continue
        commitments.append(
            Commitment(
                id=next_id,
                title=session.title,
                target_date=target_date,
                start_minute=session.start_minute,
                end_minute=session.end_minute,
                all_day=False,
                source=CommitmentSource.KRATOS,
                external_ref=session.external_ref,
                nudge_mode=NudgeMode.NONE,
                urgent_quiet_bypass=False,
                notes=None,
                read_only=True,
            )
        )
        next_id -= 1
    return tuple(commitments), completed


_match_planned_day = match_planned_day


def _find_matching_workout_block_id(schedule: DaySchedule) -> int | None:
    for item in schedule.slices:
        if item.kind != SliceKind.GRID_BLOCK or item.suppressed_tail:
            continue
        if (item.category or "").strip().lower() == "workout":
            return item.source_id
        title_words = set(re.findall(r"[a-z0-9]+", item.title.lower()))
        if title_words & WORKOUT_TITLE_WORDS:
            return item.source_id
    return None


def evaluate_workout_hydration(
    target_date: date,
    kratos: KratosDaySnapshot,
    schedule: DaySchedule,
    *,
    completed_sessions: tuple[CompletedKratosSession, ...] | None = None,
    tz: ZoneInfo = DEFAULT_TIMEZONE,
) -> DayWorkoutHydration:
    """Resolve Kratos planned workout placement and completed sessions for a day."""
    if not kratos.status.available:
        return DayWorkoutHydration(planned_workout=None, completed_sessions=(), peer_degraded=True)

    sessions = (
        completed_sessions
        if completed_sessions is not None
        else _extract_completed_kratos_sessions(target_date, kratos, tz)
    )
    planned_day = _match_planned_day(target_date, kratos.planned_days)
    if planned_day is None:
        return DayWorkoutHydration(planned_workout=None, completed_sessions=sessions, peer_degraded=False)

    workout_type = _clean_text(planned_day.workout_type)
    if workout_type and workout_type.lower() == "rest":
        return DayWorkoutHydration(planned_workout=None, completed_sessions=sessions, peer_degraded=False)

    title = (
        _clean_text(planned_day.session_name)
        or _clean_text(planned_day.activity)
        or workout_type
    )
    if title is None:
        return DayWorkoutHydration(planned_workout=None, completed_sessions=sessions, peer_degraded=False)

    matched_block_id = _find_matching_workout_block_id(schedule)
    planned_workout = HydratedWorkout(
        title=title,
        workout_type=workout_type,
        emphasis=_clean_text(planned_day.emphasis),
        notes=_clean_text(planned_day.notes),
        attached_grid_block_id=matched_block_id,
        placement="grid_block" if matched_block_id is not None else "day_header",
    )
    return DayWorkoutHydration(
        planned_workout=planned_workout,
        completed_sessions=sessions,
        peer_degraded=False,
    )


def attach_workout_hydration_to_schedule(
    schedule: DaySchedule, hydration: DayWorkoutHydration
) -> DaySchedule:
    """Return a new DaySchedule with workout hydration attached to matching slices and day context."""
    planned = hydration.planned_workout
    if planned is None or planned.placement != "grid_block" or planned.attached_grid_block_id is None:
        return replace(schedule, workout_hydration=hydration)

    updated_slices = tuple(
        replace(item, hydrated_workout=planned)
        if item.kind == SliceKind.GRID_BLOCK
        and item.source_id == planned.attached_grid_block_id
        and not item.suppressed_tail
        else item
        for item in schedule.slices
    )
    return replace(schedule, slices=updated_slices, workout_hydration=hydration)
