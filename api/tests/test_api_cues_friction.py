"""API integration tests for habit cues, friction notes, reflection, and CORS removal."""

from collections.abc import Generator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event
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


def _create_block(client: TestClient, title: str, day_of_week: int = 0) -> int:
    resp = client.post(
        "/weekly-grid/blocks",
        json={
            "title": title,
            "day_of_week": day_of_week,
            "start_minute": 420,
            "end_minute": 480,
            "category": "routine",
        },
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def test_cross_origin_requests_omit_wildcard_cors_headers(client: TestClient) -> None:
    get_resp = client.get("/health", headers={"Origin": "https://evil.example"})
    assert get_resp.status_code == 200
    assert "access-control-allow-origin" not in get_resp.headers

    preflight = client.options(
        "/cues",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in preflight.headers


def test_cue_crud_anchoring_filtering_and_ordering(client: TestClient) -> None:
    block_mon = _create_block(client, "Morning Prep", day_of_week=0)
    block_wed = _create_block(client, "Midweek Prep", day_of_week=2)
    block_fri = _create_block(client, "Friday Wrap", day_of_week=4)

    cue_b = client.post(
        "/cues",
        json={
            "title": "Brew tea",
            "zone_chain": "kitchen",
            "chain_order": 2,
            "passive_rule": "Kettle next to mug",
            "active": True,
            "notes": "Keep leaves visible",
            "grid_block_ids": [block_wed, block_mon, block_mon],
        },
    )
    assert cue_b.status_code == 201
    cue_b_data = cue_b.json()
    assert cue_b_data["grid_block_ids"] == sorted([block_mon, block_wed])
    assert cue_b_data["recent_friction"] == []

    cue_a = client.post(
        "/cues",
        json={
            "title": "Stretch mat out",
            "zone_chain": "bedroom",
            "chain_order": 1,
            "active": False,
            "grid_block_ids": [block_mon],
        },
    )
    assert cue_a.status_code == 201

    cue_c = client.post(
        "/cues",
        json={
            "title": "Fill water bottle",
            "zone_chain": "kitchen",
            "chain_order": 1,
            "active": True,
            "grid_block_ids": [block_fri],
        },
    )
    assert cue_c.status_code == 201

    ordered = client.get("/cues").json()
    assert [c["title"] for c in ordered] == [
        "Stretch mat out",
        "Fill water bottle",
        "Brew tea",
    ]

    active_only = client.get("/cues", params={"active_only": True}).json()
    assert [c["title"] for c in active_only] == ["Fill water bottle", "Brew tea"]

    by_block = client.get("/cues", params={"grid_block_id": block_mon}).json()
    assert [c["title"] for c in by_block] == ["Stretch mat out", "Brew tea"]

    by_zone = client.get("/cues", params={"zone_chain": "kitchen"}).json()
    assert [c["title"] for c in by_zone] == ["Fill water bottle", "Brew tea"]

    # Partial PATCH preserves anchors when grid_block_ids is omitted, clears nullable notes
    patched_keep = client.patch(
        f"/cues/{cue_b_data['id']}",
        json={"title": "Brew green tea", "notes": None},
    )
    assert patched_keep.status_code == 200
    assert patched_keep.json()["title"] == "Brew green tea"
    assert patched_keep.json()["notes"] is None
    assert patched_keep.json()["passive_rule"] == "Kettle next to mug"
    assert patched_keep.json()["grid_block_ids"] == sorted([block_mon, block_wed])

    # PATCH with grid_block_ids atomically replaces anchors
    patched_replace = client.patch(
        f"/cues/{cue_b_data['id']}",
        json={"grid_block_ids": [block_fri]},
    )
    assert patched_replace.status_code == 200
    assert patched_replace.json()["grid_block_ids"] == [block_fri]
    assert [c["title"] for c in client.get("/cues", params={"grid_block_id": block_wed}).json()] == []

    # Deleting a linked grid block cascades join rows cleanly
    assert client.delete(f"/weekly-grid/blocks/{block_fri}").status_code == 204
    refreshed = client.get("/cues", params={"zone_chain": "kitchen"}).json()
    for item in refreshed:
        assert block_fri not in item["grid_block_ids"]

    assert client.delete(f"/cues/{cue_a.json()['id']}").status_code == 204
    assert client.delete(f"/cues/{cue_a.json()['id']}").status_code == 404


def test_friction_notes_recent_cap_cascade_and_reflection(client: TestClient) -> None:
    block_id = _create_block(client, "Evening Reset", day_of_week=1)
    cue_id = client.post(
        "/cues",
        json={
            "title": "Pack gym bag",
            "zone_chain": "hallway",
            "chain_order": 0,
            "grid_block_ids": [block_id],
        },
    ).json()["id"]

    # Create 4 notes linked to cue_id across dates; recent_friction on cue should cap at 3 newest
    created_ids: list[int] = []
    for day in ("2026-11-23", "2026-11-24", "2026-11-25", "2026-11-25"):
        resp = client.post(
            "/friction-notes",
            json={
                "target_date": day,
                "tag": "tired" if day != "2026-11-24" else "environment_missing",
                "note": f"Friction on {day}",
                "habit_cue_id": cue_id,
                "grid_block_id": block_id,
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["cue_title"] == "Pack gym bag"
        assert body["block_title"] == "Evening Reset"
        created_ids.append(body["id"])

    # One unlinked note in the same week and one outside the week
    unlinked_resp = client.post(
        "/friction-notes",
        json={
            "target_date": "2026-11-27",
            "tag": "schedule_overrun",
            "note": "Dinner ran late",
            "grid_block_id": block_id,
        },
    )
    assert unlinked_resp.status_code == 201
    unlinked_id = unlinked_resp.json()["id"]

    outside_resp = client.post(
        "/friction-notes",
        json={
            "target_date": "2026-11-30",
            "tag": "trigger_missed",
            "note": "Next week note",
            "habit_cue_id": cue_id,
        },
    )
    assert outside_resp.status_code == 201

    cues = client.get("/cues", params={"zone_chain": "hallway"}).json()
    assert len(cues) == 1
    recent = cues[0]["recent_friction"]
    assert len(recent) == 3
    assert [n["id"] for n in recent] == [
        outside_resp.json()["id"],
        created_ids[3],
        created_ids[2],
    ]

    listed = client.get(
        "/friction-notes",
        params={"start_date": "2026-11-23", "end_date": "2026-11-29", "limit": 10},
    ).json()
    assert [n["id"] for n in listed] == [
        unlinked_id,
        created_ids[3],
        created_ids[2],
        created_ids[1],
        created_ids[0],
    ]

    reflection = client.get("/friction-notes/reflection", params={"week_start": "2026-11-23"})
    assert reflection.status_code == 200
    ref_data = reflection.json()
    assert ref_data["week_start"] == "2026-11-23"
    assert ref_data["week_end"] == "2026-11-29"
    assert ref_data["total_notes"] == 5
    assert ref_data["by_tag"][0]["tag"] == "tired"
    assert ref_data["by_tag"][0]["count"] == 3
    assert len(ref_data["by_cue"]) == 1
    assert ref_data["by_cue"][0]["habit_cue_id"] == cue_id
    assert ref_data["by_cue"][0]["zone_chain"] == "hallway"
    assert ref_data["by_cue"][0]["count"] == 4
    assert [n["id"] for n in ref_data["unlinked_notes"]] == [unlinked_id]

    # Default week_start uses current NZDT Monday
    today_nz = datetime.now(ZoneInfo("Pacific/Auckland")).date()
    expected_monday = today_nz - timedelta(days=today_nz.weekday())
    default_ref = client.get("/friction-notes/reflection")
    assert default_ref.status_code == 200
    assert default_ref.json()["week_start"] == expected_monday.isoformat()
    assert default_ref.json()["week_end"] == (expected_monday + timedelta(days=6)).isoformat()

    # Deleting cue detaches friction_notes (ON DELETE SET NULL) instead of deleting them
    assert client.delete(f"/cues/{cue_id}").status_code == 204
    after_cue_delete = client.get("/friction-notes", params={"habit_cue_id": cue_id}).json()
    assert after_cue_delete == []
    all_notes = client.get("/friction-notes", params={"limit": 10}).json()
    assert len(all_notes) == 6
    assert all(n["habit_cue_id"] is None and n["cue_title"] is None for n in all_notes)

    assert client.delete(f"/friction-notes/{unlinked_id}").status_code == 204
    assert client.delete(f"/friction-notes/{unlinked_id}").status_code == 404


def test_cues_and_friction_validation_and_not_found_errors(client: TestClient) -> None:
    # 422 empty title / zone_chain / negative chain_order / non-positive grid_block_ids
    assert (
        client.post("/cues", json={"title": "   ", "zone_chain": "kitchen"}).status_code == 422
    )
    assert (
        client.post("/cues", json={"title": "Valid", "zone_chain": "  "}).status_code == 422
    )
    assert (
        client.post(
            "/cues", json={"title": "Valid", "zone_chain": "kitchen", "chain_order": -1}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/cues",
            json={"title": "Valid", "zone_chain": "kitchen", "grid_block_ids": [0]},
        ).status_code
        == 422
    )

    # 404 missing grid_block_ids on create / update, and missing cue on patch
    assert (
        client.post(
            "/cues",
            json={"title": "Valid", "zone_chain": "kitchen", "grid_block_ids": [9999]},
        ).status_code
        == 404
    )
    assert client.patch("/cues/9999", json={"title": "Missing"}).status_code == 404

    cue_id = client.post(
        "/cues", json={"title": "Valid", "zone_chain": "kitchen"}
    ).json()["id"]
    assert client.patch(f"/cues/{cue_id}", json={"grid_block_ids": [9999]}).status_code == 404

    # 422 friction note validation: empty note, >280 chars after strip, bad tag, bad FK IDs
    assert (
        client.post(
            "/friction-notes",
            json={"target_date": "2026-11-25", "tag": "tired", "note": "   "},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/friction-notes",
            json={"target_date": "2026-11-25", "tag": "tired", "note": "x" * 281},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/friction-notes",
            json={"target_date": "2026-11-25", "tag": "unknown_tag", "note": "oops"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/friction-notes",
            json={
                "target_date": "2026-11-25",
                "tag": "tired",
                "note": "bad fk",
                "habit_cue_id": 0,
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/friction-notes",
            json={
                "target_date": "2026-11-25",
                "tag": "tired",
                "note": "bad fk",
                "grid_block_id": -2,
            },
        ).status_code
        == 422
    )

    # 404 missing habit_cue_id or grid_block_id on friction note create
    assert (
        client.post(
            "/friction-notes",
            json={
                "target_date": "2026-11-25",
                "tag": "tired",
                "note": "missing cue",
                "habit_cue_id": 9999,
            },
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/friction-notes",
            json={
                "target_date": "2026-11-25",
                "tag": "tired",
                "note": "missing block",
                "grid_block_id": 9999,
            },
        ).status_code
        == 404
    )

    # 280 chars after strip succeeds cleanly
    exact_280 = "  " + ("a" * 280) + "  "
    ok_280 = client.post(
        "/friction-notes",
        json={"target_date": "2026-11-25", "tag": "tired", "note": exact_280},
    )
    assert ok_280.status_code == 201
    assert ok_280.json()["note"] == "a" * 280

    # Query param validations
    assert (
        client.get(
            "/friction-notes",
            params={"start_date": "2026-11-29", "end_date": "2026-11-23"},
        ).status_code
        == 422
    )
    assert client.get("/friction-notes", params={"limit": 0}).status_code == 422
    assert client.get("/cues", params={"grid_block_id": 0}).status_code == 422
