"""API integration tests for Chronos travel mode overlays and travel commitments."""

from collections.abc import Generator

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base, get_db
from app.main import app


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


def test_travel_mode_upsert_list_schedule_effect_and_idempotent_clear(client: TestClient) -> None:
    # Seed work and non-work blocks on Monday (day_of_week=0, 2026-11-23)
    client.post(
        "/weekly-grid/blocks",
        json={
            "title": "Deep Work",
            "day_of_week": 0,
            "start_minute": 540,
            "end_minute": 660,
            "category": "deep_work",
        },
    )
    client.post(
        "/weekly-grid/blocks",
        json={
            "title": "Evening Mobility",
            "day_of_week": 0,
            "start_minute": 1080,
            "end_minute": 1140,
            "category": "health",
        },
    )

    # Validation errors on active=true
    assert (
        client.post(
            "/schedule/travel-mode",
            json={
                "external_ref": "odysseus:tokyo-2027",
                "label": "Tokyo Trip",
                "start_date": None,
                "end_date": None,
                "mode": "suppress_all_grid_blocks",
                "active": True,
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/schedule/travel-mode",
            json={
                "external_ref": "odysseus:tokyo-2027",
                "label": "Tokyo Trip",
                "start_date": "2026-11-25",
                "end_date": "2026-11-23",
                "mode": "suppress_all_grid_blocks",
                "active": True,
            },
        ).status_code
        == 422
    )

    # Activate suppress_all_grid_blocks
    created = client.post(
        "/schedule/travel-mode",
        json={
            "external_ref": "odysseus:tokyo-2027",
            "label": "Tokyo Trip",
            "start_date": "2026-11-23",
            "end_date": "2026-11-26",
            "mode": "suppress_all_grid_blocks",
            "active": True,
        },
    )
    assert created.status_code == 200
    created_body = created.json()
    assert isinstance(created_body["id"], int)
    assert created_body["active"] is True
    assert created_body["mode"] == "suppress_all_grid_blocks"

    # Day schedule suppresses all grid blocks and reports active travel_overlay
    day_suppress = client.get("/schedule/day", params={"target_date": "2026-11-23"})
    assert day_suppress.status_code == 200
    assert day_suppress.json()["slices"] == []
    assert day_suppress.json()["travel_overlay"]["external_ref"] == "odysseus:tokyo-2027"

    # Upsert same external_ref to keep_work_blocks
    updated = client.post(
        "/schedule/travel-mode",
        json={
            "external_ref": "odysseus:tokyo-2027",
            "label": "Tokyo Workation",
            "start_date": "2026-11-23",
            "end_date": "2026-11-26",
            "mode": "keep_work_blocks",
            "active": True,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["id"] == created_body["id"]
    assert updated.json()["label"] == "Tokyo Workation"

    day_workation = client.get("/schedule/day", params={"target_date": "2026-11-23"})
    assert day_workation.status_code == 200
    assert [s["title"] for s in day_workation.json()["slices"]] == ["Deep Work"]
    assert day_workation.json()["travel_overlay"]["mode"] == "keep_work_blocks"

    # List endpoint filtering
    listed = client.get(
        "/schedule/travel-mode",
        params={"start_date": "2026-11-24", "end_date": "2026-11-24"},
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["external_ref"] == "odysseus:tokyo-2027"

    # Deactivate via POST active=false (idempotent)
    cleared = client.post(
        "/schedule/travel-mode",
        json={
            "external_ref": "odysseus:tokyo-2027",
            "label": "Tokyo Workation",
            "start_date": None,
            "end_date": None,
            "mode": "keep_work_blocks",
            "active": False,
        },
    )
    assert cleared.status_code == 200
    assert cleared.json() == {
        "id": None,
        "external_ref": "odysseus:tokyo-2027",
        "label": "Tokyo Workation",
        "start_date": None,
        "end_date": None,
        "mode": "keep_work_blocks",
        "active": False,
    }
    assert client.get("/schedule/travel-mode").json() == []

    # Day schedule restored after clearing overlay
    restored = client.get("/schedule/day", params={"target_date": "2026-11-23"})
    assert len(restored.json()["slices"]) == 2
    assert restored.json()["travel_overlay"] is None

    # DELETE /schedule/travel-mode/{external_ref} is idempotent
    client.post(
        "/schedule/travel-mode",
        json={
            "external_ref": "odysseus:osaka-2027",
            "label": "Osaka",
            "start_date": "2026-12-01",
            "end_date": "2026-12-02",
            "mode": "suppress_all_grid_blocks",
            "active": True,
        },
    )
    assert client.delete("/schedule/travel-mode/odysseus:osaka-2027").status_code == 204
    assert client.delete("/schedule/travel-mode/odysseus:osaka-2027").status_code == 204


def test_travel_commitments_upsert_prefix_filter_and_delete_by_ref(client: TestClient) -> None:
    # upsert_by_external_ref=true requires non-empty external_ref
    missing_ref = client.post(
        "/commitments",
        json={
            "title": "Flight JL43",
            "target_date": "2026-11-23",
            "start_minute": 540,
            "end_minute": 660,
            "source": "travel",
            "external_ref": "   ",
            "upsert_by_external_ref": True,
        },
    )
    assert missing_ref.status_code == 422

    first = client.post(
        "/commitments",
        json={
            "title": "Flight JL43",
            "target_date": "2026-11-23",
            "start_minute": 540,
            "end_minute": 660,
            "source": "travel",
            "external_ref": "odysseus:tokyo-2027:item:101",
            "urgent_quiet_bypass": True,
            "notes": "Haneda T3",
            "upsert_by_external_ref": True,
        },
    )
    assert first.status_code == 201
    first_id = first.json()["id"]

    # Second upsert with same (source, external_ref) updates in place
    second = client.post(
        "/commitments",
        json={
            "title": "Flight JL43 (Gate B2)",
            "target_date": "2026-11-23",
            "start_minute": 555,
            "end_minute": 675,
            "source": "travel",
            "external_ref": "odysseus:tokyo-2027:item:101",
            "urgent_quiet_bypass": True,
            "notes": "Updated gate",
            "upsert_by_external_ref": True,
        },
    )
    assert second.status_code == 201
    assert second.json()["id"] == first_id
    assert second.json()["title"] == "Flight JL43 (Gate B2)"
    assert second.json()["start_minute"] == 555

    # Default upsert_by_external_ref=false preserves conflict error on duplicate (source, external_ref)
    with pytest.raises(IntegrityError):
        client.post(
            "/commitments",
            json={
                "title": "Duplicate",
                "target_date": "2026-11-23",
                "start_minute": 600,
                "end_minute": 630,
                "source": "travel",
                "external_ref": "odysseus:tokyo-2027:item:101",
            },
        )

    # Seed another trip commitment and a manual commitment for filter isolation
    client.post(
        "/commitments",
        json={
            "title": "Kyoto Ryokan Check-in",
            "target_date": "2026-11-24",
            "all_day": True,
            "source": "travel",
            "external_ref": "odysseus:kyoto-2027:item:202",
            "upsert_by_external_ref": True,
        },
    )
    client.post(
        "/commitments",
        json={
            "title": "Dentist",
            "target_date": "2026-11-23",
            "start_minute": 700,
            "end_minute": 730,
            "source": "manual",
            "external_ref": "odysseus:tokyo-2027:manual-lookalike",
        },
    )

    filtered = client.get(
        "/commitments",
        params={"source": "travel", "external_ref_prefix": "odysseus:tokyo-2027:"},
    )
    assert filtered.status_code == 200
    assert [item["id"] for item in filtered.json()] == [first_id]

    # DELETE /commitments/by-ref resolves before /{commitment_id} and is idempotent
    deleted = client.delete(
        "/commitments/by-ref",
        params={"source": "travel", "external_ref": "odysseus:tokyo-2027:item:101"},
    )
    assert deleted.status_code == 204
    assert (
        client.delete(
            "/commitments/by-ref",
            params={"source": "travel", "external_ref": "odysseus:tokyo-2027:item:101"},
        ).status_code
        == 204
    )
    remaining_travel = client.get("/commitments", params={"source": "travel"}).json()
    assert [item["external_ref"] for item in remaining_travel] == [
        "odysseus:kyoto-2027:item:202"
    ]
