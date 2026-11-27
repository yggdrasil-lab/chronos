"""HTTP peer gateways for internal aether-net Eir, Kratos, and Plutus APIs."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
import logging
import math
from typing import Any, Protocol

import httpx

from app.domain.fasting import EirDailyVitalsSnapshot
from app.domain.sleep_recovery import EirNapRecord
from app.domain.passive_signals import (
    EirDaySnapshot,
    EirDetectedWorkout,
    KratosDaySnapshot,
    KratosPlannedDay,
    KratosSessionSnapshot,
    PeerFetchStatus,
    PlutusAccountSnapshot,
    PlutusCheckinSnapshot,
)

logger = logging.getLogger(__name__)


class EirGateway(Protocol):
    """Port for fetching Eir daily biometric and workout snapshots."""

    def fetch_day_snapshot(self, target_date: date) -> EirDaySnapshot: ...


class EirVitalsGateway(Protocol):
    """Narrow port for fetching Eir daily recovery vitals."""

    def fetch_daily_vitals(self, target_date: date) -> EirDailyVitalsSnapshot: ...


class KratosGateway(Protocol):
    """Port for fetching Kratos weekly plan and session snapshots."""

    def fetch_day_snapshot(self, target_date: date) -> KratosDaySnapshot: ...


class PlutusGateway(Protocol):
    """Port for fetching Plutus monthly financial check-in snapshots."""

    def fetch_checkin_snapshot(self, target_date: date) -> PlutusCheckinSnapshot: ...


@contextmanager
def _client_scope(
    base_url: str, timeout_seconds: float, client: httpx.Client | None
) -> Iterator[httpx.Client]:
    if client is not None:
        yield client
        return
    with httpx.Client(base_url=base_url, timeout=timeout_seconds) as managed:
        yield managed


def _get_json(client: httpx.Client, path: str, *, allow_404: bool = False) -> Any:
    response = client.get(path)
    if allow_404 and response.status_code == 404:
        return None
    if response.status_code != 200:
        raise ValueError(f"Unexpected status {response.status_code} from {path}")
    return response.json()


def _coerce_optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _coerce_optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value)) if math.isfinite(value) else None
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        try:
            return int(cleaned)
        except ValueError:
            parsed = _coerce_optional_float(cleaned)
            return int(round(parsed)) if parsed is not None else None
    return None


def _parse_eir_naps(raw_naps: Any) -> tuple[EirNapRecord, ...]:
    if not isinstance(raw_naps, list):
        return ()
    parsed: list[EirNapRecord] = []
    for item in raw_naps:
        if not isinstance(item, dict):
            continue
        start_at = item.get("start_at")
        end_at = item.get("end_at")
        if not isinstance(start_at, str) or not start_at.strip():
            continue
        if not isinstance(end_at, str) or not end_at.strip():
            continue
        duration_hours = _coerce_optional_float(item.get("duration_hours"))
        if duration_hours is None or duration_hours <= 0:
            continue
        parsed.append(
            EirNapRecord(
                start_at=start_at.strip(),
                end_at=end_at.strip(),
                duration_hours=duration_hours,
            )
        )
    return tuple(parsed)


class HttpEirGateway:
    """httpx-backed gateway for Eir internal aether-net endpoints."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = 2.5,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.strip().rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._client = client

    def fetch_day_snapshot(self, target_date: date) -> EirDaySnapshot:
        if not self._base_url and self._client is None:
            return EirDaySnapshot(status=PeerFetchStatus(available=False, error="Eir URL unset"))
        try:
            with _client_scope(self._base_url, self._timeout_seconds, self._client) as client:
                latest_payload = _get_json(client, "/biometrics/latest?source=withings")
                workouts_payload = _get_json(
                    client, f"/biometrics/detected-workouts?target_date={target_date.isoformat()}"
                )
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            logger.warning("Eir peer fetch degraded: %s", exc)
            return EirDaySnapshot(status=PeerFetchStatus(available=False, error=str(exc)))

        latest_measured_at: str | None = None
        latest_weight_kg: Any = None
        if isinstance(latest_payload, dict):
            latest_measured_at = latest_payload.get("measured_at")
            latest_weight_kg = latest_payload.get("weight_kg")
        elif latest_payload is not None:
            return EirDaySnapshot(
                status=PeerFetchStatus(available=False, error="Invalid Eir latest payload")
            )

        if not isinstance(workouts_payload, list):
            return EirDaySnapshot(
                status=PeerFetchStatus(available=False, error="Invalid Eir workouts payload")
            )

        detected = tuple(
            EirDetectedWorkout(
                started_at=item.get("started_at"),
                ended_at=item.get("ended_at"),
                duration_minutes=item.get("duration_minutes"),
                activity_type=item.get("activity_type"),
                classification=item.get("classification"),
            )
            for item in workouts_payload
            if isinstance(item, dict)
        )
        return EirDaySnapshot(
            status=PeerFetchStatus(available=True),
            latest_measured_at=latest_measured_at,
            latest_weight_kg=latest_weight_kg,
            detected_workouts=detected,
        )

    def fetch_daily_vitals(self, target_date: date) -> EirDailyVitalsSnapshot:
        if not self._base_url and self._client is None:
            return EirDailyVitalsSnapshot(
                status=PeerFetchStatus(available=False, error="Eir URL unset")
            )
        try:
            with _client_scope(self._base_url, self._timeout_seconds, self._client) as client:
                payload = _get_json(
                    client,
                    f"/biometrics/daily/{target_date.isoformat()}",
                    allow_404=True,
                )
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            logger.warning("Eir daily vitals fetch degraded: %s", exc)
            return EirDailyVitalsSnapshot(status=PeerFetchStatus(available=False, error=str(exc)))

        if payload is None:
            return EirDailyVitalsSnapshot(status=PeerFetchStatus(available=True))
        if not isinstance(payload, dict):
            return EirDailyVitalsSnapshot(
                status=PeerFetchStatus(available=False, error="Invalid Eir daily vitals payload")
            )

        naps = _parse_eir_naps(payload.get("naps"))
        nap_duration_hours = _coerce_optional_float(payload.get("nap_duration_hours"))
        if nap_duration_hours is not None and nap_duration_hours < 0:
            nap_duration_hours = None
        raw_nap_count = _coerce_optional_int(payload.get("nap_count"))
        nap_count = (
            raw_nap_count if raw_nap_count is not None and raw_nap_count >= 0 else len(naps)
        )

        return EirDailyVitalsSnapshot(
            status=PeerFetchStatus(available=True),
            sleep_duration_hours=_coerce_optional_float(payload.get("sleep_duration_hours")),
            sleep_deep_hours=_coerce_optional_float(payload.get("sleep_deep_hours")),
            hrv_ms=_coerce_optional_float(payload.get("hrv_ms")),
            overnight_hrv_ms=_coerce_optional_float(payload.get("overnight_hrv_ms")),
            resting_hr_bpm=_coerce_optional_int(payload.get("resting_hr_bpm")),
            nap_duration_hours=nap_duration_hours,
            nap_count=nap_count,
            naps=naps,
        )


