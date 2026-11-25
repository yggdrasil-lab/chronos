"""Shared FastAPI dependency wiring for Chronos routers."""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.repositories.sqlalchemy_push_repo import SqlAlchemyPushRepository
from app.repositories.sqlalchemy_schedule_repo import (
    SqlAlchemyHabitCueRepo,
    SqlAlchemyScheduleRepository,
)
from app.services.cue_service import CueService
from app.services.push_gateway import PushGateway, PyWebPushGateway
from app.services.push_service import PushService
from app.services.schedule_service import ScheduleService


def get_schedule_service(db: Session = Depends(get_db)) -> ScheduleService:
    """Construct a ScheduleService backed by the request's SQLAlchemy session."""
    return ScheduleService(SqlAlchemyScheduleRepository(db), timezone_name=settings.timezone)


def get_cue_service(db: Session = Depends(get_db)) -> CueService:
    """Construct a CueService backed by the request's SQLAlchemy session."""
    return CueService(SqlAlchemyHabitCueRepo(db), timezone_name=settings.timezone)


def build_push_service(db: Session) -> PushService:
    """Construct a PushService with VAPID gateway when configured."""
    gateway: PushGateway | None = None
    if settings.vapid_configured:
        gateway = PyWebPushGateway(
            vapid_private_key=settings.vapid_private_key,
            vapid_subject=settings.vapid_subject,
        )
    return PushService(
        schedule_repo=SqlAlchemyScheduleRepository(db),
        push_repo=SqlAlchemyPushRepository(db),
        gateway=gateway,
        vapid_public_key=settings.vapid_public_key,
        timezone_name=settings.timezone,
    )


def get_push_service(db: Session = Depends(get_db)) -> PushService:
    """FastAPI dependency returning a request-scoped PushService."""
    return build_push_service(db)
