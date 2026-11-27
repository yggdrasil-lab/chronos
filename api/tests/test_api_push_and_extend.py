"""API integration tests for Chronos Web Push lifecycle and Deep Work Extend."""

import base64
from collections.abc import Generator
from datetime import date, datetime
from zoneinfo import ZoneInfo

from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from py_vapid import Vapid
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base, get_db
from app.domain.fasting import EirDailyVitalsSnapshot
from app.domain.models import PushSubscription
from app.domain.passive_signals import PeerFetchStatus
from app.domain.sleep_recovery import EirNapRecord
from app.main import app
from app.repositories.sqlalchemy_push_repo import SqlAlchemyPushRepository
from app.repositories.sqlalchemy_schedule_repo import SqlAlchemyScheduleRepository
from app.routers import deps
from app.services.push_gateway import PushSendResult, PyWebPushGateway
from app.services.push_service import PushService


class FakePushGateway:
    """In-memory boundary fake recording payloads and simulating 410 stale endpoints."""

    def __init__(self, *, stale_endpoints: set[str] | None = None) -> None:
        self.stale_endpoints = stale_endpoints or set()
        self.sent: list[tuple[str, dict[str, str]]] = []

    def send(self, subscription: PushSubscription, payload: dict[str, str]) -> PushSendResult:
        if subscription.endpoint in self.stale_endpoints:
            return PushSendResult(ok=False, stale=True, status_code=410, error="Gone")
        self.sent.append((subscription.endpoint, payload))
        return PushSendResult(ok=True, status_code=201)


def _generate_raw_p256_base64url_private_key() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    raw_scalar = key.private_numbers().private_value.to_bytes(32, "big")
    return base64.urlsafe_b64encode(raw_scalar).rstrip(b"=").decode("ascii")


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


def test_vapid_public_key_and_subscription_lifecycle(
    test_session_factory: sessionmaker[Session], monkeypatch
) -> None:
    monkeypatch.setattr(deps.settings, "vapid_private_key", "")
    monkeypatch.setattr(deps.settings, "vapid_public_key", "")
    monkeypatch.setattr(deps.settings, "vapid_subject", "")

    with TestClient(app) as client:
        disabled = client.get("/push/vapid-public-key")
        assert disabled.status_code == 200
        assert disabled.json() == {"public_key": "", "enabled": False}

        monkeypatch.setattr(deps.settings, "vapid_private_key", _generate_raw_p256_base64url_private_key())
        monkeypatch.setattr(deps.settings, "vapid_public_key", "BTestPublicKey123")
        monkeypatch.setattr(deps.settings, "vapid_subject", "mailto:chronos@example.invalid")

        enabled = client.get("/push/vapid-public-key")
        assert enabled.status_code == 200
        assert enabled.json() == {"public_key": "BTestPublicKey123", "enabled": True}

        sub_1 = client.post(
            "/push/subscribe",
            json={
                "endpoint": "https://push.example.invalid/sub/1",
                "keys": {"p256dh": "key-1", "auth": "auth-1"},
                "user_agent": "Kairos/1.0",
            },
        )
        assert sub_1.status_code == 200
        sub_id = sub_1.json()["id"]
        assert sub_1.json()["active"] is True

        # Idempotent upsert keeps same subscription row ID and updates keys
        sub_1_refresh = client.post(
            "/push/subscribe",
            json={
                "endpoint": "https://push.example.invalid/sub/1",
                "keys": {"p256dh": "key-2", "auth": "auth-2"},
                "user_agent": "Kairos/1.1",
            },
        )
        assert sub_1_refresh.status_code == 200
        assert sub_1_refresh.json()["id"] == sub_id

        removed = client.post(
            "/push/unsubscribe",
            json={"endpoint": "https://push.example.invalid/sub/1"},
        )
        assert removed.status_code == 200
        assert removed.json() == {
            "endpoint": "https://push.example.invalid/sub/1",
            "removed": True,
        }

        missing = client.post(
            "/push/unsubscribe",
            json={"endpoint": "https://push.example.invalid/sub/1"},
        )
        assert missing.status_code == 200
        assert missing.json()["removed"] is False


