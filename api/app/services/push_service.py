"""Application service for Web Push subscription management and nudge dispatch."""

from datetime import datetime
import logging
from zoneinfo import ZoneInfo

from app.domain.errors import ScheduleValidationError
from app.domain.models import DueNudge, PushSubscription
from app.domain.push_scheduler import evaluate_due_nudges
from app.domain.schedule_engine import resolve_day_schedule
from app.repositories.protocols import PushRepository, ScheduleRepository
from app.services.push_gateway import PushGateway

logger = logging.getLogger(__name__)


def _require_non_empty(value: str, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ScheduleValidationError(f"{field_name} must not be empty.")
    return cleaned


class PushService:
    """Use-case coordinator for Web Push subscriptions and scheduled transition nudges."""

    def __init__(
        self,
        *,
        schedule_repo: ScheduleRepository,
        push_repo: PushRepository,
        gateway: PushGateway | None,
        vapid_public_key: str,
        timezone_name: str = "Pacific/Auckland",
    ) -> None:
        self._schedule_repo = schedule_repo
        self._push_repo = push_repo
        self._gateway = gateway
        self._vapid_public_key = vapid_public_key.strip()
        self._tz = ZoneInfo(timezone_name)

    def get_vapid_public_key(self) -> tuple[str, bool]:
        """Return (public_key, enabled) for client PushManager subscription."""
        enabled = self._gateway is not None and bool(self._vapid_public_key)
        return (self._vapid_public_key if enabled else ""), enabled

    def subscribe(
        self,
        *,
        endpoint: str,
        p256dh: str,
        auth: str,
        user_agent: str | None = None,
    ) -> PushSubscription:
        clean_endpoint = _require_non_empty(endpoint, "endpoint")
        if not (clean_endpoint.startswith("https://") or clean_endpoint.startswith("http://")):
            raise ScheduleValidationError("endpoint must be a valid HTTP(S) URL.")
        clean_p256dh = _require_non_empty(p256dh, "keys.p256dh")
        clean_auth = _require_non_empty(auth, "keys.auth")
        clean_ua = user_agent.strip()[:255] if user_agent and user_agent.strip() else None
        return self._push_repo.upsert_subscription(
            endpoint=clean_endpoint,
            p256dh=clean_p256dh,
            auth=clean_auth,
            user_agent=clean_ua,
        )

    def unsubscribe(self, *, endpoint: str) -> bool:
        clean_endpoint = _require_non_empty(endpoint, "endpoint")
        return self._push_repo.delete_subscription_by_endpoint(clean_endpoint)

    def run_scheduled_tick(self, *, now_dt: datetime | None = None) -> int:
        """Evaluate due transition nudges and dispatch once per dedupe_key."""
        if self._gateway is None:
            raise RuntimeError("PushGateway is not configured for scheduled push dispatch.")

        current_dt = now_dt.astimezone(self._tz) if now_dt is not None else datetime.now(self._tz)
        target_date = current_dt.date()
        now_minute = current_dt.hour * 60 + current_dt.minute

        grid_blocks = self._schedule_repo.list_grid_blocks(
            day_of_week=target_date.weekday(), active_only=True
        )
        exceptions = self._schedule_repo.list_exceptions_for_date(target_date)
        commitments = self._schedule_repo.list_commitments(
            start_date=target_date, end_date=target_date
        )
        travel_overlays = self._schedule_repo.list_travel_overlays(
            start_date=target_date, end_date=target_date
        )
        schedule = resolve_day_schedule(
            target_date,
            grid_blocks,
            exceptions,
            commitments,
            travel_overlays=travel_overlays,
        )
        already_sent = self._push_repo.list_sent_dedupe_keys(target_date)
        due_nudges = evaluate_due_nudges(schedule, now_minute, already_sent)
        if not due_nudges:
            return 0

        subscriptions = self._push_repo.list_subscriptions()
        if not subscriptions:
            return 0

        delivered_count = 0
        for nudge in due_nudges:
            if self._dispatch_nudge(self._gateway, nudge, subscriptions):
                self._push_repo.record_delivery(
                    dedupe_key=nudge.dedupe_key,
                    target_date=nudge.target_date,
                    source_kind=nudge.source_kind,
                    source_id=nudge.source_id,
                    stage=nudge.stage,
                )
                delivered_count += 1
        return delivered_count

    def _dispatch_nudge(
        self,
        gateway: PushGateway,
        nudge: DueNudge,
        subscriptions: list[PushSubscription],
    ) -> bool:
        payload = nudge.to_payload()
        any_delivered = False
        for sub in subscriptions:
            result = gateway.send(sub, payload)
            if result.stale:
                logger.warning("Pruning stale push subscription %s (status=%s)", sub.id, result.status_code)
                self._push_repo.delete_subscription_by_endpoint(sub.endpoint)
                continue
            if result.ok:
                any_delivered = True
                continue
            logger.warning(
                "Web Push delivery failed for subscription %s (status=%s): %s",
                sub.id,
                result.status_code,
                result.error,
            )
        return any_delivered
