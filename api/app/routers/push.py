"""REST endpoints for Web Push VAPID config and browser subscription lifecycle."""

from fastapi import APIRouter, Depends

from app.routers.deps import get_push_service
from app.schemas.push import (
    PushSubscribeRequest,
    PushSubscribeResponse,
    PushUnsubscribeRequest,
    PushUnsubscribeResponse,
    VapidPublicKeyResponse,
)
from app.services.push_service import PushService

router = APIRouter(prefix="/push", tags=["Push"])


@router.get("/vapid-public-key", response_model=VapidPublicKeyResponse)
def get_vapid_public_key(
    service: PushService = Depends(get_push_service),
) -> VapidPublicKeyResponse:
    """Return the public VAPID key and whether Web Push dispatch is enabled."""
    public_key, enabled = service.get_vapid_public_key()
    return VapidPublicKeyResponse(public_key=public_key, enabled=enabled)


@router.post("/subscribe", response_model=PushSubscribeResponse)
def subscribe_push(
    payload: PushSubscribeRequest,
    service: PushService = Depends(get_push_service),
) -> PushSubscribeResponse:
    """Idempotently register or refresh a browser Web Push subscription."""
    sub = service.subscribe(
        endpoint=payload.endpoint,
        p256dh=payload.keys.p256dh,
        auth=payload.keys.auth,
        user_agent=payload.user_agent,
    )
    return PushSubscribeResponse(id=sub.id, endpoint=sub.endpoint, active=True)


@router.post("/unsubscribe", response_model=PushUnsubscribeResponse)
def unsubscribe_push(
    payload: PushUnsubscribeRequest,
    service: PushService = Depends(get_push_service),
) -> PushUnsubscribeResponse:
    """Remove a registered browser Web Push subscription by endpoint."""
    removed = service.unsubscribe(endpoint=payload.endpoint)
    return PushUnsubscribeResponse(endpoint=payload.endpoint.strip(), removed=removed)
