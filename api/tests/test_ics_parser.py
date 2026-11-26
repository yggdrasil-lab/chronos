"""Pure unit tests for Chronos stdlib iCalendar (.ics) attachment parser."""

import base64
from datetime import date

from app.schemas.integrations import EmailAttachmentPayload
from app.services.email_ingestion.ics_parser import (
    extract_event_from_ics_attachments,
    parse_ics_event,
)


def test_parse_ics_event_unfolded_utc_and_valarm_ignored() -> None:
    raw_ics = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "BEGIN:VEVENT\r\n"
        "SUMMARY:Dentist Checkup with Dr.\r\n"
        "  Alex Tan\r\n"
        "DTSTART:20261126T010000Z\r\n"
        "DTEND:20261126T023000Z\r\n"
        "LOCATION:123 Harbour Rd\\, Wellington\r\n"
        "DESCRIPTION:Annual checkup\\nBring referral letter.\r\n"
        "BEGIN:VALARM\r\n"
        "ACTION:DISPLAY\r\n"
        "SUMMARY:Alarm should not overwrite VEVENT summary\r\n"
        "DESCRIPTION:Alarm description ignored\r\n"
        "TRIGGER:-PT15M\r\n"
        "END:VALARM\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )

    event = parse_ics_event(raw_ics, timezone_name="Pacific/Auckland")
    assert event is not None
    assert event.title == "Dentist Checkup with Dr. Alex Tan"
    # 01:00 UTC in Nov (NZDT +13:00) -> 14:00 (840m) to 15:30 (930m) on 2026-11-26
    assert event.target_date == date(2026, 11, 26)
    assert event.start_minute == 14 * 60
    assert event.end_minute == 15 * 60 + 30
    assert event.all_day is False
    assert event.notes == "Location: 123 Harbour Rd, Wellington\n\nAnnual checkup\nBring referral letter."


def test_parse_ics_event_tzid_windows_alias_and_duration() -> None:
    raw_ics = (
        "BEGIN:VCALENDAR\n"
        "BEGIN:VEVENT\n"
        "SUMMARY:Flight NZ502 to Wellington\n"
        "DTSTART;TZID=\"New Zealand Standard Time\":20261127T083000\n"
        "DURATION:PT1H15M\n"
        "END:VEVENT\n"
        "END:VCALENDAR\n"
    )
    event = parse_ics_event(raw_ics, timezone_name="Pacific/Auckland")
    assert event is not None
    assert event.title == "Flight NZ502 to Wellington"
    assert event.target_date == date(2026, 11, 27)
    assert event.start_minute == 8 * 60 + 30
    assert event.end_minute == 9 * 60 + 45
    assert event.all_day is False


def test_parse_ics_event_all_day_value_date() -> None:
    raw_ics = (
        "BEGIN:VCALENDAR\n"
        "BEGIN:VEVENT\n"
        "SUMMARY:Conference Day\n"
        "DTSTART;VALUE=DATE:20261204\n"
        "DTEND;VALUE=DATE:20261205\n"
        "END:VEVENT\n"
        "END:VCALENDAR\n"
    )
    event = parse_ics_event(raw_ics, timezone_name="Pacific/Auckland")
    assert event is not None
    assert event.title == "Conference Day"
    assert event.target_date == date(2026, 12, 4)
    assert event.all_day is True
    assert event.start_minute is None
    assert event.end_minute is None


def test_extract_event_from_ics_attachments_skips_non_ics_and_invalid_b64() -> None:
    ics_bytes = (
        "BEGIN:VCALENDAR\r\n"
        "BEGIN:VEVENT\r\n"
        "DTSTART;TZID=Pacific/Auckland:20261128T100000\r\n"
        "DTEND;TZID=Pacific/Auckland:20261128T110000\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    ).encode("utf-8")
    attachments = [
        EmailAttachmentPayload(
            filename="receipt.pdf",
            content_type="application/pdf",
            size_bytes=10,
            content_base64=base64.b64encode(b"%PDF-1.4").decode("ascii"),
        ),
        EmailAttachmentPayload(
            filename="broken.ics",
            content_type="text/calendar",
            size_bytes=4,
            content_base64="!!!not-base64!!!",
        ),
        EmailAttachmentPayload(
            filename="invite.ics",
            content_type="application/octet-stream",
            size_bytes=len(ics_bytes),
            content_base64=base64.b64encode(ics_bytes).decode("ascii"),
        ),
    ]
    event = extract_event_from_ics_attachments(
        attachments,
        timezone_name="Pacific/Auckland",
        fallback_title="Team Planning Session",
    )
    assert event is not None
    assert event.title == "Team Planning Session"
    assert event.target_date == date(2026, 11, 28)
    assert event.start_minute == 600
    assert event.end_minute == 660
