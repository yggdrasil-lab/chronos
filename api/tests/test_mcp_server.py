"""Tests for Chronos Bearer-authenticated /mcp endpoint, tools, and Traefik labels."""

from collections.abc import Generator
from datetime import date
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db.session import Base, get_db
from app.domain.models import NudgeMode
from app.domain.passive_signals import KratosDaySnapshot, PeerFetchStatus
from app.main import app
from app.repositories.sqlalchemy_schedule_repo import (
    SqlAlchemyHabitCueRepo,
    SqlAlchemyScheduleRepository,
)
from app.routers import deps

MCP_URL = "/mcp"
PROTOCOL_VERSION = "2025-06-18"
REPO_ROOT = Path(__file__).resolve().parents[2]


class _StubKratosGateway:
    def fetch_day_snapshot(self, target_date: date) -> KratosDaySnapshot:
        del target_date
        return KratosDaySnapshot(status=PeerFetchStatus(available=True))


def _rpc(
    client: TestClient,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    token: str | None = "synthetic-mcp-token",
    request_id: int = 1,
) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "id": request_id}
    if params is not None:
        body["params"] = params

    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"

    response = client.post(MCP_URL, json=body, headers=headers)
    assert response.status_code == 200, response.text
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:") :].strip())
        raise AssertionError(f"No data frame in event stream: {response.text!r}")
    return response.json()


def _initialize(client: TestClient) -> dict[str, Any]:
    return _rpc(
        client,
        "initialize",
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "chronos-tests", "version": "0.1.0"},
        },
    )


