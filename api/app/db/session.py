"""SQLAlchemy engine and session management for Chronos."""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """Declarative base for Chronos SQLAlchemy ORM models."""


def get_db() -> Generator[Session, None, None]:
    """Yield an active SQLAlchemy session and close it on exit."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
