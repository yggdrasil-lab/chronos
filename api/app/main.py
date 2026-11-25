"""FastAPI entrypoint for Chronos local-first time engine."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.config import settings
from app.db.session import SessionLocal
from app.domain.errors import (
    EntityNotFoundError,
    ScheduleConflictError,
    ScheduleValidationError,
)
from app.routers import (
    commitments,
    cues,
    exceptions,
    friction,
    health,
    push,
    schedule,
    weekly_grid,
)
from app.routers.deps import build_push_service

logger = logging.getLogger(__name__)


def _run_push_tick_with_session() -> int:
    with SessionLocal() as db:
        return build_push_service(db).run_scheduled_tick()


async def _push_scheduler_loop(poll_interval_seconds: int) -> None:
    while True:
        try:
            await asyncio.to_thread(_run_push_tick_with_session)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scheduled Web Push tick failed")
        await asyncio.sleep(poll_interval_seconds)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Start background Web Push polling loop when enabled and VAPID keys are configured."""
    task: asyncio.Task[None] | None = None
    if settings.push_scheduler_enabled and settings.vapid_configured:
        task = asyncio.create_task(_push_scheduler_loop(settings.push_poll_interval_seconds))
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(
    title="Chronos API",
    description="Headless local-first Time Engine for Yggdrasil.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.exception_handler(ScheduleValidationError)
async def _handle_validation_error(_request: Request, exc: ScheduleValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": str(exc)},
    )


@app.exception_handler(EntityNotFoundError)
async def _handle_not_found_error(_request: Request, exc: EntityNotFoundError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": str(exc)},
    )


@app.exception_handler(ScheduleConflictError)
async def _handle_conflict_error(_request: Request, exc: ScheduleConflictError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": str(exc)},
    )


app.include_router(health.router)
app.include_router(weekly_grid.router)
app.include_router(exceptions.router)
app.include_router(commitments.router)
app.include_router(schedule.router)
app.include_router(cues.router)
app.include_router(friction.router)
app.include_router(push.router)
