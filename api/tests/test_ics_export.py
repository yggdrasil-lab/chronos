"""Tests for pure RFC 5545 ICS projection, IcsService, and /ics/{token}.ics endpoint."""

from collections.abc import Generator
from datetime import date, datetime
import re

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base, get_db
from app.domain.ics_exporter import IcsEvent, render_ics_calendar
from app.domain.models import CommitmentSource, NudgeMode
from app.domain.passive_signals import (
    KratosDaySnapshot,
    KratosPlannedDay,
    KratosSessionSnapshot,
    PeerFetchStatus,
)
from app.main import app
from app.repositories.sqlalchemy_schedule_repo import SqlAlchemyScheduleRepository
from app.routers import deps
from app.services.email_ingestion.ics_parser import parse_ics_event
from app.services.ics_service import IcsService


class _CountingKratosGateway:
    def __init__(self, snapshot: KratosDaySnapshot) -> None:
        self._snapshot = snapshot
        self.calls: list[date] = []

    def fetch_day_snapshot(self, target_date: date) -> KratosDaySnapshot:
        self.calls.append(target_date)
        return self._snapshot


class _TimeoutKratosGateway:
    def fetch_day_snapshot(self, target_date: date) -> KratosDaySnapshot:
        del target_date
        raise httpx.ReadTimeout("kratos timed out")


def _extract_vevents(raw_ics: str) -> list[dict[str, str]]:
    unfolded = re.sub(r"\r\n[ \t]", "", raw_ics)
    events: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in unfolded.split("\r\n"):
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current is not None:
                events.append(current)
            current = None
            continue
        if current is not None and ":" in line:
            key, val = line.split(":", 1)
            current[key] = val
    return events


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


def test_render_ics_calendar_rfc5545_folding_escaping_and_roundtrip() -> None:
    long_summary = ("Dentist Checkup; Bring X-Rays, Referral\\ID & " + ("LongNote-" * 20))[:200]
    events = [
        IcsEvent(
            uid="commitment-1@chronos.local",
            summary=long_summary,
            target_date=date(2026, 11, 26),
            start_minute=570,
            end_minute=630,
            all_day=False,
            description="Line 1\nLine 2; semi, comma\\slash",
            categories=("chronos:manual",),
        ),
        IcsEvent(
            uid="commitment-2@chronos.local",
            summary="Public Holiday",
            target_date=date(2026, 11, 27),
            all_day=True,
        ),
    ]

    rendered = render_ics_calendar(
        events,
        calendar_name="Chronos Feed",
        timezone="Pacific/Auckland",
    )

    assert rendered.endswith("\r\n")
    assert "\n" not in rendered.replace("\r\n", "")
    physical_lines = rendered.split("\r\n")[:-1]
    assert all(len(line.encode("utf-8")) <= 75 for line in physical_lines)

    parsed_events = _extract_vevents(rendered)
    assert len(parsed_events) == 2
    assert parsed_events[0]["UID"] == "commitment-1@chronos.local"
    assert parsed_events[0]["DTSTART"] == "20261125T203000Z"
    assert parsed_events[0]["DTEND"] == "20261125T213000Z"
    assert parsed_events[1]["DTSTART;VALUE=DATE"] == "20261127"
    assert parsed_events[1]["DTEND;VALUE=DATE"] == "20261128"

    roundtrip = parse_ics_event(rendered, timezone_name="Pacific/Auckland")
    assert roundtrip is not None
    assert roundtrip.title == long_summary[:200]
    assert roundtrip.target_date == date(2026, 11, 26)
    assert roundtrip.start_minute == 570
    assert roundtrip.end_minute == 630


