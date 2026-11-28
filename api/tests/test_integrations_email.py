"""Integration and unit tests for Iris email ingestion and tentative commitments."""

import base64
from collections.abc import Generator
from datetime import date
import json

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db.session import Base, get_db
from app.domain.models import Commitment, CommitmentSource, NudgeMode
from app.main import app
from app.repositories.sqlalchemy_schedule_repo import SqlAlchemyScheduleRepository
from app.routers import deps
from app.schemas.integrations import InboundEmailPayload, ScheduleEventPayload
from app.services.email_ingestion import (
    EmailIngestionService,
    ExtractedScheduleEvent,
    MuninnScheduleExtractorAdapter,
    NullScheduleExtractor,
    ScheduleExtractionUpstreamError,
)


class FakeScheduleExtractor:
    """In-memory extractor recording calls for idempotency verification."""

    def __init__(self, result: ExtractedScheduleEvent | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[str] = []

    def extract(self, payload: InboundEmailPayload) -> ExtractedScheduleEvent | None:
        self.calls.append(payload.message_id)
        if self.error is not None:
            raise self.error
        return self.result


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


def _base_email_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "email_id": 101,
        "message_id": "<msg-101@example.invalid>",
        "route_target": "chronos_commitment",
        "from_address": "appointments@clinic.example.invalid",
        "to_address": "alex@example.invalid",
        "subject": "Dentist Checkup",
        "received_at": "2026-11-26T01:00:00Z",
        "text_body": "Reminder for Dentist Checkup on 2026-11-28 at 09:30.",
        "html_excerpt": None,
        "raw_excerpt": None,
        "jev_verdict": {"route_target": "chronos_commitment", "confidence": 0.96},
        "attachments": [],
    }
    payload.update(overrides)
    return payload


def test_integrations_email_auth_enforces_bearer_and_never_trusts_authentik(
    test_session_factory: sessionmaker[Session], monkeypatch
) -> None:
    monkeypatch.setattr(deps.settings, "environment", "production")
    monkeypatch.setattr(deps.settings, "webhook_secret", "")

    with TestClient(app) as client:
        unconfigured = client.post("/integrations/email", json=_base_email_payload())
        assert unconfigured.status_code == 503

        monkeypatch.setattr(deps.settings, "webhook_secret", "synthetic-webhook-secret")

        missing_header = client.post("/integrations/email", json=_base_email_payload())
        assert missing_header.status_code == 401

        wrong_token = client.post(
            "/integrations/email",
            headers={"Authorization": "Bearer wrong-secret"},
            json=_base_email_payload(),
        )
        assert wrong_token.status_code == 401

        authentik_spoof = client.post(
            "/integrations/email",
            headers={
                "X-authentik-username": "alex.tan",
                "X-authentik-email": "alex@example.invalid",
            },
            json=_base_email_payload(),
        )
        assert authentik_spoof.status_code == 401

        valid = client.post(
            "/integrations/email",
            headers={"Authorization": "Bearer synthetic-webhook-secret"},
            json=_base_email_payload(
                schedule_event={
                    "title": "Dentist Checkup",
                    "target_date": "2026-11-28",
                    "start_minute": 570,
                    "end_minute": 630,
                }
            ),
        )
        assert valid.status_code == 201
        assert valid.json()["status"] == "created"
        assert valid.json()["commitment"]["tentative"] is True


