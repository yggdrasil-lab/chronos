"""Shared FastAPI dependency wiring for Chronos routers."""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.repositories.sqlalchemy_schedule_repo import (
    SqlAlchemyHabitCueRepo,
    SqlAlchemyScheduleRepository,
)
from app.services.cue_service import CueService
from app.services.schedule_service import ScheduleService


def get_schedule_service(db: Session = Depends(get_db)) -> ScheduleService:
    """Construct a ScheduleService backed by the request's SQLAlchemy session."""
    return ScheduleService(SqlAlchemyScheduleRepository(db), timezone_name=settings.timezone)


def get_cue_service(db: Session = Depends(get_db)) -> CueService:
    """Construct a CueService backed by the request's SQLAlchemy session."""
    return CueService(SqlAlchemyHabitCueRepo(db), timezone_name=settings.timezone)
