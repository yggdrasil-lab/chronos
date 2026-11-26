"""Frozen DTOs and typed errors for Chronos email ingestion."""

from dataclasses import dataclass
from datetime import date
from typing import Literal

from app.domain.errors import ChronosDomainError
from app.domain.models import Commitment


class UnschedulableEmailError(ChronosDomainError):
    """Raised when an inbound email contains no schedulable date or event."""


class ScheduleExtractionUpstreamError(ChronosDomainError):
    """Raised when the upstream Muninn LLM extraction boundary fails."""


@dataclass(frozen=True)
class ExtractedScheduleEvent:
    """Normalized schedule event extracted from payload, .ics, or Muninn."""

    title: str
    target_date: date
    start_minute: int | None = None
    end_minute: int | None = None
    all_day: bool = False
    notes: str | None = None


@dataclass(frozen=True)
class EmailIngestionResult:
    """Outcome of processing an inbound Iris email into a tentative commitment."""

    status: Literal["created", "duplicate"]
    consumer_ref: str
    commitment: Commitment
