"""LLM schedule extraction port and Muninn adapter for inbound emails."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
import json
from typing import Any, Protocol

import httpx

from app.domain.schedule_engine import MINUTES_PER_DAY
from app.schemas.integrations import InboundEmailPayload
from app.services.email_ingestion._types import (
    ExtractedScheduleEvent,
    ScheduleExtractionUpstreamError,
)

_SYSTEM_PROMPT = (
    "You extract a single calendar event or appointment from an email into strict JSON. "
    "Interpret relative or unzoned times in timezone {timezone}. "
    "Return ONLY a JSON object with keys: "
    '"schedulable" (boolean), "title" (string or null), "target_date" ("YYYY-MM-DD" or null), '
    '"start_minute" (integer 0-1439 or null), "end_minute" (integer 1-1440 or null), '
    '"all_day" (boolean), "notes" (string or null). '
    "If the email has no clear dated appointment, flight, reservation, or meeting, "
    'set "schedulable": false.'
)


class ScheduleExtractorPort(Protocol):
    """Port for extracting a schedulable event from unstructured email content."""

    def extract(self, payload: InboundEmailPayload) -> ExtractedScheduleEvent | None:
        """Return an ExtractedScheduleEvent, or None if the email is unschedulable."""


class NullScheduleExtractor:
    """No-op extractor returning None when LLM extraction is disabled."""

    def extract(self, payload: InboundEmailPayload) -> ExtractedScheduleEvent | None:
        del payload
        return None


@contextmanager
def _client_scope(
    api_base: str, timeout_seconds: float, client: httpx.Client | None
) -> Iterator[httpx.Client]:
    if client is not None:
        yield client
        return
    with httpx.Client(base_url=api_base, timeout=timeout_seconds) as managed:
        yield managed


def _build_user_prompt(payload: InboundEmailPayload) -> str:
    body = (payload.text_body or payload.raw_excerpt or payload.html_excerpt or "").strip()
    parts = [
        f"Subject: {payload.subject}",
        f"From: {payload.from_address or 'unknown'}",
        f"Received At: {payload.received_at.isoformat() if payload.received_at else 'unknown'}",
        f"Body:\n{body[:4000]}",
    ]
    if payload.jev_verdict is not None:
        parts.append(f"Classifier Hint: {json.dumps(payload.jev_verdict, default=str)[:500]}")
    return "\n".join(parts)


def _strip_code_fences(content: str) -> str:
    cleaned = content.strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.splitlines()
    if len(lines) >= 2 and lines[-1].strip().startswith("```"):
        return "\n".join(lines[1:-1]).strip()
    return cleaned


def _parse_extractor_json(
    raw_content: str, fallback_subject: str
) -> ExtractedScheduleEvent | None:
    try:
        parsed = json.loads(_strip_code_fences(raw_content))
    except json.JSONDecodeError as exc:
        raise ScheduleExtractionUpstreamError(f"Muninn returned invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ScheduleExtractionUpstreamError("Muninn returned a non-object JSON payload.")

    if parsed.get("schedulable") is False or not parsed.get("target_date"):
        return None
    try:
        target_date = date.fromisoformat(str(parsed["target_date"]).strip())
    except ValueError as exc:
        raise ScheduleExtractionUpstreamError("Muninn returned an invalid target_date.") from exc

    raw_title = str(parsed.get("title") or "").strip() or fallback_subject.strip()
    if not raw_title:
        return None
    all_day = bool(parsed.get("all_day", False))
    notes = str(parsed["notes"]).strip() if parsed.get("notes") else None
    if all_day:
        return ExtractedScheduleEvent(
            title=raw_title[:200],
            target_date=target_date,
            all_day=True,
            notes=notes,
        )

    start_minute = parsed.get("start_minute")
    end_minute = parsed.get("end_minute")
    if (
        isinstance(start_minute, bool)
        or isinstance(end_minute, bool)
        or not isinstance(start_minute, int)
        or not isinstance(end_minute, int)
        or start_minute < 0
        or end_minute > MINUTES_PER_DAY
        or start_minute >= end_minute
    ):
        raise ScheduleExtractionUpstreamError("Muninn returned invalid start_minute/end_minute.")
    return ExtractedScheduleEvent(
        title=raw_title[:200],
        target_date=target_date,
        start_minute=start_minute,
        end_minute=end_minute,
        all_day=False,
        notes=notes,
    )


class MuninnScheduleExtractorAdapter:
    """OpenAI-compatible Muninn LLM adapter for schedule extraction."""

    def __init__(
        self,
        *,
        api_base: str,
        api_key: str,
        model: str = "app-synthesize",
        timezone_name: str = "Pacific/Auckland",
        timeout_seconds: float = 60.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_base = api_base.strip().rstrip("/")
        self._api_key = api_key.strip()
        self._model = model.strip() or "app-synthesize"
        self._timezone_name = timezone_name
        self._timeout_seconds = timeout_seconds
        self._client = client

    def extract(self, payload: InboundEmailPayload) -> ExtractedScheduleEvent | None:
        if self._client is None and (not self._api_base or not self._api_key):
            raise ScheduleExtractionUpstreamError("Muninn schedule extractor is not configured.")
        request_body: dict[str, Any] = {
            "model": self._model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": _SYSTEM_PROMPT.format(timezone=self._timezone_name),
                },
                {"role": "user", "content": _build_user_prompt(payload)},
            ],
        }
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        try:
            with _client_scope(self._api_base, self._timeout_seconds, self._client) as client:
                response = client.post("/chat/completions", json=request_body, headers=headers)
        except httpx.HTTPError as exc:
            raise ScheduleExtractionUpstreamError(f"Muninn request failed: {exc}") from exc
        if response.status_code != 200:
            raise ScheduleExtractionUpstreamError(
                f"Muninn returned HTTP {response.status_code}."
            )
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ScheduleExtractionUpstreamError(
                "Muninn response missing choices[0].message.content."
            ) from exc
        if not isinstance(content, str):
            raise ScheduleExtractionUpstreamError("Muninn message content is not text.")
        return _parse_extractor_json(content, payload.subject)
