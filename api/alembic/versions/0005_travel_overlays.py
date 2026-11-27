"""Add travel_overlays table for date-bounded travel mode overlays.

Revision ID: 0005_travel_overlays
Revises: 0004_tentative_commitments
Create Date: 2026-11-27 04:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0005_travel_overlays"
down_revision: Union[str, None] = "0004_tentative_commitments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "travel_overlays",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("external_ref", sa.String(length=255), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("mode", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("end_date >= start_date", name="ck_travel_overlays_date_range"),
    )
    op.create_index(
        "uq_travel_overlays_external_ref",
        "travel_overlays",
        ["external_ref"],
        unique=True,
    )
    op.create_index(
        "ix_travel_overlays_dates",
        "travel_overlays",
        ["start_date", "end_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_travel_overlays_dates", table_name="travel_overlays")
    op.drop_index("uq_travel_overlays_external_ref", table_name="travel_overlays")
    op.drop_table("travel_overlays")
