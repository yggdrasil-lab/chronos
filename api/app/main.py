"""FastAPI entrypoint for Chronos local-first time engine."""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.domain.errors import EntityNotFoundError, ScheduleValidationError
from app.routers import commitments, cues, exceptions, friction, health, schedule, weekly_grid

app = FastAPI(
    title="Chronos API",
    description="Headless local-first Time Engine for Yggdrasil.",
    version="0.1.0",
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


app.include_router(health.router)
app.include_router(weekly_grid.router)
app.include_router(exceptions.router)
app.include_router(commitments.router)
app.include_router(schedule.router)
app.include_router(cues.router)
app.include_router(friction.router)
