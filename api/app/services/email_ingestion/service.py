"""Application service orchestrating idempotent Iris email ingestion."""

import hashlib

from sqlalchemy.exc import IntegrityError

from app.domain.errors import ScheduleValidationError
from app.domain.models import CommitmentSource, NudgeMode
from app.domain.schedule_engine import MINUTES_PER_DAY
from app.repositories.protocols import ScheduleRepository
from app.schemas.integrations import InboundEmailPayload, ScheduleEventPayload
from app.services.email_ingestion._types import (
    EmailIngestionResult,
    ExtractedScheduleEvent,
    UnschedulableEmailError,
)
from app.services.email_ingestion.extractor import ScheduleExtractorPort
from app.services.email_ingestion.ics_parser import extract_event_from_ics_attachments

_MAX_EXTERNAL_REF_LEN = 255


def _build_external_ref(message_id: str) -> str:
    cleaned = message_id.strip()
    candidate = f"iris:{cleaned}"
    if len(candidate) <= _MAX_EXTERNAL_REF_LEN:
        return candidate
    digest = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()
    return f"iris:sha256:{digest}"


def _event_from_structured(payload: ScheduleEventPayload) -> ExtractedScheduleEvent:
    return ExtractedScheduleEvent(
        title=payload.title.strip(),
        target_date=payload.target_date,
        start_minute=payload.start_minute,
        end_minute=payload.end_minute,
        all_day=payload.all_day,
        notes=payload.notes,
    )


def _validate_extracted_event(event: ExtractedScheduleEvent) -> ExtractedScheduleEvent:
    clean_title = event.title.strip()
    if not clean_title:
        raise UnschedulableEmailError("Extracted schedule event has an empty title.")
    if event.all_day:
        if event.start_minute is not None or event.end_minute is not None:
            raise ScheduleValidationError(
                "all_day schedule_event must not specify start_minute or end_minute."
            )
        return ExtractedScheduleEvent(
            title=clean_title,
            target_date=event.target_date,
            all_day=True,
            notes=event.notes,
        )
    if event.start_minute is None or event.end_minute is None:
        raise ScheduleValidationError(
            "Timed schedule_event requires both start_minute and end_minute."
        )
    if (
        event.start_minute < 0
        or event.end_minute > MINUTES_PER_DAY
        or event.start_minute >= event.end_minute
    ):
        raise ScheduleValidationError(
            f"Invalid time window [{event.start_minute}, {event.end_minute})."
        )
    return ExtractedScheduleEvent(
        title=clean_title,
        target_date=event.target_date,
        start_minute=event.start_minute,
        end_minute=event.end_minute,
        all_day=False,
        notes=event.notes,
    )


class EmailIngestionService:
    """Ingests Iris inbound emails into idempotent tentative commitments."""

    def __init__(
        self,
        repo: ScheduleRepository,
        extractor: ScheduleExtractorPort,
        *,
        timezone_name: str = "Pacific/Auckland",
    ) -> None:
        self._repo = repo
        self._extractor = extractor
        self._timezone_name = timezone_name

    def _resolve_event(self, payload: InboundEmailPayload) -> ExtractedScheduleEvent | None:
        if payload.schedule_event is not None:
            return _event_from_structured(payload.schedule_event)
        ics_event = extract_event_from_ics_attachments(
            payload.attachments,
            timezone_name=self._timezone_name,
            fallback_title=payload.subject,
        )
        if ics_event is not None:
            return ics_event
        return self._extractor.extract(payload)

    def ingest_email(self, payload: InboundEmailPayload) -> EmailIngestionResult:
        """Ingest an inbound Iris email into a tentative commitment idempotently."""
        external_ref = _build_external_ref(payload.message_id)
        existing = self._repo.get_commitment_by_source_external_ref(
            CommitmentSource.IRIS, external_ref
        )
        if existing is not None:
            return EmailIngestionResult(
                status="duplicate",
                consumer_ref=f"commitment:{existing.id}",
                commitment=existing,
            )

        extracted = self._resolve_event(payload)
        if extracted is None:
            raise UnschedulableEmailError("Email contains no schedulable date or event.")
        validated = _validate_extracted_event(extracted)
        nudge_mode = NudgeMode.NONE if validated.all_day else NudgeMode.TWO_STAGE

        try:
            created = self._repo.create_commitment(
                title=validated.title,
                target_date=validated.target_date,
                start_minute=validated.start_minute,
                end_minute=validated.end_minute,
                all_day=validated.all_day,
                source=CommitmentSource.IRIS,
                external_ref=external_ref,
                nudge_mode=nudge_mode,
                urgent_quiet_bypass=False,
                tentative=True,
                notes=validated.notes,
            )
        except IntegrityError:
            concurrent = self._repo.get_commitment_by_source_external_ref(
                CommitmentSource.IRIS, external_ref
            )
            if concurrent is None:
                raise
            return EmailIngestionResult(
                status="duplicate",
                consumer_ref=f"commitment:{concurrent.id}",
                commitment=concurrent,
            )

        return EmailIngestionResult(
            status="created",
            consumer_ref=f"commitment:{created.id}",
            commitment=created,
        )