def test_push_scheduler_tick_single_fire_and_410_pruning(
    test_session_factory: sessionmaker[Session],
) -> None:
    with TestClient(app) as client:
        # Timed commitment defaults to two-stage; all_day defaults to none
        timed = client.post(
            "/commitments",
            json={
                "title": "Design Review",
                "target_date": "2026-11-23",
                "start_minute": 540,
                "end_minute": 600,
            },
        )
        assert timed.status_code == 201
        assert timed.json()["nudge_mode"] == "two-stage"

        all_day = client.post(
            "/commitments",
            json={"title": "Offsite", "target_date": "2026-11-23", "all_day": True},
        )
        assert all_day.status_code == 201
        assert all_day.json()["nudge_mode"] == "none"

        client.post(
            "/push/subscribe",
            json={
                "endpoint": "https://push.example.invalid/active",
                "keys": {"p256dh": "k1", "auth": "a1"},
            },
        )
        client.post(
            "/push/subscribe",
            json={
                "endpoint": "https://push.example.invalid/stale",
                "keys": {"p256dh": "k2", "auth": "a2"},
            },
        )

    fake_gateway = FakePushGateway(stale_endpoints={"https://push.example.invalid/stale"})
    with test_session_factory() as db:
        service = PushService(
            schedule_repo=SqlAlchemyScheduleRepository(db),
            push_repo=SqlAlchemyPushRepository(db),
            gateway=fake_gateway,
            vapid_public_key="BPub",
            timezone_name="Pacific/Auckland",
        )
        tick_dt = datetime(2026, 11, 23, 9, 50, tzinfo=ZoneInfo("Pacific/Auckland"))
        assert service.run_scheduled_tick(now_dt=tick_dt) == 1
        assert len(fake_gateway.sent) == 1
        endpoint, payload = fake_gateway.sent[0]
        assert endpoint == "https://push.example.invalid/active"
        assert payload["stage"] == "pre_end_10m"
        assert payload["source_kind"] == "commitment"
        assert payload["url"] == "/?tab=now"

        # Stale endpoint pruned and same pre_end_10m does not re-fire
        assert len(SqlAlchemyPushRepository(db).list_subscriptions()) == 1
        assert service.run_scheduled_tick(now_dt=tick_dt) == 0


def test_pywebpush_gateway_with_synthetic_raw_vapid_key(monkeypatch) -> None:
    raw_private = _generate_raw_p256_base64url_private_key()
    assert len(raw_private) == 43
    vapid = Vapid.from_string(raw_private)
    assert vapid.private_key is not None

    captured: dict[str, object] = {}

    def _fake_webpush(**kwargs) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("app.services.push_gateway.webpush", _fake_webpush)
    gateway = PyWebPushGateway(
        vapid_private_key=raw_private,
        vapid_subject="mailto:chronos@example.invalid",
    )
    sub = PushSubscription(
        id="00000000-0000-0000-0000-000000000001",
        endpoint="https://push.example.invalid/sub/99",
        p256dh="p256dh-val",
        auth="auth-val",
        user_agent=None,
        created_at=datetime.now(ZoneInfo("UTC")),
        updated_at=datetime.now(ZoneInfo("UTC")),
    )
    result = gateway.send(sub, {"title": "Test", "body": "Body", "url": "/?tab=now"})
    assert result.ok is True
    assert captured["ttl"] == 900
    assert captured["headers"] == {"Urgency": "high"}
    assert captured["vapid_claims"] == {"sub": "mailto:chronos@example.invalid"}


