"""Unit and integration tests for Chronos deterministic fasting suggestion engine."""

from collections.abc import Generator
from datetime import date, time
import json
import threading
from typing import Any

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base, get_db
from app.domain.fasting import (
    EirDailyVitalsSnapshot,
    evaluate_fasting_phase,
    evaluate_fasting_suggestion,
)
from app.domain.models import DaySchedule
from app.domain.passive_signals import (
    KratosDaySnapshot,
    KratosPlannedDay,
    PeerFetchStatus,
)
from app.domain.schedule_engine import compute_now_next
from app.main import app
from app.repositories.sqlalchemy_schedule_repo import SqlAlchemyScheduleRepository
from app.routers import deps
from app.services.peer_gateways import HttpEirGateway
from app.services.schedule_service import ScheduleService

TARGET_THURSDAY = date(2026, 11, 26)
TARGET_FRIDAY = date(2026, 11, 27)
TARGET_SATURDAY = date(2026, 11, 28)

GREEN_VITALS = EirDailyVitalsSnapshot(
    status=PeerFetchStatus(available=True),
    sleep_duration_hours=7.5,
    sleep_deep_hours=1.1,
    hrv_ms=54.0,
    resting_hr_bpm=58,
)

FRIDAY_REST_KRATOS = KratosDaySnapshot(
    status=PeerFetchStatus(available=True),
    planned_days=(
        KratosPlannedDay(day="Thu", day_index=3, session_name="Upper A", workout_type="strength"),
        KratosPlannedDay(day="Fri", day_index=4, session_name="Rest", workout_type="Rest"),
    ),
)


class _RecordingKratosGateway:
    def __init__(self, snapshot: KratosDaySnapshot, calls: list[date] | None = None) -> None:
        self._snapshot = snapshot
        self._calls = calls

    def fetch_day_snapshot(self, target_date: date) -> KratosDaySnapshot:
        if self._calls is not None:
            self._calls.append(target_date)
        return self._snapshot


class _RecordingEirVitalsGateway:
    def __init__(self, snapshot: EirDailyVitalsSnapshot, calls: list[date] | None = None) -> None:
        self._snapshot = snapshot
        self._calls = calls

    def fetch_daily_vitals(self, target_date: date) -> EirDailyVitalsSnapshot:
        if self._calls is not None:
            self._calls.append(target_date)
        return self._snapshot


class _RaisingEirVitalsGateway:
    def fetch_daily_vitals(self, target_date: date) -> EirDailyVitalsSnapshot:
        del target_date
        raise httpx.ReadTimeout("eir vitals timed out")


@pytest.fixture()
def api_client(monkeypatch) -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_sqlite_fk(dbapi_conn, _conn_record) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def _override_get_db() -> Generator[Session, None, None]:
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(deps.settings, "environment", "production")
    monkeypatch.setattr(deps.settings, "mcp_token", "synthetic-mcp-token")
    app.dependency_overrides[get_db] = _override_get_db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_non_friday_always_returns_14_10_baseline() -> None:
    for non_friday in (date(2026, 11, 23), TARGET_THURSDAY, TARGET_SATURDAY, date(2026, 11, 29)):
        suggestion = evaluate_fasting_suggestion(
            non_friday,
            time(11, 0),
            kratos_snapshot=KratosDaySnapshot(status=PeerFetchStatus(available=True)),
            eir_vitals=GREEN_VITALS,
        )
        assert suggestion.protocol == "14:10"
        assert suggestion.window_start == "10:30"
        assert suggestion.window_end == "20:30"
        assert suggestion.phase == "eating"
        assert suggestion.is_optional_reset is False


def test_friday_rest_with_green_vitals_triggers_18_6() -> None:
    suggestion = evaluate_fasting_suggestion(
        TARGET_FRIDAY,
        time(13, 0),
        kratos_snapshot=FRIDAY_REST_KRATOS,
        eir_vitals=GREEN_VITALS,
    )
    assert suggestion.protocol == "18:6"
    assert suggestion.window_start == "14:30"
    assert suggestion.window_end == "20:30"
    assert suggestion.phase == "fasting"
    assert suggestion.is_optional_reset is True

    # Missing Friday entry in Kratos planned_days is also treated as a rest day
    unscheduled_friday = KratosDaySnapshot(
        status=PeerFetchStatus(available=True),
        planned_days=(KratosPlannedDay(day="Mon", day_index=0, workout_type="strength"),),
    )
    suggestion_unscheduled = evaluate_fasting_suggestion(
        TARGET_FRIDAY,
        time(15, 0),
        kratos_snapshot=unscheduled_friday,
        eir_vitals=GREEN_VITALS,
    )
    assert suggestion_unscheduled.protocol == "18:6"
    assert suggestion_unscheduled.phase == "eating"
    assert suggestion_unscheduled.is_optional_reset is True


