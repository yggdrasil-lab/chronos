"""API integration tests for internal GET /integrations/daily-summary."""

from collections.abc import Generator
from datetime import date

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base, get_db
from app.domain.models import CommitmentSource, FrictionTag, NudgeMode
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
from app.repositories.sqlalchemy_schedule_repo import (
    SqlAlchemyHabitCueRepo,
    SqlAlchemyScheduleRepository,
)
from app.routers import deps


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


class _TimeoutEirGateway:
    def fetch_day_snapshot(self, target_date: date) -> EirDaySnapshot:
        del target_date
        raise httpx.ReadTimeout("eir timed out")


class _TimeoutKratosGateway:
    def fetch_day_snapshot(self, target_date: date) -> KratosDaySnapshot:
        del target_date
        raise httpx.ReadTimeout("kratos timed out")


@pytest.fixture()
def test_session_factory() -> Generator[sessionmaker[Session], None, None]:
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
        yield TestingSessionLocal
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_daily_summary_full_payload_and_peer_timeout_resilience(
    test_session_factory: sessionmaker[Session],
) -> None:
    today = date(2026, 11, 26)  # Thursday (weekday=3)
    yesterday = date(2026, 11, 25)

    with test_session_factory() as db:
        sched_repo = SqlAlchemyScheduleRepository(db)
        cue_repo = SqlAlchemyHabitCueRepo(db)

        workout_block = sched_repo.create_grid_block(
            title="Evening Training",
            day_of_week=3,
            start_minute=1020,
            end_minute=1140,
            category="workout",
            nudge_mode=NudgeMode.BOUNDARY,
            flexible=False,
            active=True,
            notes=None,
        )
        skipped_block = sched_repo.create_grid_block(
            title="Reading",
            day_of_week=3,
            start_minute=1200,
            end_minute=1260,
            category="personal",
            nudge_mode=NudgeMode.NONE,
            flexible=True,
            active=True,
            notes=None,
        )
        sched_repo.upsert_exception(
            grid_block_id=skipped_block.id,
            target_date=today,
            skipped=True,
            start_minute=None,
            end_minute=None,
            extension_count=0,
            notes="Skipped for flight prep",
        )
        sched_repo.create_commitment(
            title="Flight NZ502 to Wellington",
            target_date=today,
            start_minute=540,
            end_minute=660,
            all_day=False,
            source=CommitmentSource.IRIS,
            external_ref="<nz502@example.invalid>",
            nudge_mode=NudgeMode.TWO_STAGE,
            urgent_quiet_bypass=False,
            tentative=True,
            notes="Gate 4",
        )
        sched_repo.create_commitment(
            title="Dentist Checkup",
            target_date=today,
            start_minute=600,
            end_minute=690,
            all_day=False,
            source=CommitmentSource.MANUAL,
            external_ref=None,
            nudge_mode=NudgeMode.BOUNDARY,
            urgent_quiet_bypass=False,
            tentative=False,
            notes=None,
        )

        cue_weigh = cue_repo.create_cue(
            title="Morning Weigh-In",
            zone_chain="bathroom-scale",
            chain_order=0,
            passive_rule="eir_morning_weigh_in",
            active=True,
            notes=None,
            grid_block_ids=(),
        )
        cue_repo.create_cue(
            title="Evening Lift",
            zone_chain="gym-floor",
            chain_order=1,
            passive_rule="kratos_workout_logged",
            active=True,
            notes=None,
            grid_block_ids=(workout_block.id,),
        )
        cue_repo.create_friction_note(
            target_date=yesterday,
            tag=FrictionTag.SCHEDULE_OVERRUN,
            note="Late client call delayed gym warm-up",
            habit_cue_id=cue_weigh.id,
            grid_block_id=workout_block.id,
        )

    app.dependency_overrides[deps.get_eir_gateway] = lambda: _StubEirGateway(
        EirDaySnapshot(
            status=PeerFetchStatus(available=True),
            latest_measured_at="2026-11-25T07:10:00+13:00",
            latest_weight_kg="76.2",
            detected_workouts=(
                EirDetectedWorkout(
                    started_at="2026-11-25T06:15:00+13:00",
                    ended_at="2026-11-25T07:00:00+13:00",
                    duration_minutes=45,
                    classification="walk",
                ),
            ),
        )
    )
    app.dependency_overrides[deps.get_kratos_gateway] = lambda: _StubKratosGateway(
        KratosDaySnapshot(
            status=PeerFetchStatus(available=True),
            planned_days=(
                KratosPlannedDay(
                    day="Thu",
                    day_index=3,
                    session_name="Upper Push A",
                    workout_type="strength",
                ),
            ),
            sessions=(
                KratosSessionSnapshot(
                    session_id="sess-yday",
                    routine_name="Lower Pull A",
                    activity_type="strength",
                    started_at="2026-11-25T17:30:00+13:00",
                    ended_at="2026-11-25T18:20:00+13:00",
                ),
            ),
        )
    )
    app.dependency_overrides[deps.get_plutus_gateway] = lambda: _StubPlutusGateway(
        PlutusCheckinSnapshot(
            status=PeerFetchStatus(available=True),
            accounts=(
                PlutusAccountSnapshot(
                    account_id="1",
                    last_synced_at="2026-11-25T09:00:00+13:00",
                ),
            ),
            general_category_count=0,
            needs_review_count=0,
        )
    )

    with TestClient(app) as client:
        resp = client.get(
            "/integrations/daily-summary",
            params={"target_date": "2026-11-26"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["date"] == "2026-11-26"
        assert body["yesterday"] == "2026-11-25"
        assert body["tentative_count"] == 1
        assert body["conflict_count"] == 2
        assert len(body["commitments"]) == 2
        assert all(item["has_conflict"] is True for item in body["commitments"])

        assert len(body["grid_exceptions"]) == 1
        assert body["grid_exceptions"][0]["action"] == "skip"
        assert body["grid_exceptions"][0]["notes"] == "Skipped for flight prep"

        hydration = body["workout_hydration"]
        assert hydration["status"] == "ok"
        assert hydration["planned_routine_name"] == "Upper Push A"
        assert hydration["planned_day_of_week"] == "Thu"
        assert hydration["attached_grid_block_id"] == workout_block.id
        assert body["fasting_suggestion"]["protocol"] == "14:10"
        assert body["fasting_suggestion"]["is_optional_reset"] is False

        assert len(body["yesterday_friction_notes"]) == 1
        assert body["yesterday_friction_notes"][0]["tag"] == "schedule_overrun"

        signals_by_rule = {
            item["rule_key"]: item for item in body["yesterday_passive_signals"]
        }
        assert signals_by_rule["eir_morning_weigh_in"]["observed"] is True
        assert signals_by_rule["eir_morning_weigh_in"]["peer_status"] == "ok"
        assert signals_by_rule["kratos_workout_completed"]["observed"] is True
        assert body["degraded_peers"] == []

        # Peer timeouts degrade cleanly without breaking 200 response
        app.dependency_overrides[deps.get_eir_gateway] = _TimeoutEirGateway
        app.dependency_overrides[deps.get_kratos_gateway] = _TimeoutKratosGateway
        degraded_resp = client.get(
            "/integrations/daily-summary",
            params={"target_date": "2026-11-26"},
        )
        assert degraded_resp.status_code == 200
        degraded = degraded_resp.json()
        assert set(degraded["degraded_peers"]) == {"eir", "kratos"}
        assert degraded["workout_hydration"]["status"] == "degraded"
        degraded_signals = {
            item["rule_key"]: item for item in degraded["yesterday_passive_signals"]
        }
        assert degraded_signals["eir_morning_weigh_in"]["peer_status"] == "degraded"
        assert degraded_signals["kratos_workout_completed"]["peer_status"] == "degraded"
