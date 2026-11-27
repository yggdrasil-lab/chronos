"""Bearer-authenticated MCP endpoint for Chronos mounted at /mcp."""

from __future__ import annotations

from collections.abc import Callable, Iterator
import contextlib
from datetime import date, datetime
import hmac
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy.orm import Session
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import settings
from app.db.session import get_db
from app.domain.errors import (
    EntityNotFoundError,
    ScheduleConflictError,
    ScheduleValidationError,
)
from app.domain.models import CommitmentSource, FrictionTag
from app.repositories.sqlalchemy_schedule_repo import (
    SqlAlchemyHabitCueRepo,
    SqlAlchemyScheduleRepository,
)
from app.routers.deps import get_eir_vitals_gateway, get_kratos_gateway
from app.schemas.cues import FrictionNoteResponse, WeeklyReflectionResponse
from app.schemas.schedule import (
    CommitmentResponse,
    DayScheduleResponse,
    NowNextResponse,
)
from app.services.cue_service import CueService
from app.services.peer_gateways import EirVitalsGateway, KratosGateway
from app.services.schedule_service import ScheduleService

SessionOpener = Callable[[], contextlib.AbstractContextManager[Session]]
KratosGetter = Callable[[], KratosGateway]
EirVitalsGetter = Callable[[], EirVitalsGateway]


def _extract_authorization_header(scope: Scope) -> str:
    for name, value in scope.get("headers", []):
        if name.lower() == b"authorization":
            return value.decode("latin-1")
    return ""


