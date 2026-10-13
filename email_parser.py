"""Parse Gmail API messages into normalized email objects."""

import base64
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ParsedEmail(BaseModel):
    """Normalized email fields used by downstream components."""

    model_config = ConfigDict(extra="forbid")

    message_id: str
    thread_id: str | None = None
    sender: str = ""
    subject: str = ""
    received_at: datetime
    body: str = ""
    body_source: str = "empty"
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class EmailParseError(ValueError):
    """Raised when a Gmail message cannot be parsed safely."""


class _HTMLTextExtractor(HTMLParser):
    """Extract readable text while ignoring HTML markup."""

    BLOCK_TAGS = {
        "address", "article", "br", "div", "footer", "h1", "h2",
        "h3", "h4", "header", "hr", "li", "ol", "p", "section",
        "table", "tr", "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fragments: list[str] = []
        self._hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._hidden_depth += 1
        if self._hidden_depth == 0 and tag in self.BLOCK_TAGS:
            self.fragments.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._hidden_depth:
            self._hidden_depth -= 1
        if self._hidden_depth == 0 and tag in self.BLOCK_TAGS:
            self.fragments.append("\n")

    def handle_data(self, data: str) -> None:
        if self._hidden_depth == 0:
            self.fragments.append(data)

    def get_text(self) -> str:
        text = "".join(self.fragments)
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
        return "\n".join(line for line in lines if line).strip()


def _decode_base64url(data: str) -> str:
    """Decode a Gmail base64url body into UTF-8 text."""
    try:
        encoded = data.encode("ascii")
        padding = b"=" * (-len(encoded) % 4)
        decoded = base64.b64decode(
            encoded + padding,
            altchars=b"-_",
            validate=True,
        )
        return decoded.decode("utf-8", errors="replace")
    except (UnicodeEncodeError, ValueError) as exc:
        raise EmailParseError("Invalid base64url message body.") from exc


def _header_map(payload: dict[str, Any]) -> dict[str, str]:
    """Read headers case-insensitively, keeping the first occurrence."""
    result: dict[str, str] = {}

    for header in payload.get("headers", []):
        name = header.get("name", "")
        value = header.get("value", "")
        if name and name.casefold() not in result:
            result[name.casefold()] = value

    return result


def _walk_parts(part: dict[str, Any]):
    """Yield a MIME part and all nested parts in order."""
    yield part
    for child in part.get("parts", []) or []:
        yield from _walk_parts(child)


def _extract_body(payload: dict[str, Any]) -> tuple[str, str]:
    """Prefer plain text; use HTML text if plain text is unavailable."""
    plain_parts: list[str] = []
    html_parts: list[str] = []

    for part in _walk_parts(payload):
        mime_type = part.get("mimeType", "").casefold()

        # Ignore attachment payloads. Attachments will be handled separately.
        if part.get("filename"):
            continue

        body_data = (part.get("body") or {}).get("data")
        if not body_data:
            continue

        text = _decode_base64url(body_data)

        if mime_type == "text/plain":
            plain_parts.append(text)
        elif mime_type == "text/html":
            html_parts.append(text)

    if plain_parts:
        return "\n".join(plain_parts).strip(), "plain"

    if html_parts:
        extractor = _HTMLTextExtractor()
        extractor.feed("\n".join(html_parts))
        extractor.close()
        return extractor.get_text(), "html"

    return "", "empty"


def _parse_received_at(date_header: str | None, internal_date: Any) -> datetime:
    """Parse the Date header, falling back to Gmail's internal timestamp."""
    if date_header:
        try:
            parsed = parsedate_to_datetime(date_header)
            if parsed is not None and parsed.tzinfo is not None:
                return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            pass

    if internal_date is not None:
        try:
            milliseconds = int(internal_date)
            if milliseconds < 0:
                raise ValueError("Negative Gmail internal timestamp")
            return datetime.fromtimestamp(
                milliseconds / 1000,
                tz=timezone.utc,
            )
        except (TypeError, ValueError, OverflowError, OSError):
            pass

    raise EmailParseError(
        "Message has no usable timezone-aware Date header or internalDate."
    )


def parse_gmail_message(message: dict[str, Any]) -> ParsedEmail:
    """Convert a Gmail API message resource into a normalized email."""
    if not isinstance(message, dict):
        raise EmailParseError("Gmail message must be a dictionary.")

    message_id = message.get("id")
    if not isinstance(message_id, str) or not message_id.strip():
        raise EmailParseError("Gmail message is missing its ID.")

    payload = message.get("payload")
    if not isinstance(payload, dict):
        raise EmailParseError("Gmail message is missing its payload.")

    headers = _header_map(payload)
    body, body_source = _extract_body(payload)

    attachments: list[dict[str, Any]] = []
    for part in _walk_parts(payload):
        filename = part.get("filename")
        if filename:
            body_dict = part.get("body") or {}
            attachments.append({
                "filename": filename,
                "mime_type": part.get("mimeType", "application/octet-stream"),
                "size": body_dict.get("size", 0),
                "attachment_id": body_dict.get("attachmentId", ""),
            })

    return ParsedEmail(
        message_id=message_id,
        thread_id=message.get("threadId"),
        sender=headers.get("from", ""),
        subject=headers.get("subject", ""),
        received_at=_parse_received_at(
            headers.get("date"),
            message.get("internalDate"),
        ),
        body=body,
        body_source=body_source,
        attachments=attachments,
    )
