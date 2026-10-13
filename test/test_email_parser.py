import base64
from datetime import datetime, timezone

import pytest

from email_parser import EmailParseError, parse_gmail_message


def encoded(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode().rstrip("=")


def make_message(
    *,
    headers=None,
    body="Hello from Gmail",
    mime_type="text/plain",
    message_id="msg-1",
    internal_date="1791806400000",
):
    return {
        "id": message_id,
        "threadId": "thread-1",
        "internalDate": internal_date,
        "payload": {
            "mimeType": mime_type,
            "headers": headers or [
                {"name": "From", "value": "Recruiter <jobs@example.com>"},
                {"name": "Subject", "value": "Data Science Internship"},
                {"name": "Date", "value": "Mon, 12 Oct 2026 10:00:00 +0530"},
            ],
            "body": {"data": encoded(body)},
        },
    }


def test_parses_headers_and_normalizes_date():
    email = parse_gmail_message(make_message())

    assert email.message_id == "msg-1"
    assert email.thread_id == "thread-1"
    assert email.sender == "Recruiter <jobs@example.com>"
    assert email.subject == "Data Science Internship"
    assert email.received_at == datetime(
        2026, 10, 12, 4, 30, tzinfo=timezone.utc
    )
    assert email.body == "Hello from Gmail"
    assert email.body_source == "plain"


def test_header_names_are_case_insensitive():
    message = make_message(headers=[
        {"name": "sUbJeCt", "value": "Hackathon"},
        {"name": "fRoM", "value": "organizer@example.com"},
        {"name": "dAtE", "value": "Mon, 12 Oct 2026 10:00:00 +0530"},
    ])

    email = parse_gmail_message(message)

    assert email.subject == "Hackathon"
    assert email.sender == "organizer@example.com"


def test_prefers_plain_text_over_html():
    message = make_message()
    message["payload"] = {
        "mimeType": "multipart/alternative",
        "headers": message["payload"]["headers"],
        "parts": [
            {
                "mimeType": "text/plain",
                "body": {"data": encoded("Plain version")},
            },
            {
                "mimeType": "text/html",
                "body": {"data": encoded("<p>HTML version</p>")},
            },
        ],
    }

    email = parse_gmail_message(message)

    assert email.body == "Plain version"
    assert email.body_source == "plain"


def test_uses_nested_plain_text_part():
    message = make_message()
    message["payload"] = {
        "mimeType": "multipart/mixed",
        "headers": message["payload"]["headers"],
        "parts": [{
            "mimeType": "multipart/alternative",
            "parts": [{
                "mimeType": "text/plain",
                "body": {"data": encoded("Nested body")},
            }],
        }],
    }

    email = parse_gmail_message(message)

    assert email.body == "Nested body"


def test_uses_html_when_plain_text_is_missing():
    message = make_message(
        body="<p>Application <b>deadline</b> tomorrow</p>"
    )
    message["payload"]["mimeType"] = "text/html"

    email = parse_gmail_message(message)

    assert email.body == "Application deadline tomorrow"
    assert email.body_source == "html"


def test_ignores_attachment_content():
    message = make_message()
    message["payload"] = {
        "mimeType": "multipart/mixed",
        "headers": message["payload"]["headers"],
        "parts": [
            {
                "mimeType": "text/plain",
                "body": {"data": encoded("Email body")},
            },
            {
                "mimeType": "text/plain",
                "filename": "notes.txt",
                "body": {"data": encoded("Attachment content")},
            },
        ],
    }

    email = parse_gmail_message(message)

    assert email.body == "Email body"


def test_falls_back_to_internal_date():
    message = make_message(headers=[
        {"name": "Subject", "value": "Notice"},
    ])

    email = parse_gmail_message(message)

    assert email.received_at.tzinfo is not None
    assert email.received_at == datetime.fromtimestamp(
        1791806400, tz=timezone.utc
    )


def test_rejects_message_without_id():
    message = make_message()
    message.pop("id")

    with pytest.raises(EmailParseError, match="ID"):
        parse_gmail_message(message)


def test_rejects_message_without_payload():
    message = {"id": "msg-1"}

    with pytest.raises(EmailParseError, match="payload"):
        parse_gmail_message(message)


def test_rejects_message_without_any_usable_date():
    message = make_message(headers=[
        {"name": "Subject", "value": "Notice"},
        {"name": "Date", "value": "not a valid date"},
    ])
    message.pop("internalDate")

    with pytest.raises(EmailParseError, match="Date header"):
        parse_gmail_message(message)


def test_invalid_base64_body_raises_clear_error():
    message = make_message()
    message["payload"]["body"]["data"] = "%%%invalid%%%"

    with pytest.raises(EmailParseError, match="base64url"):
        parse_gmail_message(message)