def _parse_kratos_planned_days(plan_payload: Any) -> tuple[KratosPlannedDay, ...]:
    if plan_payload is None:
        return ()
    if not isinstance(plan_payload, dict):
        raise ValueError("Invalid Kratos weekly plan payload")
    raw_days = plan_payload.get("days") or []
    if not isinstance(raw_days, list):
        raise ValueError("Invalid Kratos weekly plan days")
    return tuple(
        KratosPlannedDay(
            day=item.get("day"),
            day_index=item.get("dayIndex"),
            session_name=item.get("sessionName"),
            activity=item.get("activity"),
            workout_type=item.get("type"),
            emphasis=item.get("emphasis"),
            notes=item.get("notes"),
        )
        for item in raw_days
        if isinstance(item, dict)
    )


def _parse_kratos_sessions(sessions_payload: Any) -> tuple[KratosSessionSnapshot, ...]:
    if not isinstance(sessions_payload, list):
        raise ValueError("Invalid Kratos sessions payload")
    parsed: list[KratosSessionSnapshot] = []
    for item in sessions_payload:
        if not isinstance(item, dict) or item.get("id") is None:
            continue
        extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
        parsed.append(
            KratosSessionSnapshot(
                session_id=str(item["id"]),
                routine_id=item.get("routine_id"),
                routine_name=extra.get("routine_name"),
                activity_type=extra.get("activity_type"),
                classification=extra.get("classification"),
                started_at=item.get("started_at"),
                ended_at=item.get("ended_at"),
                deleted_at=item.get("deleted_at"),
                notes=item.get("notes"),
            )
        )
    return tuple(parsed)


