"""Pure stdlib iCalendar (.ics) VEVENT parser for Chronos email attachments."""

import base64
import binascii
from collections.abc import Sequence
from datetime import date, datetime, timedelta
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.domain.schedule_engine import MINUTES_PER_DAY
from app.schemas.integrations import EmailAttachmentPayload
from app.services.email_ingestion._types import ExtractedScheduleEvent

_WINDOWS_TZ_ALIASES: dict[str, str] = {
    "NEW ZEALAND STANDARD TIME": "Pacific/Auckland",
    "AUS EASTERN STANDARD TIME": "Australia/Sydney",
    "E. AUSTRALIA STANDARD TIME": "Australia/Brisbane",
    "SINGAPORE STANDARD TIME": "Asia/Singapore",
    "UTC": "UTC",
    "GREENWICH STANDARD TIME": "UTC",
    "GMT STANDARD TIME": "Europe/London",
    "EASTERN STANDARD TIME": "America/New_York",
    "PACIFIC STANDARD TIME": "America/Los_Angeles",
}
_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


def _unfold_ics_lines(raw_text: str) -> list[str]:
    unfolded = re.sub(r"\r?\n[ \t]", "", raw_text)
    return [line for line in unfolded.splitlines() if line.strip()]


def _unescape_ics_text(value: str) -> str:
    return (
        value.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
        .strip()
    )


def _parse_ics_line(line: str) -> tuple[str, dict[str, str], str] | None:
    if ":" not in line:
        return None
    head, raw_value = line.split(":", 1)
    parts = head.split(";")
    prop_name = parts[0].strip().upper()
    params: dict[str, str] = {}
    for param in parts[1:]:
        if "=" in param:
            key, val = param.split("=", 1)
            params[key.strip().upper()] = val.strip().strip('"')
    return prop_name, params, raw_value.strip()


def _resolve_tzinfo(tzid: str | None, default_tz: ZoneInfo) -> ZoneInfo:
    if not tzid:
        return default_tz
    cleaned = tzid.strip()
    mapped = _WINDOWS_TZ_ALIASES.get(cleaned.upper(), cleaned)
    try:
        return ZoneInfo(mapped)
    except ZoneInfoNotFoundError:
        return default_tz


def _parse_ics_datetime(
    raw_value: str, params: dict[str, str], default_tz: ZoneInfo
) -> tuple[date | None, datetime | None]:
    value = raw_value.strip()
    is_date_only = params.get("VALUE", "").upper() == "DATE" or (
        len(value) == 8 and value.isdigit()
    )
    if is_date_only:
        try:
            return datetime.strptime(value[:8], "%Y%m%d").date(), None
        except ValueError:
            return None, None

    is_utc = value.endswith("Z")
    stamp = value[:-1] if is_utc else value
    fmt = "%Y%m%dT%H%M%S" if len(stamp) == 15 else "%Y%m%dT%H%M"
    try:
        naive = datetime.strptime(stamp, fmt)
    except ValueError:
        return None, None
    tz = ZoneInfo("UTC") if is_utc else _resolve_tzinfo(params.get("TZID"), default_tz)
    return None, naive.replace(tzinfo=tz).astimezone(default_tz)


def _parse_duration(raw_value: str) -> timedelta | None:
    match = _DURATION_RE.match(raw_value.strip().upper())
    if not match:
        return None
    return timedelta(
        days=int(match.group("days") or 0),
        hours=int(match.group("hours") or 0),
        minutes=int(match.group("minutes") or 0),
        seconds=int(match.group("seconds") or 0),
    )


def _extract_first_vevent_props(raw_text: str) -> dict[str, tuple[dict[str, str], str]]:
    stack: list[str] = []
    props: dict[str, tuple[dict[str, str], str]] = {}
    in_vevent = False
    for line in _unfold_ics_lines(raw_text):
        parsed = _parse_ics_line(line)
        if parsed is None:
            continue
        name, params, value = parsed
        if name == "BEGIN":
            comp = value.upper()
            stack.append(comp)
            if comp == "VEVENT" and not in_vevent:
                in_vevent = True
            continue
        if name == "END":
            comp = value.upper()
            if stack and stack[-1] == comp:
                stack.pop()
            if in_vevent and comp == "VEVENT":
                break
            continue
        if in_vevent and stack and stack[-1] == "VEVENT" and name not in props:
            props[name] = (params, value)
    return props


