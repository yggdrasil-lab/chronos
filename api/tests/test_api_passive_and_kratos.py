"""API integration tests for passive habit signals and Kratos workout hydration."""

from collections.abc import Generator
from datetime import date

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base, get_db
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
from app.main import app
from app.routers.deps import get_eir_gateway, get_kratos_gateway, get_plutus_gateway


class _StubEirGateway:
    def __init__(self, snapshot: EirDaySnapshot) -> None:
        self._snapshot = snapshot

    def fetch_day_snapshot(self, target_date: date) -> EirDaySnapshot:
        del target_date
        return self._snapshot


class _StubKratosGateway:
    def __init__(self, snapshot: KratosDaySnapshot) -> None:
        self._snapshot = snapshot

    def fetch_day_snapshot(self, target_date: date) -> KratosDaySnapshot:
        del target_date
        return self._snapshot


class _StubPlutusGateway:
    def __init__(self, snapshot: PlutusCheckinSnapshot) -> None:
        self._snapshot = snapshot

    def fetch_checkin_snapshot(self, target_date: date) -> PlutusCheckinSnapshot:
        del target_date
        return self._snapshot


class _TimeoutPlutusGateway:
    def fetch_checkin_snapshot(self, target_date: date) -> PlutusCheckinSnapshot:
        del target_date
        raise httpx.ReadTimeout("plutus timed out")


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
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

    app.dependency_overrides[get_db] = _override_get_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_get_passive_signals_endpoint_with_healthy_and_degraded_peers(client: TestClient) -> None:
    app.dependency_overrides[get_eir_gateway] = lambda: _StubEirGateway(
        EirDaySnapshot(
            status=PeerFetchStatus(available=True),
            latest_measured_at="2026-11-23T07:12:00+13:00",
            latest_weight_kg="76.5",
            detected_workouts=(
                EirDetectedWorkout(
                    started_at="2026-11-23T06:15:00+13:00",
                    ended_at="2026-11-23T07:00:00+13:00",
                    duration_minutes=45,
                    classification="walk",
                ),
            ),
        )
    )
    app.dependency_overrides[get_kratos_gateway] = lambda: _StubKratosGateway(
        KratosDaySnapshot(
            status=PeerFetchStatus(available=True),
            sessions=(
                KratosSessionSnapshot(
                    session_id="k-1",
                    routine_name="Upper A",
                    started_at="2026-11-23T17:30:00+13:00",
                    ended_at="2026-11-23T18:15:00+13:00",
                ),
            ),
        )
    )
    app.dependency_overrides[get_plutus_gateway] = lambda: _StubPlutusGateway(
        PlutusCheckinSnapshot(
            status=PeerFetchStatus(available=True),
            accounts=(
                PlutusAccountSnapshot(
                    account_id="1",
                    last_synced_at="2026-11-22T10:00:00+13:00",
                ),
            ),
            general_category_count=0,
            needs_review_count=0,
        )
    )

    resp = client.get("/cues/passive-signals", params={"target_date": "2026-11-23"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["target_date"] == "2026-11-23"
    assert body["degraded_peers"] == []
    assert body["signals"]["eir_morning_weigh_in"]["observed"] is True
    assert body["signals"]["eir_morning_weigh_in"]["fact_label"] == "Weighed in 07:12"
    assert body["signals"]["eir_fasted_walk_40m"]["observed"] is True
    assert body["signals"]["kratos_workout_completed"]["observed"] is True
    assert body["signals"]["plutus_monthly_checkin"]["observed"] is True

    # Timeout in Plutus degrades gracefully to 200 with degraded_peers=["plutus"]
    app.dependency_overrides[get_plutus_gateway] = _TimeoutPlutusGateway
    degraded_resp = client.get("/cues/passive-signals", params={"target_date": "2026-11-23"})
    assert degraded_resp.status_code == 200
    degraded_body = degraded_resp.json()
    assert degraded_body["degraded_peers"] == ["plutus"]
    assert degraded_body["signals"]["plutus_monthly_checkin"] == {
        "rule_key": "plutus_monthly_checkin",
        "label": "Monthly Financial Check-in (Plutus)",
        "observed": False,
        "fact_label": None,
        "observed_at": None,
        "source": "plutus",
        "peer_available": False,
    }
    assert degraded_body["signals"]["eir_morning_weigh_in"]["observed"] is True


def test_passive_signals_with_unset_dev_peer_urls_degrades_gracefully(client: TestClient) -> None:
    resp = client.get("/cues/passive-signals", params={"target_date": "2026-11-23"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["degraded_peers"] == ["eir", "kratos", "plutus"]
    assert all(sig["peer_available"] is False for sig in body["signals"].values())


def test_schedule_day_and_now_next_kratos_workout_hydration(client: TestClient) -> None:
    created = client.post(
        "/weekly-grid/blocks",
        json={
            "title": "Evening Training",
            "day_of_week": 0,
            "start_minute": 1020,
            "end_minute": 1140,
            "category": "workout",
            "nudge_mode": "boundary",
        },
    )
    assert created.status_code == 201
    block_id = created.json()["id"]

    app.dependency_overrides[get_kratos_gateway] = lambda: _StubKratosGateway(
        KratosDaySnapshot(
            status=PeerFetchStatus(available=True),
            planned_days=(
                KratosPlannedDay(
                    day="Mon",
                    day_index=0,
                    session_name="Upper A",
                    workout_type="strength",
                    emphasis="Back & Chest",
                    notes="Warm up shoulders",
                ),
            ),
            sessions=(
                KratosSessionSnapshot(
                    session_id="sess-77",
                    routine_name="Upper A",
                    activity_type="strength",
                    started_at="2026-11-23T17:30:00+13:00",
                    ended_at="2026-11-23T18:30:00+13:00",
                ),
            ),
        )
    )

    day_resp = client.get("/schedule/day", params={"target_date": "2026-11-23"})
    assert day_resp.status_code == 200
    day_data = day_resp.json()
    assert day_data["fasting_suggestion"]["protocol"] == "14:10"
    assert day_data["fasting_suggestion"]["window_start"] == "10:30"
    assert day_data["fasting_suggestion"]["window_end"] == "20:30"
    assert day_data["fasting_suggestion"]["is_optional_reset"] is False
    hydration = day_data["workout_hydration"]
    assert hydration["peer_degraded"] is False
    assert hydration["planned_workout"]["placement"] == "grid_block"
    assert hydration["planned_workout"]["attached_grid_block_id"] == block_id
    assert len(hydration["completed_sessions"]) == 1
    assert hydration["completed_sessions"][0]["external_ref"] == "sess-77"

    kratos_commitments = [c for c in day_data["timed_commitments"] if c["source"] == "kratos"]
    assert len(kratos_commitments) == 1
    assert kratos_commitments[0]["id"] == -1
    assert kratos_commitments[0]["read_only"] is True
    assert kratos_commitments[0]["nudge_mode"] == "none"

    grid_slices = [s for s in day_data["slices"] if s["kind"] == "grid_block"]
    assert len(grid_slices) == 2
    assert grid_slices[0]["hydrated_workout"]["title"] == "Upper A"

    now_resp = client.get(
        "/schedule/now-next",
        params={"target_date": "2026-11-23", "now_minute": 1030},
    )
    assert now_resp.status_code == 200
    now_data = now_resp.json()
    assert now_data["active_slice"]["hydrated_workout"]["title"] == "Upper A"
    assert now_data["workout_hydration"]["planned_workout"]["attached_grid_block_id"] == block_id
    assert now_data["fasting_suggestion"]["protocol"] == "14:10"
    assert now_data["fasting_suggestion"]["phase"] == "eating"

    # Peer degradation still returns 200 with peer_degraded=True
    app.dependency_overrides[get_kratos_gateway] = lambda: _StubKratosGateway(
        KratosDaySnapshot(status=PeerFetchStatus(available=False, error="down"))
    )
    degraded_day = client.get("/schedule/day", params={"target_date": "2026-11-23"})
    assert degraded_day.status_code == 200
    assert degraded_day.json()["workout_hydration"] == {
        "planned_workout": None,
        "completed_sessions": [],
        "peer_degraded": True,
    }