def test_ics_service_default_vs_include_grid_and_kratos_deduplication(
    test_session_factory: sessionmaker[Session],
) -> None:
    ref_date = date(2026, 11, 26)  # Thursday (weekday=3)
    with test_session_factory() as db:
        repo = SqlAlchemyScheduleRepository(db)
        block = repo.create_grid_block(
            title="Deep Work",
            day_of_week=3,
            start_minute=540,
            end_minute=720,
            category="focus",
            nudge_mode=NudgeMode.BOUNDARY,
            flexible=False,
            active=True,
            notes="Morning focus block",
        )
        skipped_block = repo.create_grid_block(
            title="Admin Cleanup",
            day_of_week=3,
            start_minute=780,
            end_minute=840,
            category="admin",
            nudge_mode=NudgeMode.NONE,
            flexible=True,
            active=True,
            notes=None,
        )
        repo.upsert_exception(
            grid_block_id=skipped_block.id,
            target_date=ref_date,
            skipped=True,
            start_minute=None,
            end_minute=None,
            extension_count=0,
            notes="Skipped today",
        )
        confirmed = repo.create_commitment(
            title="Dentist Checkup",
            target_date=ref_date,
            start_minute=600,
            end_minute=660,
            all_day=False,
            source=CommitmentSource.MANUAL,
            external_ref=None,
            nudge_mode=NudgeMode.TWO_STAGE,
            urgent_quiet_bypass=False,
            tentative=False,
            notes="City Clinic",
        )
        tentative = repo.create_commitment(
            title="Tentative Dinner",
            target_date=ref_date,
            start_minute=1140,
            end_minute=1200,
            all_day=False,
            source=CommitmentSource.IRIS,
            external_ref="<msg-tentative@example.invalid>",
            nudge_mode=NudgeMode.BOUNDARY,
            urgent_quiet_bypass=False,
            tentative=True,
            notes=None,
        )

        kratos_gw = _CountingKratosGateway(
            KratosDaySnapshot(
                status=PeerFetchStatus(available=True),
                planned_days=(
                    KratosPlannedDay(
                        day="Thu",
                        day_index=3,
                        session_name="Upper Push A",
                        workout_type="strength",
                        emphasis="Chest & Shoulders",
                    ),
                ),
                sessions=(
                    KratosSessionSnapshot(
                        session_id="sess-901",
                        routine_name="Upper Push A",
                        activity_type="strength",
                        started_at="2026-11-26T17:30:00+13:00",
                        ended_at="2026-11-26T18:30:00+13:00",
                    ),
                ),
            )
        )

        service = IcsService(repo, timezone_name="Pacific/Auckland", kratos_gateway=kratos_gw)

        default_feed = service.build_feed(include_grid=False, reference_date=ref_date)
        # 38-day window [-7d, +30d] spans at most 7 ISO weeks, never 38 calls
        assert len(kratos_gw.calls) <= 7
        default_events = _extract_vevents(default_feed)
        default_uids = [evt["UID"] for evt in default_events]
        assert len(default_uids) == len(set(default_uids))
        assert f"commitment-{confirmed.id}@chronos.local" in default_uids
        assert f"commitment-{tentative.id}@chronos.local" not in default_uids
        assert "kratos-session-sess-901@chronos.local" in default_uids
        assert "kratos-plan-2026-11-26@chronos.local" in default_uids
        assert not any(uid.startswith("grid-") for uid in default_uids)

        grid_feed = service.build_feed(include_grid=True, reference_date=ref_date)
        grid_events = _extract_vevents(grid_feed)
        grid_uids = [evt["UID"] for evt in grid_events]
        assert len(grid_uids) == len(set(grid_uids))
        # Deep Work (09:00-12:00) carved by Dentist (10:00-11:00) into 2 slices on 2026-11-26
        assert f"grid-{block.id}-2026-11-26@chronos.local" in grid_uids
        assert f"grid-{block.id}-2026-11-26-660@chronos.local" in grid_uids
        # Skipped Admin Cleanup must NOT appear on 2026-11-26
        assert f"grid-{skipped_block.id}-2026-11-26@chronos.local" not in grid_uids


def test_ics_endpoint_auth_headers_and_kratos_resilience(
    test_session_factory: sessionmaker[Session], monkeypatch
) -> None:
    monkeypatch.setattr(deps.settings, "environment", "production")
    monkeypatch.setattr(deps.settings, "ics_token", "")
    app.dependency_overrides[deps.get_kratos_gateway] = _TimeoutKratosGateway

    with TestClient(app) as client:
        unconfigured = client.get("/ics/any-token.ics")
        assert unconfigured.status_code == 503

        monkeypatch.setattr(deps.settings, "ics_token", "synthetic-ics-token")

        bad = client.get("/ics/wrong-token.ics")
        assert bad.status_code == 401

        forged = client.get(
            "/ics/wrong-token.ics",
            headers={
                "X-authentik-username": "alex.tan",
                "X-authentik-email": "alex@example.invalid",
            },
        )
        assert forged.status_code == 401

        ok = client.get("/ics/synthetic-ics-token.ics")
        assert ok.status_code == 200
        assert ok.headers["content-type"].startswith("text/calendar")
        assert "charset=utf-8" in ok.headers["content-type"].lower()
        assert ok.headers.get("cache-control") == "no-store"
        assert "BEGIN:VCALENDAR" in ok.text
        assert "END:VCALENDAR" in ok.text
