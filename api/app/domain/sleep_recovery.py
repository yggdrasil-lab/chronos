"""Pure deterministic daytime nap recovery blocks and sleep-pressure nudge evaluator."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
import math
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from app.domain.fasting import EirDailyVitalsSnapshot

MINUTES_PER_DAY = 1440
LATE_NAP_END_MINUTE = 900  # 15:00
LONG_NAP_HOURS = 1.5  # 90m
SLEEP_PRESSURE_SUGGESTION = (
    "Keep 22:00 screens-off calm, avoid forcing early sleep, and dim lights until drowsy."
)
DEFAULT_TIMEZONE = ZoneInfo("Pacific/Auckland")


@dataclass(frozen=True)
class EirNapRecord:
    """Frozen individual daytime nap record from Eir peer API."""

    start_at: str
    end_at: str
    duration_hours: float


@dataclass(frozen=True)
class NapRecoveryBlock:
    """Non-carving daytime nap recovery block mapped to local day minutes."""

    start_at: str
    end_at: str
    start_minute: int
    end_minute: int
    duration_hours: float


@dataclass(frozen=True)
class SleepPressureNudge:
    """Deterministic evening sleep-pressure guidance derived from daytime naps."""

    triggered: bool
    reason: str
    suggestion: str
    latest_nap_end: str
    total_nap_hours: float
    nap_count: int


def _parse_iso_datetime(value: str, tz: ZoneInfo) -> datetime | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    normalized = f"{cleaned[:-1]}+00:00" if cleaned.endswith("Z") else cleaned
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def _format_hhmm(minute_of_day: int) -> str:
    hours, minutes = divmod(minute_of_day, 60)
    return f"{hours:02d}:{minutes:02d}"


def build_nap_recovery_blocks(
    vitals: EirDailyVitalsSnapshot | None,
    tz: ZoneInfo = DEFAULT_TIMEZONE,
) -> tuple[NapRecoveryBlock, ...]:
    """Convert Eir nap ISO timestamps into sorted non-carving NapRecoveryBlocks."""
    if vitals is None or not vitals.status.available or not vitals.naps:
        return ()

    blocks: list[NapRecoveryBlock] = []
    for nap in vitals.naps:
        if not math.isfinite(nap.duration_hours) or nap.duration_hours <= 0:
            continue
        start_dt = _parse_iso_datetime(nap.start_at, tz)
        end_dt = _parse_iso_datetime(nap.end_at, tz)
        if start_dt is None or end_dt is None or end_dt <= start_dt:
            continue
        start_minute = max(0, min(MINUTES_PER_DAY, start_dt.hour * 60 + start_dt.minute))
        raw_end_minute = (
            MINUTES_PER_DAY
            if end_dt.date() > start_dt.date()
            else end_dt.hour * 60 + end_dt.minute
        )
        end_minute = max(0, min(MINUTES_PER_DAY, raw_end_minute))
        if end_minute <= start_minute:
            continue
        blocks.append(
            NapRecoveryBlock(
                start_at=start_dt.isoformat(),
                end_at=end_dt.isoformat(),
                start_minute=start_minute,
                end_minute=end_minute,
                duration_hours=float(nap.duration_hours),
            )
        )
    blocks.sort(key=lambda b: (b.start_minute, b.end_minute))
    return tuple(blocks)


def evaluate_sleep_pressure_nudge(
    naps: Sequence[NapRecoveryBlock],
    nap_duration_hours: float | None = None,
) -> SleepPressureNudge | None:
    """Evaluate evening sleep-pressure guidance from daytime naps (None when below thresholds)."""
    if not naps:
        return None

    max_end_minute = max(n.end_minute for n in naps)
    if (
        nap_duration_hours is not None
        and math.isfinite(nap_duration_hours)
        and nap_duration_hours > 0
    ):
        total_nap_hours = round(nap_duration_hours, 2)
    else:
        total_nap_hours = round(sum(n.duration_hours for n in naps), 2)

    is_late = max_end_minute >= LATE_NAP_END_MINUTE
    is_long = total_nap_hours >= LONG_NAP_HOURS
    if not is_late and not is_long:
        return None

    latest_nap_end = _format_hhmm(max_end_minute)
    if is_late and is_long:
        reason = f"Late {total_nap_hours:.1f}h nap ending {latest_nap_end} may delay evening sleep onset."
    elif is_late:
        reason = f"Late nap ending {latest_nap_end} may lower evening sleep pressure."
    else:
        reason = f"Extended {total_nap_hours:.1f}h nap (ended {latest_nap_end}) may lower evening sleep pressure."

    return SleepPressureNudge(
        triggered=True,
        reason=reason,
        suggestion=SLEEP_PRESSURE_SUGGESTION,
        latest_nap_end=latest_nap_end,
        total_nap_hours=total_nap_hours,
        nap_count=len(naps),
    )
