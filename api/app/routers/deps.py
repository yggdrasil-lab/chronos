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
from app.services.passive_signal_service import PassiveSignalService
from app.services.peer_gateways import (
    EirGateway,
    HttpEirGateway,
    HttpKratosGateway,
    HttpPlutusGateway,
    KratosGateway,
    PlutusGateway,
)
from app.services.push_gateway import PushGateway, PyWebPushGateway
from app.services.push_service import PushService
from app.services.schedule_service import ScheduleService


def get_eir_gateway() -> EirGateway:
    """Construct the Eir peer gateway using runtime settings."""
    return HttpEirGateway(
        base_url=settings.eir_base_url,
        timeout_seconds=settings.peer_timeout_seconds,
    )


def get_kratos_gateway() -> KratosGateway:
    """Construct the Kratos peer gateway using runtime settings."""
    return HttpKratosGateway(
        base_url=settings.kratos_base_url,
        timeout_seconds=settings.peer_timeout_seconds,
    )


def get_plutus_gateway() -> PlutusGateway:
    """Construct the Plutus peer gateway using runtime settings."""
    return HttpPlutusGateway(
        base_url=settings.plutus_base_url,
        timeout_seconds=settings.peer_timeout_seconds,
    )


def get_schedule_service(
    db: Session = Depends(get_db),
    kratos_gateway: KratosGateway = Depends(get_kratos_gateway),
) -> ScheduleService:
    """Construct a ScheduleService backed by the request's SQLAlchemy session."""
    return ScheduleService(
        SqlAlchemyScheduleRepository(db),
        timezone_name=settings.timezone,
        kratos_gateway=kratos_gateway,
    )


def get_passive_signal_service(
    eir_gateway: EirGateway = Depends(get_eir_gateway),
    kratos_gateway: KratosGateway = Depends(get_kratos_gateway),
    plutus_gateway: PlutusGateway = Depends(get_plutus_gateway),
) -> PassiveSignalService:
    """Construct a PassiveSignalService wired to Eir, Kratos, and Plutus peer gateways."""
    return PassiveSignalService(
        eir_gateway=eir_gateway,
        kratos_gateway=kratos_gateway,
        plutus_gateway=plutus_gateway,
        timezone_name=settings.timezone,
    )


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
