"""API integration tests for Chronos REST endpoints using in-memory SQLite."""

from collections.abc import Generator

from datetime import date

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base, get_db
from app.domain.fasting import EirDailyVitalsSnapshot
from app.domain.passive_signals import PeerFetchStatus
from app.domain.sleep_recovery import EirNapRecord
from app.main import app
from app.routers import deps


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


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "chronos-api"}


def test_weekly_grid_crud_and_validation(client: TestClient) -> None:
    created = client.post(
        "/weekly-grid/blocks",
        json={
            "title": "Deep Work",
            "day_of_week": 0,
            "start_minute": 540,
            "end_minute": 660,
            "category": "focus",
            "nudge_mode": "two-stage",
        },
    )
    assert created.status_code == 201
    block_id = created.json()["id"]

    listed = client.get("/weekly-grid", params={"day_of_week": 0})
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["title"] == "Deep Work"

    patched = client.patch(
        f"/weekly-grid/blocks/{block_id}",
        json={"title": "Architecture Block", "end_minute": 690},
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "Architecture Block"
    assert patched.json()["end_minute"] == 690

    invalid_day = client.post(
        "/weekly-grid/blocks",
        json={"title": "Bad", "day_of_week": 7, "start_minute": 60, "end_minute": 120},
    )
    assert invalid_day.status_code == 422

    invalid_window = client.post(
        "/weekly-grid/blocks",
        json={"title": "Bad", "day_of_week": 1, "start_minute": 300, "end_minute": 300},
    )
    assert invalid_window.status_code == 422

    missing_patch = client.patch("/weekly-grid/blocks/9999", json={"title": "Missing"})
    assert missing_patch.status_code == 404

    deleted = client.delete(f"/weekly-grid/blocks/{block_id}")
    assert deleted.status_code == 204
    assert client.delete(f"/weekly-grid/blocks/{block_id}").status_code == 404


def test_exceptions_upsert_cascade_and_validation(client: TestClient) -> None:
    block_id = client.post(
        "/weekly-grid/blocks",
        json={"title": "Morning Mobility", "day_of_week": 0, "start_minute": 420, "end_minute": 480},
    ).json()["id"]

    first = client.put(
        "/exceptions",
        json={
            "grid_block_id": block_id,
            "target_date": "2026-11-23",
            "start_minute": 450,
            "end_minute": 510,
            "extension_count": 1,
        },
    )
    assert first.status_code == 200
    exc_id = first.json()["id"]

    # Upsert same (grid_block_id, target_date) updates existing row cleanly
    second = client.put(
        "/exceptions",
        json={
            "grid_block_id": block_id,
            "target_date": "2026-11-23",
            "skipped": True,
            "extension_count": 1,
        },
    )
    assert second.status_code == 200
    assert second.json()["id"] == exc_id
    assert second.json()["skipped"] is True

    listed = client.get("/exceptions", params={"target_date": "2026-11-23"})
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    # Missing block -> 404
    missing_block = client.put(
        "/exceptions",
        json={"grid_block_id": 9999, "target_date": "2026-11-23", "skipped": True},
    )
    assert missing_block.status_code == 404

    # Deleting parent block cascades to exception
    assert client.delete(f"/weekly-grid/blocks/{block_id}").status_code == 204
    assert client.get("/exceptions", params={"target_date": "2026-11-23"}).json() == []
    assert client.delete(f"/exceptions/{exc_id}").status_code == 404


def test_commitments_and_schedule_endpoints(client: TestClient) -> None:
    client.post(
        "/weekly-grid/blocks",
        json={
            "title": "Deep Work",
            "day_of_week": 0,
            "start_minute": 540,
            "end_minute": 660,
            "category": "focus",
        },
    )

    c1 = client.post(
        "/commitments",
        json={
            "title": "Planning Sync",
            "target_date": "2026-11-23",
            "start_minute": 570,
            "end_minute": 646,
            "source": "iris",
            "external_ref": "evt-001",
            "nudge_mode": "boundary",
        },
    )
    assert c1.status_code == 201
    c1_id = c1.json()["id"]

    c2 = client.post(
        "/commitments",
        json={
            "title": "Gym Session",
            "target_date": "2026-11-23",
            "start_minute": 600,
            "end_minute": 640,
            "source": "kratos",
        },
    )
    assert c2.status_code == 201

    all_day = client.post(
        "/commitments",
        json={
            "title": "Quarterly Milestone",
            "target_date": "2026-11-23",
            "all_day": True,
        },
    )
    assert all_day.status_code == 201

    # Invalid all_day with start_minute -> 422
    bad_all_day = client.post(
        "/commitments",
        json={
            "title": "Invalid",
            "target_date": "2026-11-23",
            "all_day": True,
            "start_minute": 60,
            "end_minute": 120,
        },
    )
    assert bad_all_day.status_code == 422

    # Invalid date range -> 422
    bad_range = client.get(
        "/commitments", params={"start_date": "2026-11-25", "end_date": "2026-11-23"}
    )
    assert bad_range.status_code == 422

    day_res = client.get("/schedule/day", params={"target_date": "2026-11-23"})
    assert day_res.status_code == 200
    day_body = day_res.json()
    assert day_body["has_conflicts"] is True
    assert len(day_body["all_day_commitments"]) == 1
    assert any(s["suppressed_tail"] is True for s in day_body["slices"])

    now_next = client.get(
        "/schedule/now-next", params={"target_date": "2026-11-23", "now_minute": 610}
    )
    assert now_next.status_code == 200
    nn_body = now_next.json()
    assert len(nn_body["active_items"]) == 2
    assert nn_body["open_time_minutes"] is None
    assert nn_body["is_quiet_hours"] is False

    # Omitted query params default cleanly to Pacific/Auckland today/now
    default_nn = client.get("/schedule/now-next")
    assert default_nn.status_code == 200
    assert 0 <= default_nn.json()["now_minute"] <= 1440

    # Patch timed commitment -> all_day clears start/end cleanly
    patched = client.patch(f"/commitments/{c1_id}", json={"all_day": True})
    assert patched.status_code == 200
    assert patched.json()["all_day"] is True
    assert patched.json()["start_minute"] is None

    assert client.delete(f"/commitments/{c1_id}").status_code == 204
    assert client.delete(f"/commitments/{c1_id}").status_code == 404


def test_schedule_day_and_now_next_expose_naps_without_carving_or_conflicts(
    client: TestClient,
) -> None:
    class _NapEirVitalsGateway:
        def fetch_daily_vitals(self, target_date: date) -> EirDailyVitalsSnapshot:
            del target_date
            return EirDailyVitalsSnapshot(
                status=PeerFetchStatus(available=True),
                sleep_duration_hours=6.64,
                nap_duration_hours=2.18,
                nap_count=1,
                naps=(
                    EirNapRecord(
                        start_at="2026-11-26T16:29:00+13:00",
                        end_at="2026-11-26T18:39:00+13:00",
                        duration_hours=2.18,
                    ),
                ),
            )

    app.dependency_overrides[deps.get_eir_vitals_gateway] = _NapEirVitalsGateway
    client.post(
        "/weekly-grid/blocks",
        json={
            "title": "Deep Work",
            "day_of_week": 3,
            "start_minute": 960,
            "end_minute": 1140,
            "category": "focus",
        },
    )
    client.post(
        "/commitments",
        json={
            "title": "Evening Sync",
            "target_date": "2026-11-26",
            "start_minute": 1020,
            "end_minute": 1080,
        },
    )

    day_resp = client.get("/schedule/day", params={"target_date": "2026-11-26"})
    assert day_resp.status_code == 200
    day_data = day_resp.json()
    assert day_data["has_conflicts"] is False
    assert [(s["start_minute"], s["end_minute"]) for s in day_data["slices"]] == [
        (960, 1020),
        (1020, 1080),
        (1080, 1140),
    ]
    assert len(day_data["naps"]) == 1
    assert day_data["naps"][0]["start_minute"] == 989
    assert day_data["naps"][0]["end_minute"] == 1119
    assert day_data["naps"][0]["duration_hours"] == 2.18
    assert day_data["sleep_pressure_nudge"] == {
        "triggered": True,
        "reason": "Late 2.2h nap ending 18:39 may delay evening sleep onset.",
        "suggestion": "Keep 22:00 screens-off calm, avoid forcing early sleep, and dim lights until drowsy.",
        "latest_nap_end": "18:39",
        "total_nap_hours": 2.18,
        "nap_count": 1,
    }
    assert day_data["fasting_suggestion"]["protocol"] == "14:10"

    now_resp = client.get(
        "/schedule/now-next", params={"target_date": "2026-11-26", "now_minute": 1200}
    )
    assert now_resp.status_code == 200
    now_data = now_resp.json()
    assert now_data["sleep_pressure_nudge"] == day_data["sleep_pressure_nudge"]
