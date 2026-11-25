"""Create initial Chronos schedule tables.

Revision ID: 0001_initial_schedule_tables
Revises:
Create Date: 2026-11-25 04:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0001_initial_schedule_tables"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "grid_blocks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("day_of_week", sa.Integer(), nullable=False),
        sa.Column("start_minute", sa.Integer(), nullable=False),
        sa.Column("end_minute", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("nudge_mode", sa.String(length=20), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_grid_blocks_day_of_week", "grid_blocks", ["day_of_week"], unique=False)

    op.create_table(
        "grid_exceptions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("grid_block_id", sa.Integer(), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("skipped", sa.Boolean(), nullable=False),
        sa.Column("start_minute", sa.Integer(), nullable=True),
        sa.Column("end_minute", sa.Integer(), nullable=True),
        sa.Column("extension_count", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["grid_block_id"], ["grid_blocks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("grid_block_id", "target_date", name="uq_grid_exceptions_block_date"),
    )
    op.create_index("ix_grid_exceptions_grid_block_id", "grid_exceptions", ["grid_block_id"], unique=False)
    op.create_index("ix_grid_exceptions_target_date", "grid_exceptions", ["target_date"], unique=False)

    op.create_table(
        "commitments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("start_minute", sa.Integer(), nullable=True),
        sa.Column("end_minute", sa.Integer(), nullable=True),
        sa.Column("all_day", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("external_ref", sa.String(length=255), nullable=True),
        sa.Column("nudge_mode", sa.String(length=20), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_commitments_target_date", "commitments", ["target_date"], unique=False)
    op.create_index("ix_commitments_external_ref", "commitments", ["external_ref"], unique=False)
    op.create_index("ix_commitments_date_start", "commitments", ["target_date", "start_minute"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_commitments_date_start", table_name="commitments")
    op.drop_index("ix_commitments_external_ref", table_name="commitments")
    op.drop_index("ix_commitments_target_date", table_name="commitments")
    op.drop_table("commitments")

    op.drop_index("ix_grid_exceptions_target_date", table_name="grid_exceptions")
    op.drop_index("ix_grid_exceptions_grid_block_id", table_name="grid_exceptions")
    op.drop_table("grid_exceptions")

    op.drop_index("ix_grid_blocks_day_of_week", table_name="grid_blocks")
    op.drop_table("grid_blocks")
