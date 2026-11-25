"""SQLAlchemy repository for Web Push subscriptions and delivery deduplication."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.orm_models import PushDeliveryORM, PushSubscriptionORM
from app.domain.models import PushDelivery, PushSubscription


def _to_subscription(row: PushSubscriptionORM) -> PushSubscription:
    return PushSubscription(
        id=str(row.id),
        endpoint=row.endpoint,
        p256dh=row.p256dh,
        auth=row.auth,
        user_agent=row.user_agent,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_delivery(row: PushDeliveryORM) -> PushDelivery:
    return PushDelivery(
        id=str(row.id),
        dedupe_key=row.dedupe_key,
        target_date=row.target_date,
        source_kind=row.source_kind,
        source_id=row.source_id,
        stage=row.stage,
        sent_at=row.sent_at,
    )


class SqlAlchemyPushRepository:
    """SQLAlchemy-backed repository for Web Push subscriptions and deliveries."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def upsert_subscription(
        self,
        *,
        endpoint: str,
        p256dh: str,
        auth: str,
        user_agent: str | None,
    ) -> PushSubscription:
        stmt = select(PushSubscriptionORM).where(PushSubscriptionORM.endpoint == endpoint)
        row = self._db.scalar(stmt)
        if row is None:
            row = PushSubscriptionORM(
                endpoint=endpoint,
                p256dh=p256dh,
                auth=auth,
                user_agent=user_agent,
            )
            self._db.add(row)
        else:
            row.p256dh = p256dh
            row.auth = auth
            row.user_agent = user_agent
        self._db.commit()
        self._db.refresh(row)
        return _to_subscription(row)

    def delete_subscription_by_endpoint(self, endpoint: str) -> bool:
        stmt = select(PushSubscriptionORM).where(PushSubscriptionORM.endpoint == endpoint)
        row = self._db.scalar(stmt)
        if row is None:
            return False
        self._db.delete(row)
        self._db.commit()
        return True

    def list_subscriptions(self) -> list[PushSubscription]:
        stmt = select(PushSubscriptionORM).order_by(
            PushSubscriptionORM.created_at, PushSubscriptionORM.id
        )
        return [_to_subscription(row) for row in self._db.scalars(stmt).all()]

    def list_sent_dedupe_keys(self, target_date: date) -> set[str]:
        stmt = select(PushDeliveryORM.dedupe_key).where(
            PushDeliveryORM.target_date == target_date
        )
        return set(self._db.scalars(stmt).all())

    def record_delivery(
        self,
        *,
        dedupe_key: str,
        target_date: date,
        source_kind: str,
        source_id: str,
        stage: str,
    ) -> PushDelivery:
        existing = self._db.scalar(
            select(PushDeliveryORM).where(PushDeliveryORM.dedupe_key == dedupe_key)
        )
        if existing is not None:
            return _to_delivery(existing)
        row = PushDeliveryORM(
            dedupe_key=dedupe_key,
            target_date=target_date,
            source_kind=source_kind,
            source_id=source_id,
            stage=stage,
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return _to_delivery(row)