def _call_tool(client: TestClient, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return _rpc(client, "tools/call", {"name": name, "arguments": arguments})


@pytest.fixture()
def mcp_client(monkeypatch) -> Generator[TestClient, None, None]:
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
    app.dependency_overrides[deps.get_kratos_gateway] = _StubKratosGateway

    with TestingSessionLocal() as db:
        sched_repo = SqlAlchemyScheduleRepository(db)
        cue_repo = SqlAlchemyHabitCueRepo(db)
        block = sched_repo.create_grid_block(
            title="Morning Focus",
            day_of_week=3,
            start_minute=540,
            end_minute=660,
            category="focus",
            nudge_mode=NudgeMode.BOUNDARY,
            flexible=False,
            active=True,
            notes=None,
        )
        cue_repo.create_cue(
            title="Desk Setup",
            zone_chain="desk-anchor",
            chain_order=0,
            passive_rule=None,
            active=True,
            notes=None,
            grid_block_ids=(block.id,),
        )

    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_mcp_auth_enforces_bearer_rejects_authentik_and_fails_closed(
    mcp_client: TestClient, monkeypatch
) -> None:
    missing = mcp_client.post(MCP_URL, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert missing.status_code == 401
    assert missing.headers.get("WWW-Authenticate") == "Bearer"

    wrong = mcp_client.post(
        MCP_URL,
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert wrong.status_code == 401

    forged = mcp_client.post(
        MCP_URL,
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={
            "X-authentik-username": "alex.tan",
            "X-authentik-email": "alex@example.invalid",
            "X-authentik-groups": "admins",
            "X-authentik-name": "Alex Tan",
            "X-authentik-uid": "uid-1001",
        },
    )
    assert forged.status_code == 401

    subpath = mcp_client.post(
        "/mcp/extra",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert subpath.status_code == 401

    # REST endpoints remain unblocked by MCP Bearer middleware
    assert mcp_client.get("/health").status_code == 200

    monkeypatch.setattr(deps.settings, "mcp_token", "")
    unconfigured = mcp_client.post(
        MCP_URL,
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={"Authorization": "Bearer synthetic-mcp-token"},
    )
    assert unconfigured.status_code == 503


def test_mcp_exposes_strictly_five_spec_tools_and_round_trips(mcp_client: TestClient) -> None:
    init = _initialize(mcp_client)
    assert init["result"]["serverInfo"]["name"] == "chronos"

    listed = _rpc(mcp_client, "tools/list")
    tool_names = {tool["name"] for tool in listed["result"]["tools"]}
    assert tool_names == {
        "get_today_now_next",
        "get_schedule",
        "create_commitment",
        "log_friction_note",
        "get_weekly_friction_summary",
    }

    created = _call_tool(
        mcp_client,
        "create_commitment",
        {
            "title": "Dentist Checkup",
            "target_date": "2026-11-26",
            "start_minute": 570,
            "end_minute": 630,
            "notes": "Synthetic clinic visit",
        },
    )
    created_payload = json.loads(created["result"]["content"][0]["text"])
    assert created_payload["title"] == "Dentist Checkup"
    assert created_payload["source"] == "manual"

    schedule = _call_tool(mcp_client, "get_schedule", {"target_date": "2026-11-26"})
    schedule_payload = json.loads(schedule["result"]["content"][0]["text"])
    assert any(c["title"] == "Dentist Checkup" for c in schedule_payload["timed_commitments"])

    now_next = _call_tool(mcp_client, "get_today_now_next", {"target_date": "2026-11-26"})
    now_next_payload = json.loads(now_next["result"]["content"][0]["text"])
    assert now_next_payload["target_date"] == "2026-11-26"

    friction = _call_tool(
        mcp_client,
        "log_friction_note",
        {
            "note": "Started late after morning errands",
            "tag": "schedule_overrun",
            "target_date": "2026-11-26",
            "habit_cue_id": 1,
            "grid_block_id": 1,
        },
    )
    friction_payload = json.loads(friction["result"]["content"][0]["text"])
    assert friction_payload["tag"] == "schedule_overrun"
    assert friction_payload["cue_title"] == "Desk Setup"

    summary = _call_tool(
        mcp_client,
        "get_weekly_friction_summary",
        {"week_start": "2026-11-23"},
    )
    summary_payload = json.loads(summary["result"]["content"][0]["text"])
    assert summary_payload["total_notes"] == 1
    assert summary_payload["by_tag"][0]["tag"] == "schedule_overrun"

    invalid_tag = _call_tool(
        mcp_client,
        "log_friction_note",
        {"note": "Bad tag", "tag": "unknown_tag", "target_date": "2026-11-26"},
    )
    assert invalid_tag["result"]["isError"] is True


def test_compose_authentik_header_stripping_and_production_settings(tmp_path: Path) -> None:
    compose_candidates = (REPO_ROOT / "docker-compose.yml", Path("/docker-compose.yml"))
    compose_path = next(p for p in compose_candidates if p.is_file())
    compose_text = compose_path.read_text(encoding="utf-8")
    assert ":-" not in compose_text
    assert "/integrations" not in compose_text

    required_headers = (
        "X-authentik-username=",
        "X-authentik-email=",
        "X-authentik-groups=",
        "X-authentik-name=",
        "X-authentik-uid=",
    )
    for header in required_headers:
        assert (
            f"traefik.http.middlewares.chronos-strip-authentik.headers.customrequestheaders.{header}"
            in compose_text
        )

    assert "traefik.http.routers.chronos-ics.rule=Host(`chronos.${DOMAIN_NAME}`) && PathPrefix(`/ics/`)" in compose_text
    assert "traefik.http.routers.chronos-ics.middlewares=chronos-strip-authentik" in compose_text
    assert "traefik.http.routers.chronos-mcp.rule=Host(`chronos.${DOMAIN_NAME}`) && PathPrefix(`/mcp`)" in compose_text
    assert "traefik.http.routers.chronos-mcp.middlewares=chronos-strip-authentik" in compose_text

    ics_file = tmp_path / "ics_tok"
    mcp_file = tmp_path / "mcp_tok"
    db_file = tmp_path / "db_pw"
    vapid_file = tmp_path / "vapid_priv"
    webhook_file = tmp_path / "wh_sec"
    muninn_file = tmp_path / "muninn_key"
    for path, val in (
        (ics_file, "ics-secret"),
        (mcp_file, "mcp-secret"),
        (db_file, "db-secret"),
        (vapid_file, "vapid-secret"),
        (webhook_file, "wh-secret"),
        (muninn_file, "muninn-secret"),
    ):
        path.write_text(val, encoding="utf-8")

    prod_settings = Settings(
        environment="production",
        db_password_file=str(db_file),
        vapid_private_key_file=str(vapid_file),
        vapid_public_key="pub",
        vapid_subject="mailto:chronos@example.invalid",
        eir_base_url="http://eir-api:8000",
        kratos_base_url="http://kratos-api:8000",
        plutus_base_url="http://plutus-api:8000",
        webhook_secret_file=str(webhook_file),
        muninn_api_base="http://muninn:4000/v1",
        muninn_api_key_file=str(muninn_file),
        ics_token_file=str(ics_file),
        mcp_token_file=str(mcp_file),
    )
    assert prod_settings.ics_token == "ics-secret"
    assert prod_settings.mcp_token == "mcp-secret"

    with pytest.raises(ValueError, match="CHRONOS_ICS_TOKEN"):
        Settings(
            environment="production",
            db_password="db",
            vapid_private_key="priv",
            vapid_public_key="pub",
            vapid_subject="mailto:chronos@example.invalid",
            eir_base_url="http://eir-api:8000",
            kratos_base_url="http://kratos-api:8000",
            plutus_base_url="http://plutus-api:8000",
            webhook_secret="wh",
            muninn_api_base="http://muninn:4000/v1",
            muninn_api_key="mk",
            mcp_token="mcp",
        )
