"""Add tentative flag and source/external_ref unique index on commitments.

Revision ID: 0004_tentative_commitments
Revises: 0003_web_push_and_extend
Create Date: 2026-11-26 15:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0004_tentative_commitments"
down_revision: Union[str, None] = "0003_web_push_and_extend"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "commitments",
        sa.Column("tentative", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "uq_commitments_source_external_ref",
        "commitments",
        ["source", "external_ref"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_commitments_source_external_ref", table_name="commitments")
    op.drop_column("commitments", "tentative")