def _fetch_optional_routines(client: httpx.Client) -> tuple[tuple[str, str], ...]:
    try:
        routines_payload = _get_json(client, "/routines")
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        logger.warning("Kratos optional /routines lookup skipped: %s", exc)
        return ()
    if not isinstance(routines_payload, list):
        return ()
    pairs: list[tuple[str, str]] = []
    for item in routines_payload:
        if isinstance(item, dict) and item.get("id") is not None and item.get("name"):
            pairs.append((str(item["id"]), str(item["name"])))
    return tuple(pairs)


class HttpKratosGateway:
    """httpx-backed gateway for Kratos internal aether-net endpoints."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = 2.5,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.strip().rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._client = client

    def fetch_day_snapshot(self, target_date: date) -> KratosDaySnapshot:
        if not self._base_url and self._client is None:
            return KratosDaySnapshot(
                status=PeerFetchStatus(available=False, error="Kratos URL unset")
            )
        iso_year, iso_week, _ = target_date.isocalendar()
        week_key = f"{iso_year}-W{iso_week:02d}"
        try:
            with _client_scope(self._base_url, self._timeout_seconds, self._client) as client:
                plan_payload = _get_json(client, f"/plans/week/{week_key}", allow_404=True)
                sessions_payload = _get_json(client, "/sessions?limit=50&include_deleted=false")
                planned_days = _parse_kratos_planned_days(plan_payload)
                sessions = _parse_kratos_sessions(sessions_payload)
                needs_routines = any(
                    s.routine_id is not None and not (s.routine_name or "").strip()
                    for s in sessions
                )
                routine_names = _fetch_optional_routines(client) if needs_routines else ()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            logger.warning("Kratos peer fetch degraded: %s", exc)
            return KratosDaySnapshot(status=PeerFetchStatus(available=False, error=str(exc)))

        return KratosDaySnapshot(
            status=PeerFetchStatus(available=True),
            planned_days=planned_days,
            sessions=sessions,
            routine_names_by_id=routine_names,
        )


def _fetch_plutus_account_snapshot(
    client: httpx.Client, account: dict[str, Any]
) -> PlutusAccountSnapshot:
    account_id = str(account["id"])
    snapshots = _get_json(client, f"/accounts/{account_id}/snapshots?limit=1")
    if not isinstance(snapshots, list):
        raise ValueError(f"Invalid Plutus snapshots payload for account {account_id}")
    latest = snapshots[0] if snapshots and isinstance(snapshots[0], dict) else {}
    return PlutusAccountSnapshot(
        account_id=account_id,
        name=account.get("name"),
        last_synced_at=account.get("last_synced_at"),
        updated_at=account.get("updated_at"),
        latest_snapshot_date=latest.get("snapshot_date"),
        latest_snapshot_created_at=latest.get("created_at"),
    )


class HttpPlutusGateway:
    """httpx-backed gateway for Plutus internal aether-net endpoints."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = 2.5,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.strip().rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._client = client

    def fetch_checkin_snapshot(self, target_date: date) -> PlutusCheckinSnapshot:
        del target_date
        if not self._base_url and self._client is None:
            return PlutusCheckinSnapshot(
                status=PeerFetchStatus(available=False, error="Plutus URL unset")
            )
        try:
            with _client_scope(self._base_url, self._timeout_seconds, self._client) as client:
                accounts_payload = _get_json(client, "/accounts?is_active=true")
                if not isinstance(accounts_payload, list):
                    raise ValueError("Invalid Plutus accounts payload")
                accounts = tuple(
                    _fetch_plutus_account_snapshot(client, item)
                    for item in accounts_payload
                    if isinstance(item, dict) and item.get("id") is not None
                )
                tx_resp = client.get("/transactions?category=General&limit=1")
                if tx_resp.status_code != 200:
                    raise ValueError(f"Unexpected status {tx_resp.status_code} from /transactions")
                raw_total = tx_resp.headers.get("X-Total-Count")
                if raw_total is None:
                    raise ValueError("Missing X-Total-Count header from Plutus /transactions")
                general_count = int(raw_total)
                receipts_payload = _get_json(client, "/receipts?status=needs_review&limit=200")
                if not isinstance(receipts_payload, list):
                    raise ValueError("Invalid Plutus receipts payload")
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            logger.warning("Plutus peer fetch degraded: %s", exc)
            return PlutusCheckinSnapshot(status=PeerFetchStatus(available=False, error=str(exc)))

        return PlutusCheckinSnapshot(
            status=PeerFetchStatus(available=True),
            accounts=accounts,
            general_category_count=general_count,
            needs_review_count=len(receipts_payload),
        )
