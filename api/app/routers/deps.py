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
from app.services.daily_summary_service import DailySummaryService
from app.services.email_ingestion import (
    EmailIngestionService,
    MuninnScheduleExtractorAdapter,
    NullScheduleExtractor,
    ScheduleExtractorPort,
)
from app.services.ics_service import IcsService
from app.services.passive_signal_service import PassiveSignalService
from app.services.peer_gateways import (
    EirGateway,
    EirVitalsGateway,
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


def get_eir_vitals_gateway() -> EirVitalsGateway:
    """Construct the Eir daily vitals gateway using runtime settings."""
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
    eir_vitals_gateway: EirVitalsGateway = Depends(get_eir_vitals_gateway),
) -> ScheduleService:
    """Construct a ScheduleService backed by the request's SQLAlchemy session."""
    return ScheduleService(
        SqlAlchemyScheduleRepository(db),
        timezone_name=settings.timezone,
        kratos_gateway=kratos_gateway,
        eir_vitals_gateway=eir_vitals_gateway,
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
        eir_vitals_gateway=get_eir_vitals_gateway(),
    )


def get_push_service(db: Session = Depends(get_db)) -> PushService:
    """FastAPI dependency returning a request-scoped PushService."""
    return build_push_service(db)


def get_schedule_extractor() -> ScheduleExtractorPort:
    """Construct the schedule extractor port using Muninn settings or NullScheduleExtractor."""
    api_base = settings.muninn_api_base.strip()
    api_key = settings.muninn_api_key.strip()
    if api_base and api_key:
        return MuninnScheduleExtractorAdapter(
            api_base=api_base,
            api_key=api_key,
            model=settings.muninn_model,
            timezone_name=settings.timezone,
            timeout_seconds=settings.muninn_timeout_seconds,
        )
    if settings.environment.strip().lower() == "development" and not api_base:
        return NullScheduleExtractor()
    return MuninnScheduleExtractorAdapter(
        api_base=api_base,
        api_key=api_key,
        model=settings.muninn_model,
        timezone_name=settings.timezone,
        timeout_seconds=settings.muninn_timeout_seconds,
    )


def get_email_ingestion_service(
    db: Session = Depends(get_db),
    extractor: ScheduleExtractorPort = Depends(get_schedule_extractor),
) -> EmailIngestionService:
    """Construct an EmailIngestionService backed by the request's SQLAlchemy session."""
    return EmailIngestionService(
        SqlAlchemyScheduleRepository(db),
        extractor,
        timezone_name=settings.timezone,
    )


def get_ics_service(
    db: Session = Depends(get_db),
    kratos_gateway: KratosGateway = Depends(get_kratos_gateway),
) -> IcsService:
    """Construct an IcsService backed by the request's SQLAlchemy session."""
    return IcsService(
        SqlAlchemyScheduleRepository(db),
        timezone_name=settings.timezone,
        kratos_gateway=kratos_gateway,
    )


def get_daily_summary_service(
    schedule_service: ScheduleService = Depends(get_schedule_service),
    cue_service: CueService = Depends(get_cue_service),
    passive_signal_service: PassiveSignalService = Depends(get_passive_signal_service),
) -> DailySummaryService:
    """Construct a DailySummaryService combining schedule, cues, and passive signals."""
    return DailySummaryService(
        schedule_service=schedule_service,
        cue_service=cue_service,
        passive_signal_service=passive_signal_service,
        timezone_name=settings.timezone,
    )