def test_integrations_email_ics_ingestion_idempotency_confirm_and_dismiss(
    test_session_factory: sessionmaker[Session], monkeypatch
) -> None:
    monkeypatch.setattr(deps.settings, "environment", "development")
    monkeypatch.setattr(deps.settings, "webhook_secret", "synthetic-webhook-secret")
    fake_extractor = FakeScheduleExtractor()
    app.dependency_overrides[deps.get_schedule_extractor] = lambda: fake_extractor

    ics_content = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "BEGIN:VEVENT\r\n"
        "SUMMARY:Flight NZ502 to Wellington\r\n"
        "DTSTART:20261129T000000Z\r\n"
        "DTEND:20261129T011500Z\r\n"
        "LOCATION:Auckland Airport\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    ).encode("utf-8")

    payload = _base_email_payload(
        message_id="<flight-502@airline.example.invalid>",
        subject="Your flight itinerary",
        attachments=[
            {
                "filename": "flight.ics",
                "content_type": "text/calendar; method=REQUEST",
                "size_bytes": len(ics_content),
                "content_base64": base64.b64encode(ics_content).decode("ascii"),
            }
        ],
    )

    with TestClient(app) as client:
        first = client.post(
            "/integrations/email",
            headers={"Authorization": "Bearer synthetic-webhook-secret"},
            json=payload,
        )
        assert first.status_code == 201
        body_1 = first.json()
        assert body_1["status"] == "created"
        commitment = body_1["commitment"]
        cid = commitment["id"]
        assert body_1["consumer_ref"] == f"commitment:{cid}"
        assert commitment["title"] == "Flight NZ502 to Wellington"
        # 00:00 UTC on 2026-11-29 -> 13:00 NZDT (780m..855m)
        assert commitment["target_date"] == "2026-11-29"
        assert commitment["start_minute"] == 780
        assert commitment["end_minute"] == 855
        assert commitment["source"] == "iris"
        assert commitment["external_ref"] == "iris:<flight-502@airline.example.invalid>"
        assert commitment["tentative"] is True
        assert commitment["nudge_mode"] == "two-stage"
        assert fake_extractor.calls == []

        # Duplicate dispatch returns 200 OK with same consumer_ref and zero LLM call
        duplicate = client.post(
            "/integrations/email",
            headers={"Authorization": "Bearer synthetic-webhook-secret"},
            json=payload,
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["status"] == "duplicate"
        assert duplicate.json()["consumer_ref"] == f"commitment:{cid}"
        assert fake_extractor.calls == []

        # Confirm tentative commitment via PATCH {"tentative": false}
        confirmed = client.patch(f"/commitments/{cid}", json={"tentative": False})
        assert confirmed.status_code == 200
        assert confirmed.json()["tentative"] is False
        assert confirmed.json()["title"] == "Flight NZ502 to Wellington"

        day_res = client.get("/schedule/day", params={"target_date": "2026-11-29"})
        assert day_res.status_code == 200
        commit_slices = [s for s in day_res.json()["slices"] if s["kind"] == "commitment"]
        assert len(commit_slices) == 1
        assert commit_slices[0]["tentative"] is False

        # Dismiss via DELETE
        deleted = client.delete(f"/commitments/{cid}")
        assert deleted.status_code == 204
        assert client.get("/commitments").json() == []


def test_integrations_email_muninn_adapter_unschedulable_and_upstream_error(
    test_session_factory: sessionmaker[Session], monkeypatch
) -> None:
    monkeypatch.setattr(deps.settings, "environment", "development")
    monkeypatch.setattr(deps.settings, "webhook_secret", "synthetic-webhook-secret")

    def _muninn_ok(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer muninn-secret"
        req_json = json.loads(request.content.decode("utf-8"))
        assert req_json["model"] == "app-synthesize"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "schedulable": True,
                                    "title": "All Day Offsite",
                                    "target_date": "2026-12-01",
                                    "start_minute": None,
                                    "end_minute": None,
                                    "all_day": True,
                                    "notes": "Booked by Alex Tan",
                                }
                            )
                        }
                    }
                ]
            },
        )

    adapter = MuninnScheduleExtractorAdapter(
        api_base="http://muninn:4000/v1",
        api_key="muninn-secret",
        model="app-synthesize",
        client=httpx.Client(transport=httpx.MockTransport(_muninn_ok), base_url="http://muninn:4000/v1"),
    )
    app.dependency_overrides[deps.get_schedule_extractor] = lambda: adapter

    with TestClient(app) as client:
        created = client.post(
            "/integrations/email",
            headers={"Authorization": "Bearer synthetic-webhook-secret"},
            json=_base_email_payload(message_id="<muninn-ok@example.invalid>"),
        )
        assert created.status_code == 201
        assert created.json()["commitment"]["all_day"] is True
        assert created.json()["commitment"]["nudge_mode"] == "none"
        assert created.json()["commitment"]["tentative"] is True

        # NullScheduleExtractor -> 422 UnschedulableEmailError
        app.dependency_overrides[deps.get_schedule_extractor] = NullScheduleExtractor
        unschedulable = client.post(
            "/integrations/email",
            headers={"Authorization": "Bearer synthetic-webhook-secret"},
            json=_base_email_payload(message_id="<unschedulable@example.invalid>"),
        )
        assert unschedulable.status_code == 422

        # Upstream 503 from Muninn -> 502 Bad Gateway
        failing_adapter = MuninnScheduleExtractorAdapter(
            api_base="http://muninn:4000/v1",
            api_key="muninn-secret",
            client=httpx.Client(
                transport=httpx.MockTransport(lambda _req: httpx.Response(503, text="down")),
                base_url="http://muninn:4000/v1",
            ),
        )
        app.dependency_overrides[deps.get_schedule_extractor] = lambda: failing_adapter
        upstream_err = client.post(
            "/integrations/email",
            headers={"Authorization": "Bearer synthetic-webhook-secret"},
            json=_base_email_payload(message_id="<upstream-fail@example.invalid>"),
        )
        assert upstream_err.status_code == 502