def test_extend_endpoint_trims_flexible_blocks_preserves_commitments_and_enforces_cap(
    test_session_factory: sessionmaker[Session],
) -> None:
    with TestClient(app) as client:
        deep_work = client.post(
            "/weekly-grid/blocks",
            json={
                "title": "Deep Work",
                "day_of_week": 0,
                "start_minute": 540,
                "end_minute": 660,
                "category": "focus",
                "nudge_mode": "two-stage",
                "flexible": False,
            },
        ).json()
        flex_short = client.post(
            "/weekly-grid/blocks",
            json={
                "title": "Email Sweep",
                "day_of_week": 0,
                "start_minute": 660,
                "end_minute": 675,
                "category": "admin",
                "flexible": True,
            },
        ).json()
        flex_long = client.post(
            "/weekly-grid/blocks",
            json={
                "title": "Reading",
                "day_of_week": 0,
                "start_minute": 675,
                "end_minute": 735,
                "category": "learning",
                "flexible": True,
            },
        ).json()
        commitment = client.post(
            "/commitments",
            json={
                "title": "Hard Sync",
                "target_date": "2026-11-23",
                "start_minute": 700,
                "end_minute": 720,
                "urgent_quiet_bypass": True,
            },
        ).json()
        assert commitment["urgent_quiet_bypass"] is True

        # 1st extension (+30m -> 690): consumes flex_short (skipped) and trims flex_long start to 690
        ext_1 = client.post(
            "/schedule/extend",
            json={"target_date": "2026-11-23", "grid_block_id": deep_work["id"], "minutes": 30},
        )
        assert ext_1.status_code == 200
        body_1 = ext_1.json()
        assert body_1["extended_exception"]["end_minute"] == 690
        assert body_1["extended_exception"]["extension_count"] == 1
        trimmed_by_id = {item["grid_block_id"]: item for item in body_1["trimmed_exceptions"]}
        assert trimmed_by_id[flex_short["id"]]["skipped"] is True
        assert trimmed_by_id[flex_long["id"]]["start_minute"] == 690
        assert trimmed_by_id[flex_long["id"]]["skipped"] is False

        # 2nd extension (+15m -> 705)
        ext_2 = client.post(
            "/schedule/extend",
            json={"target_date": "2026-11-23", "grid_block_id": deep_work["id"], "minutes": 15},
        )
        assert ext_2.status_code == 200
        assert ext_2.json()["extended_exception"]["end_minute"] == 705
        assert ext_2.json()["extended_exception"]["extension_count"] == 2

        # 3rd extension -> 409 Conflict
        ext_3 = client.post(
            "/schedule/extend",
            json={"target_date": "2026-11-23", "grid_block_id": deep_work["id"], "minutes": 15},
        )
        assert ext_3.status_code == 409

        # Unknown block -> 404
        missing = client.post(
            "/schedule/extend",
            json={"target_date": "2026-11-23", "grid_block_id": 99999, "minutes": 15},
        )
        assert missing.status_code == 404

        # Skipped block -> 409
        skipped_ext = client.post(
            "/schedule/extend",
            json={"target_date": "2026-11-23", "grid_block_id": flex_short["id"], "minutes": 15},
        )
        assert skipped_ext.status_code == 409

        # Hard commitment stays fixed at [700, 720)
        day_schedule = client.get("/schedule/day", params={"target_date": "2026-11-23"}).json()
        commit_slices = [s for s in day_schedule["slices"] if s["kind"] == "commitment"]
        assert [(s["start_minute"], s["end_minute"], s["urgent_quiet_bypass"]) for s in commit_slices] == [
            (700, 720, True)
        ]

        # Block already ending at 1440 -> 409
        midnight_block = client.post(
            "/weekly-grid/blocks",
            json={
                "title": "Late Night",
                "day_of_week": 0,
                "start_minute": 1380,
                "end_minute": 1440,
                "category": "rest",
            },
        ).json()
        midnight_ext = client.post(
            "/schedule/extend",
            json={"target_date": "2026-11-23", "grid_block_id": midnight_block["id"], "minutes": 15},
        )
        assert midnight_ext.status_code == 409


def test_push_service_loads_travel_overlay_suppresses_screens_off_and_keeps_urgent_bypass(
    test_session_factory: sessionmaker[Session],
) -> None:
    with TestClient(app) as client:
        client.post(
            "/schedule/travel-mode",
            json={
                "external_ref": "odysseus:kyoto-2027",
                "label": "Kyoto Trip",
                "start_date": "2026-11-23",
                "end_date": "2026-11-25",
                "mode": "suppress_all_grid_blocks",
                "active": True,
            },
        )
        client.post(
            "/commitments",
            json={
                "title": "Urgent Flight Check-in",
                "target_date": "2026-11-23",
                "start_minute": 1300,
                "end_minute": 1330,
                "source": "travel",
                "external_ref": "odysseus:kyoto-2027:item:9",
                "nudge_mode": "two-stage",
                "urgent_quiet_bypass": True,
                "upsert_by_external_ref": True,
            },
        )
        client.post(
            "/push/subscribe",
            json={
                "endpoint": "https://push.example.invalid/travel",
                "keys": {"p256dh": "k1", "auth": "a1"},
            },
        )

    fake_gateway = FakePushGateway()
    with test_session_factory() as db:
        service = PushService(
            schedule_repo=SqlAlchemyScheduleRepository(db),
            push_repo=SqlAlchemyPushRepository(db),
            gateway=fake_gateway,
            vapid_public_key="BPub",
            timezone_name="Pacific/Auckland",
        )
        tick_2200 = datetime(2026, 11, 23, 22, 0, tzinfo=ZoneInfo("Pacific/Auckland"))
        assert service.run_scheduled_tick(now_dt=tick_2200) == 1
        assert len(fake_gateway.sent) == 1
        _endpoint, payload = fake_gateway.sent[0]
        assert payload["source_kind"] == "commitment"
        assert payload["stage"] == "pre_end_10m"
        assert payload["title"] == "Urgent Flight Check-in ends in 10m"


