"""Pure deterministic fasting protocol evaluator and live phase resolver."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, time
import math
from typing import TYPE_CHECKING, Any, Literal

from app.domain.sleep_recovery import EirNapRecord

if TYPE_CHECKING:
    from app.domain.passive_signals import (
        KratosDaySnapshot,
        KratosPlannedDay,
        PeerFetchStatus,
    )

FastingProtocol = Literal["14:10", "18:6"]
FastingPhase = Literal["fasting", "eating", "closed"]

BASELINE_PROTOCOL: FastingProtocol = "14:10"
BASELINE_WINDOW_START = "10:30"
BASELINE_WINDOW_END = "20:30"

RESET_PROTOCOL: FastingProtocol = "18:6"
RESET_WINDOW_START = "14:30"
RESET_WINDOW_END = "20:30"

FRIDAY_WEEKDAY = 4
WEEKDAY_SHORT_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MIN_GREEN_SLEEP_HOURS = 7.0
MIN_GREEN_HRV_MS = 45.0
MIN_GREEN_DEEP_SLEEP_HOURS = 0.6
MAX_GREEN_RESTING_HR_BPM = 78


@dataclass(frozen=True)
class EirDailyVitalsSnapshot:
    """Frozen daily recovery vitals snapshot from Eir peer API."""

    status: PeerFetchStatus
    sleep_duration_hours: float | None = None
    sleep_deep_hours: float | None = None
    hrv_ms: float | None = None
    overnight_hrv_ms: float | None = None
    resting_hr_bpm: int | None = None
    nap_duration_hours: float | None = None
    nap_count: int = 0
    naps: tuple[EirNapRecord, ...] = ()


@dataclass(frozen=True)
class FastingSuggestion:
    """Deterministic daily fasting window and live phase suggestion."""

    protocol: FastingProtocol
    window_start: str
    window_end: str
    phase: FastingPhase
    is_optional_reset: bool
    reason: str


def match_planned_day(
    target_date: date, planned_days: Sequence[Any]
) -> KratosPlannedDay | None:
    """Match a Kratos weekly plan entry to target_date by day label, day_index, or order."""
    weekday = target_date.weekday()
    short_name = WEEKDAY_SHORT_NAMES[weekday].lower()
    for index, entry in enumerate(planned_days):
        day_label = (entry.day or "").strip().lower()
        if day_label:
            if day_label == short_name or day_label[:3] == short_name:
                return entry
            continue
        if entry.day_index is not None:
            if entry.day_index == weekday:
                return entry
            continue
        if index == weekday:
            return entry
    return None


def _parse_hhmm(value: str) -> time:
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid HH:MM time string: {value!r}")
    return time(int(parts[0]), int(parts[1]))


def evaluate_fasting_phase(
    now_time: time,
    window_start: str,
    window_end: str,
) -> FastingPhase:
    """Evaluate live fasting phase against window bounds in local wall-clock time."""
    start_bound = _parse_hhmm(window_start)
    end_bound = _parse_hhmm(window_end)
    local_now = now_time.replace(tzinfo=None)
    if local_now < start_bound:
        return "fasting"
    if local_now < end_bound:
        return "eating"
    return "closed"


def _is_finite_number(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


def _baseline_suggestion(phase: FastingPhase, reason: str) -> FastingSuggestion:
    return FastingSuggestion(
        protocol=BASELINE_PROTOCOL,
        window_start=BASELINE_WINDOW_START,
        window_end=BASELINE_WINDOW_END,
        phase=phase,
        is_optional_reset=False,
        reason=reason,
    )


def _check_friday_rest_day(
    target_date: date,
    kratos_snapshot: KratosDaySnapshot | None,
) -> str | None:
    if kratos_snapshot is None or not kratos_snapshot.status.available:
        return "Standard 14:10 baseline (Kratos schedule unavailable)."
    matched = match_planned_day(target_date, kratos_snapshot.planned_days)
    if matched is None:
        return None
    workout_type = (matched.workout_type or "").strip().lower()
    if workout_type == "rest":
        return None
    return "Standard 14:10 baseline (training scheduled today)."


def _check_green_recovery_vitals(
    eir_vitals: EirDailyVitalsSnapshot | None,
) -> str | None:
    if eir_vitals is None or not eir_vitals.status.available:
        return "Standard 14:10 baseline (Eir recovery vitals unavailable)."
    effective_hrv = (
        eir_vitals.overnight_hrv_ms
        if eir_vitals.overnight_hrv_ms is not None
        else eir_vitals.hrv_ms
    )
    if not _is_finite_number(eir_vitals.sleep_duration_hours) or not _is_finite_number(
        effective_hrv
    ):
        return "Standard 14:10 baseline (incomplete sleep or HRV vitals)."
    assert eir_vitals.sleep_duration_hours is not None
    assert effective_hrv is not None
    if eir_vitals.sleep_duration_hours < MIN_GREEN_SLEEP_HOURS:
        return "Standard 14:10 baseline (sleep duration below 7.0h green threshold)."
    if effective_hrv < MIN_GREEN_HRV_MS:
        return "Standard 14:10 baseline (HRV below 45ms green threshold)."
    if eir_vitals.sleep_deep_hours is not None and (
        not _is_finite_number(eir_vitals.sleep_deep_hours)
        or eir_vitals.sleep_deep_hours < MIN_GREEN_DEEP_SLEEP_HOURS
    ):
        return "Standard 14:10 baseline (deep sleep below 0.6h green threshold)."
    if (
        eir_vitals.resting_hr_bpm is not None
        and eir_vitals.resting_hr_bpm >= MAX_GREEN_RESTING_HR_BPM
    ):
        return "Standard 14:10 baseline (resting HR at or above 78 bpm)."
    return None


def evaluate_fasting_suggestion(
    target_date: date,
    now_time: time,
    *,
    kratos_snapshot: KratosDaySnapshot | None = None,
    eir_vitals: EirDailyVitalsSnapshot | None = None,
) -> FastingSuggestion:
    """Resolve daily fasting protocol (14:10 baseline or optional Friday 18:6 reset)."""
    if target_date.weekday() != FRIDAY_WEEKDAY:
        phase = evaluate_fasting_phase(now_time, BASELINE_WINDOW_START, BASELINE_WINDOW_END)
        return _baseline_suggestion(phase, "Standard 14:10 baseline (non-Friday).")

    baseline_phase = evaluate_fasting_phase(
        now_time, BASELINE_WINDOW_START, BASELINE_WINDOW_END
    )
    kratos_blocker = _check_friday_rest_day(target_date, kratos_snapshot)
    if kratos_blocker is not None:
        return _baseline_suggestion(baseline_phase, kratos_blocker)

    vitals_blocker = _check_green_recovery_vitals(eir_vitals)
    if vitals_blocker is not None:
        return _baseline_suggestion(baseline_phase, vitals_blocker)

    reset_phase = evaluate_fasting_phase(now_time, RESET_WINDOW_START, RESET_WINDOW_END)
    return FastingSuggestion(
        protocol=RESET_PROTOCOL,
        window_start=RESET_WINDOW_START,
        window_end=RESET_WINDOW_END,
        phase=reset_phase,
        is_optional_reset=True,
        reason="Optional 18:6 Friday reset: rest day with green recovery vitals.",
    )
