"""Pure RFC 5545 iCalendar (.ics) projection renderer for Chronos."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone as utc_tz
from zoneinfo import ZoneInfo

PRODID = "-//Yggdrasil//Chronos//EN"
MAX_OCTETS_PER_LINE = 75


@dataclass(frozen=True)
class IcsEvent:
    """Immutable calendar event ready for RFC 5545 serialization."""

    uid: str
    summary: str
    target_date: date
    start_minute: int | None = None
    end_minute: int | None = None
    all_day: bool = False
    description: str | None = None
    categories: tuple[str, ...] = ()
    status: str | None = "CONFIRMED"


def escape_ics_text(value: str) -> str:
    """Escape TEXT values per RFC 5545 section 3.3.11."""
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return (
        normalized.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def fold_ics_line(line: str, max_octets: int = MAX_OCTETS_PER_LINE) -> str:
    """Fold an iCalendar content line at <=75 UTF-8 octets using CRLF + single space."""
    if len(line.encode("utf-8")) <= max_octets:
        return line

    chunks: list[str] = []
    current_chars: list[str] = []
    current_octets = 0
    is_continuation = False

    for ch in line:
        ch_octets = len(ch.encode("utf-8"))
        if current_chars and current_octets + ch_octets > max_octets:
            chunks.append("".join(current_chars))
            current_chars = [" ", ch]
            current_octets = 1 + ch_octets
            is_continuation = True
            continue
        if not current_chars and is_continuation:
            current_chars = [" ", ch]
            current_octets = 1 + ch_octets
            continue
        current_chars.append(ch)
        current_octets += ch_octets

    if current_chars:
        chunks.append("".join(current_chars))
    return "\r\n".join(chunks)


def _local_minute_to_utc(target_date: date, minute: int, tz: ZoneInfo) -> datetime:
    if minute < 0 or minute > 1440:
        raise ValueError(f"Minute must be in [0, 1440], got {minute}.")
    if minute == 1440:
        local_dt = datetime.combine(target_date + timedelta(days=1), time(0, 0), tzinfo=tz)
    else:
        local_dt = datetime.combine(
            target_date, time(minute // 60, minute % 60), tzinfo=tz
        )
    return local_dt.astimezone(utc_tz.utc)


def _format_utc(dt: datetime) -> str:
    return dt.astimezone(utc_tz.utc).strftime("%Y%m%dT%H%M%SZ")


def _format_event_lines(event: IcsEvent, tz: ZoneInfo) -> list[str]:
    lines = ["BEGIN:VEVENT", f"UID:{escape_ics_text(event.uid)}"]
    if event.all_day:
        start_stamp = event.target_date.strftime("%Y%m%d")
        end_stamp = (event.target_date + timedelta(days=1)).strftime("%Y%m%d")
        dtstamp = _format_utc(datetime.combine(event.target_date, time(0, 0), tzinfo=utc_tz.utc))
        lines.append(f"DTSTAMP:{dtstamp}")
        lines.append(f"DTSTART;VALUE=DATE:{start_stamp}")
        lines.append(f"DTEND;VALUE=DATE:{end_stamp}")
    else:
        if event.start_minute is None or event.end_minute is None:
            raise ValueError(f"Timed IcsEvent {event.uid} requires start_minute and end_minute.")
        start_utc = _local_minute_to_utc(event.target_date, event.start_minute, tz)
        end_utc = _local_minute_to_utc(event.target_date, event.end_minute, tz)
        lines.append(f"DTSTAMP:{_format_utc(start_utc)}")
        lines.append(f"DTSTART:{_format_utc(start_utc)}")
        lines.append(f"DTEND:{_format_utc(end_utc)}")

    lines.append(f"SUMMARY:{escape_ics_text(event.summary)}")
    if event.description:
        lines.append(f"DESCRIPTION:{escape_ics_text(event.description)}")
    if event.categories:
        escaped_cats = ",".join(escape_ics_text(cat) for cat in event.categories if cat.strip())
        if escaped_cats:
            lines.append(f"CATEGORIES:{escaped_cats}")
    if event.status:
        lines.append(f"STATUS:{escape_ics_text(event.status)}")
    lines.append("END:VEVENT")
    return lines


def render_ics_calendar(
    events: Sequence[IcsEvent],
    calendar_name: str = "Chronos",
    timezone: str = "Pacific/Auckland",
) -> str:
    """Render an RFC 5545 VCALENDAR string with CRLF endings and 75-octet folding."""
    tz = ZoneInfo(timezone)
    raw_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape_ics_text(calendar_name)}",
        f"X-WR-TIMEZONE:{escape_ics_text(timezone)}",
    ]
    for event in events:
        raw_lines.extend(_format_event_lines(event, tz))
    raw_lines.append("END:VCALENDAR")

    folded = [fold_ics_line(line) for line in raw_lines]
    return "\r\n".join(folded) + "\r\n"