def test_push_service_2200_sleep_pressure_copy_window_guard_and_fail_soft(
    test_session_factory: sessionmaker[Session],
) -> None:
    class _RecordingEirVitals:
        def __init__(self, snapshot: EirDailyVitalsSnapshot, *, raise_exc: bool = False) -> None:
            self.snapshot = snapshot
            self.raise_exc = raise_exc
            self.calls: list[date] = []

        def fetch_daily_vitals(self, target_date: date) -> EirDailyVitalsSnapshot:
            self.calls.append(target_date)
            if self.raise_exc:
                raise RuntimeError("eir offline")
            return self.snapshot

    with TestClient(app) as client:
        client.post(
            "/push/subscribe",
            json={
                "endpoint": "https://push.example.invalid/sleep-nudge",
                "keys": {"p256dh": "k1", "auth": "a1"},
            },
        )

    late_nap_vitals = EirDailyVitalsSnapshot(
        status=PeerFetchStatus(available=True),
        nap_duration_hours=2.18,
        nap_count=1,
        naps=(
            EirNapRecord(
                start_at="2026-11-23T16:29:00+13:00",
                end_at="2026-11-23T18:39:00+13:00",
                duration_hours=2.18,
            ),
        ),
    )

    with test_session_factory() as db:
        assert deps.build_push_service(db)._eir_vitals_gateway is not None

        eir_gw = _RecordingEirVitals(late_nap_vitals)
        fake_gateway = FakePushGateway()
        service = PushService(
            schedule_repo=SqlAlchemyScheduleRepository(db),
            push_repo=SqlAlchemyPushRepository(db),
            gateway=fake_gateway,
            vapid_public_key="BPub",
            timezone_name="Pacific/Auckland",
            eir_vitals_gateway=eir_gw,
        )

        # Outside 22:00-22:15 window -> zero Eir calls
        tick_2159 = datetime(2026, 11, 23, 21, 59, tzinfo=ZoneInfo("Pacific/Auckland"))
        tick_2215 = datetime(2026, 11, 23, 22, 15, tzinfo=ZoneInfo("Pacific/Auckland"))
        assert service.run_scheduled_tick(now_dt=tick_2159) == 0
        assert service.run_scheduled_tick(now_dt=tick_2215) == 0
        assert eir_gw.calls == []

        # Inside 22:00-22:15 -> Eir called once and adapted copy dispatched
        tick_2200 = datetime(2026, 11, 23, 22, 0, tzinfo=ZoneInfo("Pacific/Auckland"))
        assert service.run_scheduled_tick(now_dt=tick_2200) == 1
        assert eir_gw.calls == [date(2026, 11, 23)]
        assert len(fake_gateway.sent) == 1
        _, payload = fake_gateway.sent[0]
        assert payload["title"] == "Screens Off - Quiet Hours"
        assert payload["tag"] == "chronos-quiet-hours-screens-off"
        assert payload["body"] == (
            "It is 22:00. Late 2.2h nap ending 18:39 may delay evening sleep onset. "
            "Keep lights dim and wind down calmly without forcing sleep."
        )

    # Eir outage during 22:00-22:15 fails soft to baseline 22:00 copy on fresh day
    with test_session_factory() as db:
        failing_eir = _RecordingEirVitals(late_nap_vitals, raise_exc=True)
        fallback_gateway = FakePushGateway()
        fallback_service = PushService(
            schedule_repo=SqlAlchemyScheduleRepository(db),
            push_repo=SqlAlchemyPushRepository(db),
            gateway=fallback_gateway,
            vapid_public_key="BPub",
            timezone_name="Pacific/Auckland",
            eir_vitals_gateway=failing_eir,
        )
        tick_2205_next = datetime(2026, 11, 24, 22, 5, tzinfo=ZoneInfo("Pacific/Auckland"))
        assert fallback_service.run_scheduled_tick(now_dt=tick_2205_next) == 1
        assert failing_eir.calls == [date(2026, 11, 24)]
        _, fallback_payload = fallback_gateway.sent[0]
        assert fallback_payload["body"] == (
            "It is 22:00. Wind down for sleep; non-urgent nudges are paused until 06:30."
        )