def _build_notes(props: dict[str, tuple[dict[str, str], str]]) -> str | None:
    parts: list[str] = []
    if "LOCATION" in props:
        location = _unescape_ics_text(props["LOCATION"][1])
        if location:
            parts.append(f"Location: {location}")
    if "DESCRIPTION" in props:
        description = _unescape_ics_text(props["DESCRIPTION"][1])
        if description:
            parts.append(description[:600])
    return "\n\n".join(parts) if parts else None


def _compute_timed_bounds(
    start_dt: datetime,
    end_dt: datetime | None,
    duration_raw: str | None,
) -> tuple[int, int]:
    start_minute = start_dt.hour * 60 + start_dt.minute
    resolved_end = end_dt
    if resolved_end is None and duration_raw:
        delta = _parse_duration(duration_raw)
        if delta is not None:
            resolved_end = start_dt + delta
    if resolved_end is None:
        end_minute = min(MINUTES_PER_DAY, start_minute + 60)
    elif resolved_end.date() > start_dt.date():
        end_minute = MINUTES_PER_DAY
    else:
        end_minute = resolved_end.hour * 60 + resolved_end.minute
    if end_minute <= start_minute:
        end_minute = min(MINUTES_PER_DAY, start_minute + 60)
    if start_minute >= MINUTES_PER_DAY:
        start_minute = MINUTES_PER_DAY - 15
        end_minute = MINUTES_PER_DAY
    return start_minute, end_minute


def parse_ics_event(
    raw_ics: str,
    *,
    timezone_name: str = "Pacific/Auckland",
    fallback_title: str | None = None,
) -> ExtractedScheduleEvent | None:
    """Parse the first VEVENT from iCalendar text into an ExtractedScheduleEvent."""
    props = _extract_first_vevent_props(raw_ics)
    if "DTSTART" not in props:
        return None
    default_tz = ZoneInfo(timezone_name)
    start_params, start_val = props["DTSTART"]
    all_day_date, start_dt = _parse_ics_datetime(start_val, start_params, default_tz)

    raw_summary = _unescape_ics_text(props["SUMMARY"][1]) if "SUMMARY" in props else ""
    title = raw_summary or (fallback_title.strip() if fallback_title else "") or "Calendar Event"
    notes = _build_notes(props)

    if all_day_date is not None:
        return ExtractedScheduleEvent(
            title=title[:200],
            target_date=all_day_date,
            all_day=True,
            notes=notes,
        )
    if start_dt is None:
        return None

    end_dt: datetime | None = None
    if "DTEND" in props:
        end_params, end_val = props["DTEND"]
        _, end_dt = _parse_ics_datetime(end_val, end_params, default_tz)
    duration_raw = props["DURATION"][1] if "DURATION" in props else None
    start_minute, end_minute = _compute_timed_bounds(start_dt, end_dt, duration_raw)
    return ExtractedScheduleEvent(
        title=title[:200],
        target_date=start_dt.date(),
        start_minute=start_minute,
        end_minute=end_minute,
        all_day=False,
        notes=notes,
    )


def _is_ics_attachment(attachment: EmailAttachmentPayload) -> bool:
    ctype = (attachment.content_type or "").split(";", 1)[0].strip().lower()
    fname = (attachment.filename or "").strip().lower()
    return ctype == "text/calendar" or fname.endswith(".ics")


def extract_event_from_ics_attachments(
    attachments: Sequence[EmailAttachmentPayload],
    *,
    timezone_name: str = "Pacific/Auckland",
    fallback_title: str | None = None,
) -> ExtractedScheduleEvent | None:
    """Return the first valid VEVENT parsed from any .ics attachment, or None."""
    for attachment in attachments:
        if not _is_ics_attachment(attachment) or not attachment.content_base64:
            continue
        try:
            decoded_bytes = base64.b64decode(attachment.content_base64, validate=False)
        except (binascii.Error, ValueError):
            continue
        raw_ics = decoded_bytes.decode("utf-8", errors="replace")
        event = parse_ics_event(
            raw_ics, timezone_name=timezone_name, fallback_title=fallback_title
        )
        if event is not None:
            return event
    return None