@pytest.mark.parametrize("workout_type", ["lift", "strength", "sport", "class", "cardio"])
def test_friday_scheduled_training_falls_back_to_14_10(workout_type: str) -> None:
    kratos = KratosDaySnapshot(
        status=PeerFetchStatus(available=True),
        planned_days=(
            KratosPlannedDay(day="Fri", day_index=4, session_name="Training", workout_type=workout_type),
        ),
    )
    suggestion = evaluate_fasting_suggestion(
        TARGET_FRIDAY,
        time(12, 0),
        kratos_snapshot=kratos,
        eir_vitals=GREEN_VITALS,
    )
    assert suggestion.protocol == "14:10"
    assert suggestion.is_optional_reset is False


def test_friday_vitals_thresholds_and_peer_degradation() -> None:
    exact_green = EirDailyVitalsSnapshot(
        status=PeerFetchStatus(available=True),
        sleep_duration_hours=7.0,
        sleep_deep_hours=0.6,
        hrv_ms=45.0,
        resting_hr_bpm=77,
    )
    assert (
        evaluate_fasting_suggestion(
            TARGET_FRIDAY,
            time(9, 0),
            kratos_snapshot=FRIDAY_REST_KRATOS,
            eir_vitals=exact_green,
        ).protocol
        == "18:6"
    )

    # Optional deep sleep / resting HR omitted still qualifies when sleep and HRV are green
    minimal_green = EirDailyVitalsSnapshot(
        status=PeerFetchStatus(available=True),
        sleep_duration_hours=7.2,
        hrv_ms=48.0,
    )
    assert (
        evaluate_fasting_suggestion(
            TARGET_FRIDAY,
            time(9, 0),
            kratos_snapshot=FRIDAY_REST_KRATOS,
            eir_vitals=minimal_green,
        ).protocol
        == "18:6"
    )

    non_qualifying = (
        EirDailyVitalsSnapshot(status=PeerFetchStatus(available=False, error="timeout")),
        EirDailyVitalsSnapshot(status=PeerFetchStatus(available=True), sleep_duration_hours=None, hrv_ms=55.0),
        EirDailyVitalsSnapshot(status=PeerFetchStatus(available=True), sleep_duration_hours=7.5, hrv_ms=None),
        EirDailyVitalsSnapshot(status=PeerFetchStatus(available=True), sleep_duration_hours=6.99, hrv_ms=55.0),
        EirDailyVitalsSnapshot(status=PeerFetchStatus(available=True), sleep_duration_hours=7.5, hrv_ms=44.9),
        EirDailyVitalsSnapshot(status=PeerFetchStatus(available=True), sleep_duration_hours=7.5, hrv_ms=55.0, sleep_deep_hours=0.59),
        EirDailyVitalsSnapshot(status=PeerFetchStatus(available=True), sleep_duration_hours=7.5, hrv_ms=55.0, resting_hr_bpm=78),
        EirDailyVitalsSnapshot(status=PeerFetchStatus(available=True), sleep_duration_hours=float("nan"), hrv_ms=55.0),
    )
    for vitals in non_qualifying:
        res = evaluate_fasting_suggestion(
            TARGET_FRIDAY,
            time(12, 0),
            kratos_snapshot=FRIDAY_REST_KRATOS,
            eir_vitals=vitals,
        )
        assert res.protocol == "14:10"
        assert res.is_optional_reset is False

    degraded_kratos = evaluate_fasting_suggestion(
        TARGET_FRIDAY,
        time(12, 0),
        kratos_snapshot=KratosDaySnapshot(status=PeerFetchStatus(available=False, error="down")),
        eir_vitals=GREEN_VITALS,
    )
    assert degraded_kratos.protocol == "14:10"
    assert degraded_kratos.is_optional_reset is False