def test_email_ingestion_handles_concurrent_integrity_error_as_duplicate(
    test_session_factory: sessionmaker[Session],
) -> None:
    with test_session_factory() as db:
        repo = SqlAlchemyScheduleRepository(db)
        seeded = repo.create_commitment(
            title="Dentist Checkup",
            target_date=date(2026, 11, 28),
            start_minute=570,
            end_minute=630,
            all_day=False,
            source=CommitmentSource.IRIS,
            external_ref="iris:<race-1@example.invalid>",
            nudge_mode=NudgeMode.TWO_STAGE,
            urgent_quiet_bypass=False,
            tentative=True,
            notes=None,
        )

    class RaceRepo:
        def __init__(self, existing: Commitment) -> None:
            self._existing = existing
            self._calls = 0

        def get_commitment_by_source_external_ref(
            self, source: CommitmentSource, external_ref: str
        ) -> Commitment | None:
            del source, external_ref
            self._calls += 1
            return None if self._calls == 1 else self._existing

        def create_commitment(self, **_kwargs: object) -> Commitment:
            raise IntegrityError("INSERT", {}, Exception("UNIQUE constraint failed"))

    service = EmailIngestionService(RaceRepo(seeded), NullScheduleExtractor())  # type: ignore[arg-type]
    result = service.ingest_email(
        InboundEmailPayload(
            message_id="<race-1@example.invalid>",
            schedule_event=ScheduleEventPayload(
                title="Dentist Checkup",
                target_date=date(2026, 11, 28),
                start_minute=570,
                end_minute=630,
            ),
        )
    )
    assert result.status == "duplicate"
    assert result.consumer_ref == f"commitment:{seeded.id}"


def test_muninn_schedule_extractor_defaults_to_60s_timeout() -> None:
    adapter = MuninnScheduleExtractorAdapter(
        api_base="http://muninn:4000/v1",
        api_key="synthetic-muninn-key",
    )
    assert adapter._timeout_seconds == 60.0


def test_settings_muninn_timeout_seconds_default_env_and_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CHRONOS_MUNINN_TIMEOUT_SECONDS", raising=False)
    default_settings = Settings()
    assert default_settings.muninn_timeout_seconds == 60.0

    monkeypatch.setenv("CHRONOS_MUNINN_TIMEOUT_SECONDS", "90.5")
    env_settings = Settings()
    assert env_settings.muninn_timeout_seconds == 90.5

    for invalid_value in ("0", "-5"):
        monkeypatch.setenv("CHRONOS_MUNINN_TIMEOUT_SECONDS", invalid_value)
        with pytest.raises(ValueError, match="CHRONOS_MUNINN_TIMEOUT_SECONDS must be > 0"):
            Settings()


def test_get_schedule_extractor_passes_muninn_timeout_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deps.settings, "muninn_api_base", "http://muninn:4000/v1")
    monkeypatch.setattr(deps.settings, "muninn_api_key", "synthetic-muninn-key")
    monkeypatch.setattr(deps.settings, "muninn_timeout_seconds", 75.0)
    configured = deps.get_schedule_extractor()
    assert isinstance(configured, MuninnScheduleExtractorAdapter)
    assert configured._timeout_seconds == 75.0

    monkeypatch.setattr(deps.settings, "environment", "production")
    monkeypatch.setattr(deps.settings, "muninn_api_base", "")
    monkeypatch.setattr(deps.settings, "muninn_api_key", "")
    monkeypatch.setattr(deps.settings, "muninn_timeout_seconds", 45.0)
    fallback = deps.get_schedule_extractor()
    assert isinstance(fallback, MuninnScheduleExtractorAdapter)
    assert fallback._timeout_seconds == 45.0
