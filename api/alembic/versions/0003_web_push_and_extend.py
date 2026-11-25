"""Add flexible/urgent_quiet_bypass flags and web push tables.

Revision ID: 0003_web_push_and_extend
Revises: 0002_habit_cues_and_friction
Create Date: 2026-11-25 22:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0003_web_push_and_extend"
down_revision: Union[str, None] = "0002_habit_cues_and_friction"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "grid_blocks",
        sa.Column("flexible", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "commitments",
        sa.Column("urgent_quiet_bypass", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.Text(), nullable=False),
        sa.Column("auth", sa.Text(), nullable=False),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("endpoint", name="uq_push_subscriptions_endpoint"),
    )

    op.create_table(
        "push_deliveries",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="uq_push_deliveries_dedupe_key"),
    )
    op.create_index("ix_push_deliveries_target_date", "push_deliveries", ["target_date"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_push_deliveries_target_date", table_name="push_deliveries")
    op.drop_table("push_deliveries")
    op.drop_table("push_subscriptions")
    op.drop_column("commitments", "urgent_quiet_bypass")
    op.drop_column("grid_blocks", "flexible")