def test_fasting_phase_boundaries_and_now_next_end_of_day() -> None:
    assert evaluate_fasting_phase(time(10, 29), "10:30", "20:30") == "fasting"
    assert evaluate_fasting_phase(time(10, 30), "10:30", "20:30") == "eating"
    assert evaluate_fasting_phase(time(20, 29), "10:30", "20:30") == "eating"
    assert evaluate_fasting_phase(time(20, 30), "10:30", "20:30") == "closed"

    assert evaluate_fasting_phase(time(14, 29), "14:30", "20:30") == "fasting"
    assert evaluate_fasting_phase(time(14, 30), "14:30", "20:30") == "eating"
    assert evaluate_fasting_phase(time(20, 30), "14:30", "20:30") == "closed"

    suggestion = evaluate_fasting_suggestion(
        TARGET_FRIDAY,
        time(9, 0),
        kratos_snapshot=FRIDAY_REST_KRATOS,
        eir_vitals=GREEN_VITALS,
    )
    schedule = DaySchedule(
        target_date=TARGET_FRIDAY,
        slices=(),
        all_day_commitments=(),
        timed_commitments=(),
        fasting_suggestion=suggestion,
    )
    assert compute_now_next(schedule, 1440).fasting_suggestion is not None
    assert compute_now_next(schedule, 1440).fasting_suggestion.phase == "closed"  # type: ignore[union-attr]
    assert compute_now_next(schedule, 900).fasting_suggestion.phase == "eating"  # type: ignore[union-attr]