class McpAuthMiddleware:
    """Pure ASGI Bearer auth guard for /mcp and /mcp/*; never trusts X-authentik-*."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path != "/mcp" and not path.startswith("/mcp/"):
            await self.app(scope, receive, send)
            return

        expected = settings.mcp_token.strip()
        if not expected:
            if settings.environment.strip().lower() != "development":
                response = JSONResponse(
                    {"detail": "Chronos MCP token is not configured."},
                    status_code=503,
                )
                await response(scope, receive, send)
                return
            await self.app(scope, receive, send)
            return

        scheme, _, candidate = _extract_authorization_header(scope).partition(" ")
        cleaned = candidate.strip()
        if (
            scheme.lower() != "bearer"
            or not cleaned
            or not hmac.compare_digest(cleaned, expected)
        ):
            response = JSONResponse(
                {"detail": "Invalid or missing Bearer token."},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


@contextlib.contextmanager
def _tool_errors() -> Iterator[None]:
    """Translate domain validation and lookup errors into MCP ToolError."""
    try:
        yield
    except (
        ScheduleValidationError,
        EntityNotFoundError,
        ScheduleConflictError,
        ValueError,
    ) as exc:
        raise ToolError(str(exc)) from exc


def _parse_optional_date(raw: str | None, field_name: str) -> date | None:
    if raw is None:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    try:
        return date.fromisoformat(cleaned)
    except ValueError as exc:
        raise ToolError(f"{field_name} must be YYYY-MM-DD, got {raw!r}.") from exc


def _parse_required_date(raw: str, field_name: str) -> date:
    parsed = _parse_optional_date(raw, field_name)
    if parsed is None:
        raise ToolError(f"{field_name} is required in YYYY-MM-DD format.")
    return parsed


def _build_schedule_service(
    db: Session,
    get_kratos: KratosGetter,
    get_eir_vitals: EirVitalsGetter | None = None,
) -> ScheduleService:
    return ScheduleService(
        SqlAlchemyScheduleRepository(db),
        timezone_name=settings.timezone,
        kratos_gateway=get_kratos(),
        eir_vitals_gateway=get_eir_vitals() if get_eir_vitals is not None else None,
    )


def _build_cue_service(db: Session) -> CueService:
    return CueService(SqlAlchemyHabitCueRepo(db), timezone_name=settings.timezone)


def _register_schedule_read_tools(
    server: MCPServer,
    open_session: SessionOpener,
    get_kratos: KratosGetter,
    get_eir_vitals: EirVitalsGetter | None = None,
) -> None:
    @server.tool(
        name="get_today_now_next",
        description="Get active now/next timeline state, upcoming slices, open time, and quiet-hours flag.",
    )
    def get_today_now_next(target_date: str | None = None) -> dict[str, Any]:
        with _tool_errors():
            parsed_date = _parse_optional_date(target_date, "target_date")
            with open_session() as db:
                state = _build_schedule_service(
                    db, get_kratos, get_eir_vitals
                ).get_now_next(target_date=parsed_date)
            return NowNextResponse.from_domain(state).model_dump(mode="json")

    @server.tool(
        name="get_schedule",
        description="Get the full resolved Chronos day schedule including carved grid blocks, commitments, and workout hydration.",
    )
    def get_schedule(target_date: str | None = None) -> dict[str, Any]:
        with _tool_errors():
            parsed_date = _parse_optional_date(target_date, "target_date")
            with open_session() as db:
                schedule = _build_schedule_service(
                    db, get_kratos, get_eir_vitals
                ).get_day_schedule(target_date=parsed_date)
            return DayScheduleResponse.from_domain(schedule).model_dump(mode="json")


def _register_commitment_write_tool(
    server: MCPServer,
    open_session: SessionOpener,
    get_kratos: KratosGetter,
) -> None:
    @server.tool(
        name="create_commitment",
        description="Create a dated manual commitment (all-day or timed with minute-of-day bounds).",
    )
    def create_commitment(
        title: str,
        target_date: str,
        all_day: bool = False,
        start_minute: int | None = None,
        end_minute: int | None = None,
        notes: str | None = None,
        tentative: bool = False,
        urgent_quiet_bypass: bool = False,
    ) -> dict[str, Any]:
        with _tool_errors():
            parsed_date = _parse_required_date(target_date, "target_date")
            with open_session() as db:
                created = _build_schedule_service(db, get_kratos).create_commitment(
                    title=title,
                    target_date=parsed_date,
                    start_minute=start_minute,
                    end_minute=end_minute,
                    all_day=all_day,
                    source=CommitmentSource.MANUAL,
                    external_ref=None,
                    nudge_mode=None,
                    urgent_quiet_bypass=urgent_quiet_bypass,
                    tentative=tentative,
                    notes=notes,
                )
            return CommitmentResponse.from_domain(created).model_dump(mode="json")


def _register_log_friction_tool(server: MCPServer, open_session: SessionOpener) -> None:
    @server.tool(
        name="log_friction_note",
        description="Log a habit friction reflection note tagged with tired, trigger_missed, schedule_overrun, or environment_missing.",
    )
    def log_friction_note(
        note: str,
        tag: str,
        target_date: str | None = None,
        habit_cue_id: int | None = None,
        grid_block_id: int | None = None,
    ) -> dict[str, Any]:
        with _tool_errors():
            try:
                parsed_tag = FrictionTag(tag.strip())
            except ValueError as exc:
                allowed = ", ".join(item.value for item in FrictionTag)
                raise ToolError(f"tag must be one of [{allowed}], got {tag!r}.") from exc
            resolved_date = (
                _parse_optional_date(target_date, "target_date")
                or datetime.now(ZoneInfo(settings.timezone)).date()
            )
            with open_session() as db:
                created = _build_cue_service(db).create_friction_note(
                    target_date=resolved_date,
                    tag=parsed_tag,
                    note=note,
                    habit_cue_id=habit_cue_id,
                    grid_block_id=grid_block_id,
                )
            return FrictionNoteResponse.from_domain(created).model_dump(mode="json")


def _register_weekly_summary_tool(server: MCPServer, open_session: SessionOpener) -> None:
    @server.tool(
        name="get_weekly_friction_summary",
        description="Get the 7-day weekly friction reflection rollup grouped by tag, cue, and unlinked notes.",
    )
    def get_weekly_friction_summary(week_start: str | None = None) -> dict[str, Any]:
        with _tool_errors():
            parsed_week_start = _parse_optional_date(week_start, "week_start")
            with open_session() as db:
                reflection = _build_cue_service(db).get_weekly_reflection(
                    week_start=parsed_week_start
                )
            return WeeklyReflectionResponse.from_domain(reflection).model_dump(mode="json")


def build_mcp_server(
    open_session: SessionOpener,
    get_kratos: KratosGetter,
    get_eir_vitals: EirVitalsGetter | None = None,
) -> MCPServer:
    """Construct the Chronos MCPServer exposing strictly the 5 Spec tools."""
    server = MCPServer(
        name="chronos",
        version="0.1.0",
        instructions=(
            "Chronos local-first time engine. Read now/next and resolved schedules, "
            "create dated commitments, log habit friction notes, and inspect weekly "
            "friction summaries. Weekly grid templates are never modified via MCP."
        ),
    )
    _register_schedule_read_tools(server, open_session, get_kratos, get_eir_vitals)
    _register_commitment_write_tool(server, open_session, get_kratos)
    _register_log_friction_tool(server, open_session)
    _register_weekly_summary_tool(server, open_session)
    return server


class _RebuildableApp:
    """ASGI shim that (re)creates the SDK sub-app for each lifespan startup."""

    def __init__(self, server: MCPServer) -> None:
        self._server = server
        self.app: Starlette | None = None

    def build(self) -> Starlette:
        return self._server.streamable_http_app(
            streamable_http_path="/mcp",
            stateless_http=True,
            json_response=True,
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=False
            ),
        )

    async def __call__(self, scope, receive, send):
        if self.app is None:
            raise RuntimeError("MCP sub-app is not running; lifespan did not start")
        await self.app(scope, receive, send)


def install_mcp(app: FastAPI) -> None:
    """Attach the Chronos MCP server route, lifespan chain, and Bearer auth middleware."""

    @contextlib.contextmanager
    def _open_session() -> Iterator[Session]:
        provider = app.dependency_overrides.get(get_db, get_db)
        gen = provider()
        db = next(gen)
        try:
            yield db
        finally:
            gen.close()

    def _get_kratos() -> KratosGateway:
        provider = app.dependency_overrides.get(get_kratos_gateway, get_kratos_gateway)
        return provider()

    def _get_eir_vitals() -> EirVitalsGateway:
        provider = app.dependency_overrides.get(
            get_eir_vitals_gateway, get_eir_vitals_gateway
        )
        return provider()

    server = build_mcp_server(_open_session, _get_kratos, _get_eir_vitals)
    holder = _RebuildableApp(server)

    app.router.routes.append(Route("/mcp", endpoint=holder))

    original_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(app_: FastAPI):
        sub_app = holder.build()
        holder.app = sub_app
        async with contextlib.AsyncExitStack() as stack:
            if original_lifespan is not None:
                await stack.enter_async_context(original_lifespan(app_))
            await stack.enter_async_context(sub_app.router.lifespan_context(app_))
            yield
        holder.app = None

    app.router.lifespan_context = lifespan
    app.add_middleware(McpAuthMiddleware)
