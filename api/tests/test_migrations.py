"""Alembic migration roundtrip and schema verification tests."""

from pathlib import Path
import sqlite3

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.orm_models import (
    CommitmentORM,
    FrictionNoteORM,
    GridBlockCueORM,
    GridBlockORM,
    GridExceptionORM,
    HabitCueORM,
)


def test_alembic_upgrade_downgrade_roundtrip(tmp_path: Path, monkeypatch) -> None:
    db_file = tmp_path / "chronos_migrations.db"
    monkeypatch.setenv("CHRONOS_DATABASE_URL", f"sqlite:///{db_file}")

    api_root = Path(__file__).resolve().parent.parent
    cfg = AlembicConfig(str(api_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_file}")

    alembic_command.upgrade(cfg, "head")

    with sqlite3.connect(db_file) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        exception_indexes = {
            row[1]
            for row in conn.execute("PRAGMA index_list(grid_exceptions)").fetchall()
        }
        commitment_indexes = {
            row[1]
            for row in conn.execute("PRAGMA index_list(commitments)").fetchall()
        }

    assert {
        "grid_blocks",
        "grid_exceptions",
        "commitments",
        "habit_cues",
        "grid_block_cues",
        "friction_notes",
    }.issubset(tables)
    assert version == ("0002_habit_cues_and_friction",)
    assert "ix_grid_exceptions_grid_block_id" in exception_indexes
    assert "ix_grid_exceptions_target_date" in exception_indexes
    assert "ix_commitments_date_start" in commitment_indexes

    alembic_command.downgrade(cfg, "base")
    with sqlite3.connect(db_file) as conn:
        down_tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "grid_blocks" not in down_tables
    assert "grid_exceptions" not in down_tables
    assert "commitments" not in down_tables
    assert "habit_cues" not in down_tables
    assert "grid_block_cues" not in down_tables
    assert "friction_notes" not in down_tables

    alembic_command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_file}")
    try:
        with Session(engine) as session:
            for model in (
                GridBlockORM,
                GridExceptionORM,
                CommitmentORM,
                HabitCueORM,
                GridBlockCueORM,
                FrictionNoteORM,
            ):
                session.scalars(select(model).limit(1)).all()
    finally:
        engine.dispose()


def test_all_alembic_revision_ids_fit_postgres_varchar32() -> None:
    api_root = Path(__file__).resolve().parent.parent
    cfg = AlembicConfig(str(api_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_root / "alembic"))
    scripts = ScriptDirectory.from_config(cfg)
    too_long = [
        f"{rev.revision} ({len(rev.revision)} chars)"
        for rev in scripts.walk_revisions()
        if len(rev.revision) > 32
    ]
    assert too_long == []
