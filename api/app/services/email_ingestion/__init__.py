"""Email ingestion package converting Iris dispatches into tentative commitments."""

from app.services.email_ingestion._types import (
    EmailIngestionResult,
    ExtractedScheduleEvent,
    ScheduleExtractionUpstreamError,
    UnschedulableEmailError,
)
from app.services.email_ingestion.extractor import (
    MuninnScheduleExtractorAdapter,
    NullScheduleExtractor,
    ScheduleExtractorPort,
)
from app.services.email_ingestion.ics_parser import (
    extract_event_from_ics_attachments,
    parse_ics_event,
)
from app.services.email_ingestion.service import EmailIngestionService

__all__ = [
    "EmailIngestionResult",
    "EmailIngestionService",
    "ExtractedScheduleEvent",
    "MuninnScheduleExtractorAdapter",
    "NullScheduleExtractor",
    "ScheduleExtractionUpstreamError",
    "ScheduleExtractorPort",
    "UnschedulableEmailError",
    "extract_event_from_ics_attachments",
    "parse_ics_event",
]