def test_http_eir_gateway_fetch_daily_vitals_happy_404_and_degradation() -> None:
    def ok_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/biometrics/daily/2026-11-27":
            return httpx.Response(
                200,
                json={
                    "sleep_duration_hours": "7.8",
                    "sleep_deep_hours": 1.25,
                    "hrv_ms": 51,
                    "resting_hr_bpm": "56",
                },
            )
        return httpx.Response(404)

    ok = HttpEirGateway(
        base_url="http://eir-api:8000",
        client=httpx.Client(transport=httpx.MockTransport(ok_handler), base_url="http://eir-api:8000"),
    ).fetch_daily_vitals(TARGET_FRIDAY)
    assert ok.status.available is True
    assert ok.sleep_duration_hours == 7.8
    assert ok.sleep_deep_hours == 1.25
    assert ok.hrv_ms == 51.0
    assert ok.resting_hr_bpm == 56

    not_found = HttpEirGateway(
        base_url="http://eir-api:8000",
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _req: httpx.Response(404)),
            base_url="http://eir-api:8000",
        ),
    ).fetch_daily_vitals(TARGET_FRIDAY)
    assert not_found.status.available is True
    assert not_found.sleep_duration_hours is None
    assert not_found.hrv_ms is None

    bad_payload = HttpEirGateway(
        base_url="http://eir-api:8000",
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _req: httpx.Response(200, json=["unexpected"])),
            base_url="http://eir-api:8000",
        ),
    ).fetch_daily_vitals(TARGET_FRIDAY)
    assert bad_payload.status.available is False

    def timeout_handler(_req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    timed_out = HttpEirGateway(
        base_url="http://eir-api:8000",
        client=httpx.Client(transport=httpx.MockTransport(timeout_handler), base_url="http://eir-api:8000"),
    ).fetch_daily_vitals(TARGET_FRIDAY)
    assert timed_out.status.available is False
    assert HttpEirGateway(base_url="").fetch_daily_vitals(TARGET_FRIDAY).status.available is False


def test_schedule_service_skips_eir_on_non_friday_and_runs_concurrently_on_friday() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    with SessionLocal() as db:
        eir_calls: list[date] = []
        kratos_calls: list[date] = []
        service = ScheduleService(
            SqlAlchemyScheduleRepository(db),
            kratos_gateway=_RecordingKratosGateway(FRIDAY_REST_KRATOS, kratos_calls),
            eir_vitals_gateway=_RecordingEirVitalsGateway(GREEN_VITALS, eir_calls),
        )

        thursday_schedule = service.get_day_schedule(TARGET_THURSDAY)
        assert thursday_schedule.fasting_suggestion is not None
        assert thursday_schedule.fasting_suggestion.protocol == "14:10"
        assert kratos_calls == [TARGET_THURSDAY]
        assert eir_calls == []

    barrier = threading.Barrier(2, timeout=2.0)

    class _ConcurrentKratosGateway:
        def fetch_day_snapshot(self, target_date: date) -> KratosDaySnapshot:
            del target_date
            barrier.wait()
            return FRIDAY_REST_KRATOS

    class _ConcurrentEirVitalsGateway:
        def fetch_daily_vitals(self, target_date: date) -> EirDailyVitalsSnapshot:
            del target_date
            barrier.wait()
            return GREEN_VITALS

    with SessionLocal() as db:
        concurrent_service = ScheduleService(
            SqlAlchemyScheduleRepository(db),
            kratos_gateway=_ConcurrentKratosGateway(),
            eir_vitals_gateway=_ConcurrentEirVitalsGateway(),
        )
        friday_schedule = concurrent_service.get_day_schedule(TARGET_FRIDAY)
        assert friday_schedule.fasting_suggestion is not None
        assert friday_schedule.fasting_suggestion.protocol == "18:6"
        assert friday_schedule.fasting_suggestion.is_optional_reset is True

        degraded_service = ScheduleService(
            SqlAlchemyScheduleRepository(db),
            kratos_gateway=_RecordingKratosGateway(FRIDAY_REST_KRATOS),
            eir_vitals_gateway=_RaisingEirVitalsGateway(),
        )
        degraded_friday = degraded_service.get_day_schedule(TARGET_FRIDAY)
        assert degraded_friday.fasting_suggestion is not None
        assert degraded_friday.fasting_suggestion.protocol == "14:10"

    engine.dispose()


def test_api_and_mcp_expose_fasting_suggestion_for_friday_and_degraded_eir(
    api_client: TestClient,
) -> None:
    app.dependency_overrides[deps.get_kratos_gateway] = lambda: _RecordingKratosGateway(
        FRIDAY_REST_KRATOS
    )
    app.dependency_overrides[deps.get_eir_vitals_gateway] = lambda: _RecordingEirVitalsGateway(
        GREEN_VITALS
    )

    day_resp = api_client.get("/schedule/day", params={"target_date": "2026-11-27"})
    assert day_resp.status_code == 200
    day_fasting = day_resp.json()["fasting_suggestion"]
    assert day_fasting["protocol"] == "18:6"
    assert day_fasting["window_start"] == "14:30"
    assert day_fasting["window_end"] == "20:30"
    assert day_fasting["is_optional_reset"] is True

    now_fasting_resp = api_client.get(
        "/schedule/now-next",
        params={"target_date": "2026-11-27", "now_minute": 800},
    )
    assert now_fasting_resp.status_code == 200
    assert now_fasting_resp.json()["fasting_suggestion"]["protocol"] == "18:6"
    assert now_fasting_resp.json()["fasting_suggestion"]["phase"] == "fasting"

    now_eating_resp = api_client.get(
        "/schedule/now-next",
        params={"target_date": "2026-11-27", "now_minute": 900},
    )
    assert now_eating_resp.status_code == 200
    assert now_eating_resp.json()["fasting_suggestion"]["phase"] == "eating"

    summary_resp = api_client.get(
        "/integrations/daily-summary",
        params={"target_date": "2026-11-27"},
    )
    assert summary_resp.status_code == 200
    assert summary_resp.json()["fasting_suggestion"]["protocol"] == "18:6"

    mcp_headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Authorization": "Bearer synthetic-mcp-token",
    }
    mcp_resp = api_client.post(
        "/mcp",
        headers=mcp_headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "get_schedule", "arguments": {"target_date": "2026-11-27"}},
        },
    )
    assert mcp_resp.status_code == 200
    mcp_payload = _extract_mcp_tool_json(mcp_resp)
    assert mcp_payload["fasting_suggestion"]["protocol"] == "18:6"

    app.dependency_overrides[deps.get_eir_vitals_gateway] = _RaisingEirVitalsGateway
    fallback_resp = api_client.get("/schedule/day", params={"target_date": "2026-11-27"})
    assert fallback_resp.status_code == 200
    assert fallback_resp.json()["fasting_suggestion"]["protocol"] == "14:10"
    assert fallback_resp.json()["fasting_suggestion"]["is_optional_reset"] is False


def _extract_mcp_tool_json(response: Any) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in response.text.splitlines():
            if line.startswith("data:"):
                frame = json.loads(line[len("data:") :].strip())
                return json.loads(frame["result"]["content"][0]["text"])
        raise AssertionError(f"No data frame in MCP stream: {response.text!r}")
    frame = response.json()
    return json.loads(frame["result"]["content"][0]["text"])
