"""Pydantic v2 request and response schemas for Chronos Web Push endpoints."""

from pydantic import BaseModel


class VapidPublicKeyResponse(BaseModel):
    """Public VAPID configuration exposed to Kairos clients."""

    public_key: str
    enabled: bool


class PushKeysInput(BaseModel):
    """Browser PushSubscription key material."""

    p256dh: str
    auth: str


class PushSubscribeRequest(BaseModel):
    """Request body for registering or refreshing a Web Push subscription."""

    endpoint: str
    keys: PushKeysInput
    user_agent: str | None = None


class PushSubscribeResponse(BaseModel):
    """Registered Web Push subscription acknowledgement."""

    id: str
    endpoint: str
    active: bool = True


class PushUnsubscribeRequest(BaseModel):
    """Request body for removing a Web Push subscription."""

    endpoint: str


class PushUnsubscribeResponse(BaseModel):
    """Web Push unsubscription acknowledgement."""

    endpoint: str
    removed: bool
