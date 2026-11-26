"""Pydantic v2 schemas for internal machine-to-machine integration endpoints."""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.schedule import CommitmentResponse


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
