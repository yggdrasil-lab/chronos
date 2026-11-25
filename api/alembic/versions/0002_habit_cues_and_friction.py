"""Add habit_cues, grid_block_cues, and friction_notes tables.

Revision ID: 0002_habit_cues_and_friction
Revises: 0001_initial_schedule_tables
Create Date: 2026-11-25 18:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0002_habit_cues_and_friction"
down_revision: Union[str, None] = "0001_initial_schedule_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "habit_cues",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("zone_chain", sa.String(length=120), nullable=False),
        sa.Column("chain_order", sa.Integer(), nullable=False),
        sa.Column("passive_rule", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_habit_cues_zone_chain", "habit_cues", ["zone_chain"], unique=False)
    op.create_index(
        "ix_habit_cues_zone_order",
        "habit_cues",
        ["zone_chain", "chain_order", "id"],
        unique=False,
    )

    op.create_table(
        "grid_block_cues",
        sa.Column("grid_block_id", sa.Integer(), nullable=False),
        sa.Column("habit_cue_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["grid_block_id"], ["grid_blocks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["habit_cue_id"], ["habit_cues.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("grid_block_id", "habit_cue_id"),
    )
    op.create_index("ix_grid_block_cues_habit_cue_id", "grid_block_cues", ["habit_cue_id"], unique=False)

    op.create_table(
        "friction_notes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("tag", sa.String(length=40), nullable=False),
        sa.Column("note", sa.String(length=280), nullable=False),
        sa.Column("habit_cue_id", sa.Integer(), nullable=True),
        sa.Column("grid_block_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["grid_block_id"], ["grid_blocks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["habit_cue_id"], ["habit_cues.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_friction_notes_target_date", "friction_notes", ["target_date"], unique=False)
    op.create_index("ix_friction_notes_habit_cue_id", "friction_notes", ["habit_cue_id"], unique=False)
    op.create_index("ix_friction_notes_grid_block_id", "friction_notes", ["grid_block_id"], unique=False)
    op.create_index(
        "ix_friction_notes_date_created",
        "friction_notes",
        ["target_date", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_friction_notes_date_created", table_name="friction_notes")
    op.drop_index("ix_friction_notes_grid_block_id", table_name="friction_notes")
    op.drop_index("ix_friction_notes_habit_cue_id", table_name="friction_notes")
    op.drop_index("ix_friction_notes_target_date", table_name="friction_notes")
    op.drop_table("friction_notes")

    op.drop_index("ix_grid_block_cues_habit_cue_id", table_name="grid_block_cues")
    op.drop_table("grid_block_cues")

    op.drop_index("ix_habit_cues_zone_order", table_name="habit_cues")
    op.drop_index("ix_habit_cues_zone_chain", table_name="habit_cues")
    op.drop_table("habit_cues")
