"""Health check probe endpoint for Chronos API."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import get_db

router = APIRouter(tags=["Health"])


@router.get("/health")
def health_check(db: Session = Depends(get_db)) -> dict[str, str]:
    """Liveness and readiness health probe validating database connectivity."""
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "service": "chronos-api"}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database connectivity check failed: {exc}",
        ) from exc
