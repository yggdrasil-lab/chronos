"""Machine-to-machine integration endpoints (Iris email ingestion and Odin daily summary)."""

from datetime import date
import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from app.config import settings
from app.routers.deps import get_daily_summary_service, get_email_ingestion_service
from app.schemas.integrations import (
    DailySummaryResponse,
    EmailIngestionResponse,
    InboundEmailPayload,
)
from app.schemas.schedule import CommitmentResponse
from app.services.daily_summary_service import DailySummaryService
from app.services.email_ingestion import (
    EmailIngestionService,
    ScheduleExtractionUpstreamError,
    UnschedulableEmailError,
)

router = APIRouter(prefix="/integrations", tags=["Integrations"])


def verify_integration_token(
    authorization: str | None = Header(default=None),
) -> None:
    """Validate Bearer token with constant-time comparison; never trust X-authentik-*."""
    expected = settings.webhook_secret.strip()
    if not expected:
        if settings.environment.strip().lower() != "development":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Chronos webhook secret is not configured.",
            )
        return
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Bearer token.",
        )
    candidate = authorization[7:].strip()
    if not candidate or not hmac.compare_digest(candidate, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Bearer token.",
        )


@router.post(
    "/email",
    response_model=EmailIngestionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_integration_token)],
)
def ingest_email_commitment(
    payload: InboundEmailPayload,
    response: Response,
    service: EmailIngestionService = Depends(get_email_ingestion_service),
) -> EmailIngestionResponse:
    """Ingest an Iris email dispatch into an idempotent tentative commitment."""
    try:
        result = service.ingest_email(payload)
    except UnschedulableEmailError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except ScheduleExtractionUpstreamError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    if result.status == "duplicate":
        response.status_code = status.HTTP_200_OK
    return EmailIngestionResponse(
        status=result.status,
        consumer_ref=result.consumer_ref,
        commitment=CommitmentResponse.from_domain(result.commitment),
    )


@router.get("/daily-summary", response_model=DailySummaryResponse)
def get_daily_summary(
    target_date: date | None = Query(default=None),
    service: DailySummaryService = Depends(get_daily_summary_service),
) -> DailySummaryResponse:
    """Return unauthenticated internal aether-net summary for Odin morning copilot."""
    return service.build_daily_summary(target_date=target_date)
