"""Token-gated iCalendar (.ics) subscription endpoint."""

import hmac

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.config import settings
from app.routers.deps import get_ics_service
from app.services.ics_service import IcsService

router = APIRouter(prefix="/ics", tags=["ICS Feed"])


def verify_ics_token(token: str) -> None:
    """Validate path token with constant-time comparison; never trust X-authentik-*."""
    expected = settings.ics_token.strip()
    if not expected:
        if settings.environment.strip().lower() != "development":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Chronos ICS token is not configured.",
            )
        return
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid ICS feed token.",
        )


@router.get("/{token}.ics")
def get_ics_feed(
    token: str,
    include_grid: bool = Query(default=False),
    service: IcsService = Depends(get_ics_service),
) -> Response:
    """Render rolling [-7d, +30d] Chronos schedule as an RFC 5545 calendar feed."""
    verify_ics_token(token)
    calendar_body = service.build_feed(include_grid=include_grid)
    return Response(
        content=calendar_body,
        media_type="text/calendar; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )
