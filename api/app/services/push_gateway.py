"""Web Push delivery gateway protocol and pywebpush adapter."""

from dataclasses import dataclass
import json
from typing import Protocol

from pywebpush import WebPushException, webpush

from app.domain.models import PushSubscription

DEFAULT_PUSH_TTL_SECONDS = 900


@dataclass(frozen=True)
class PushSendResult:
    """Outcome of a single Web Push delivery attempt."""

    ok: bool
    stale: bool = False
    status_code: int | None = None
    error: str | None = None


class PushGateway(Protocol):
    """Boundary protocol for delivering a Web Push notification payload."""

    def send(self, subscription: PushSubscription, payload: dict[str, str]) -> PushSendResult:
        """Send JSON payload to a browser push subscription endpoint."""


class PyWebPushGateway:
    """pywebpush-backed Web Push gateway with VAPID claims and 15m high-urgency TTL."""

    def __init__(self, *, vapid_private_key: str, vapid_subject: str) -> None:
        clean_key = vapid_private_key.strip()
        clean_subject = vapid_subject.strip()
        if not clean_key or not clean_subject:
            raise ValueError("PyWebPushGateway requires non-empty vapid_private_key and vapid_subject.")
        self._vapid_private_key = clean_key
        self._vapid_subject = clean_subject

    def send(self, subscription: PushSubscription, payload: dict[str, str]) -> PushSendResult:
        try:
            webpush(
                subscription_info={
                    "endpoint": subscription.endpoint,
                    "keys": {
                        "p256dh": subscription.p256dh,
                        "auth": subscription.auth,
                    },
                },
                data=json.dumps(payload),
                vapid_private_key=self._vapid_private_key,
                vapid_claims={"sub": self._vapid_subject},
                ttl=DEFAULT_PUSH_TTL_SECONDS,
                headers={"Urgency": "high"},
            )
            return PushSendResult(ok=True, status_code=201)
        except WebPushException as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code in (404, 410):
                return PushSendResult(
                    ok=False,
                    stale=True,
                    status_code=status_code,
                    error=str(exc),
                )
            return PushSendResult(
                ok=False,
                stale=False,
                status_code=status_code,
                error=str(exc),
            )
