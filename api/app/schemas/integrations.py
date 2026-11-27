"""Pydantic v2 schemas for internal machine-to-machine integration endpoints."""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.schedule import (
    CommitmentResponse,
    CompletedKratosSessionResponse,
    FastingSuggestionResponse,
)


class EmailAttachmentPayload(BaseModel):
    """Email attachment metadata and optional base64 content forwarded by Iris."""

    filename: str | None = Field(default=None, max_length=255)
    content_type: str | None = Field(default=None, max_length=128)
    size_bytes: int | None = Field(default=None, ge=0)
    content_base64: str | None = None


class ScheduleEventPayload(BaseModel):
    """Optional pre-structured schedule event supplied by Iris or test callers."""

    title: str = Field(..., min_length=1, max_length=200)
    target_date: date
    start_minute: int | None = None
    end_minute: int | None = None
    all_day: bool = False
    notes: str | None = None


class InboundEmailPayload(BaseModel):
    """Inbound email payload dispatched from Iris to Chronos."""

    email_id: int | None = None
    message_id: str = Field(..., min_length=1, max_length=512)
    route_target: str | None = Field(default="chronos_commitment", max_length=64)
    from_address: str | None = Field(default=None, max_length=255)
    to_address: str | None = Field(default=None, max_length=255)
    subject: str = Field(default="", max_length=500)
    received_at: datetime | None = None
    text_body: str | None = None
    html_excerpt: str | None = None
    raw_excerpt: str | None = None
    jev_verdict: Any = None
    attachments: list[EmailAttachmentPayload] = Field(default_factory=list)
    schedule_event: ScheduleEventPayload | None = None

    @field_validator("message_id", mode="before")
    @classmethod
    def _validate_message_id(cls, value: Any) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            raise ValueError("message_id is required.")
        return cleaned


class EmailIngestionResponse(BaseModel):
    """Response returned to Iris after ingesting an email commitment."""

    status: Literal["created", "duplicate"]
    consumer_ref: str
    commitment: CommitmentResponse


class DailySummaryCommitment(BaseModel):
    """Commitment projection inside the internal daily summary payload."""

    id: int
    title: str
    target_date: date
    start_minute: int | None = None
    end_minute: int | None = None
    all_day: bool
    source: str
    tentative: bool
    has_conflict: bool
    notes: str | None = None


class DailySummaryGridException(BaseModel):
    """Grid exception projection inside the internal daily summary payload."""

    id: int
    grid_block_id: int
    target_date: date
    action: str
    new_start_minute: int | None = None
    new_end_minute: int | None = None
    extension_count: int
    notes: str | None = None


class DailySummaryWorkoutHydration(BaseModel):
    """Workout hydration summary inside the internal daily summary payload."""

    status: str
    planned_routine_name: str | None = None
    planned_day_of_week: str | None = None
    completed_sessions: list[CompletedKratosSessionResponse] = Field(default_factory=list)
    attached_grid_block_id: int | None = None
    error: str | None = None


class DailySummaryFrictionNote(BaseModel):
    """Yesterday's friction note item inside the internal daily summary payload."""

    id: int
    target_date: date
    tag: str
    note: str
    habit_cue_id: int | None = None
    grid_block_id: int | None = None


class DailySummaryPassiveSignal(BaseModel):
    """Yesterday's evaluated passive signal linked to an active HabitCue."""

    cue_id: int
    cue_title: str
    rule_key: str
    observed: bool
    fact_label: str | None = None
    peer_status: str


class DailySummaryResponse(BaseModel):
    """Internal daily summary consumed by Odin morning copilot over aether-net."""

    date: date
    yesterday: date
    commitments: list[DailySummaryCommitment]
    tentative_count: int
    conflict_count: int
    grid_exceptions: list[DailySummaryGridException]
    workout_hydration: DailySummaryWorkoutHydration | None = None
    fasting_suggestion: FastingSuggestionResponse | None = None
    yesterday_friction_notes: list[DailySummaryFrictionNote]
    yesterday_passive_signals: list[DailySummaryPassiveSignal]
    degraded_peers: list[str]
