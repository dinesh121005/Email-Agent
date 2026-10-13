"""Tests for Gmail-aware chatbot tools, live Gmail retrieval, and freshness handling."""

from datetime import datetime, timezone
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from chat import build_chat_registry
from email_parser import ParsedEmail
from storage import EmailStorage


@pytest.fixture
def storage(tmp_path: Path) -> EmailStorage:
    db = EmailStorage(tmp_path / "chat_gmail_test.db")
    email1 = ParsedEmail(
        message_id="msg-interview-101",
        thread_id="thread-int-99",
        sender="recruiter@innovate.ai",
        subject="AI Engineer Interview Invitation",
        received_at=datetime.now(timezone.utc),
        body="Congratulations! We invite you to an interview this Friday at 2pm.",
        body_source="plain",
        attachments=[
            {
                "filename": "Interview_Guide.pdf",
                "mime_type": "application/pdf",
                "size": 45000,
                "attachment_id": "att-pdf-01",
            }
        ],
    )
    db.save_email(email1)
    db.save_analysis(
        "msg-interview-101",
        {
            "category": "job",
            "intent": "interview_invitation",
            "importance": "high",
            "urgency": "urgent",
            "summary": "Interview invitation for AI Engineer position.",
            "deadline": "2026-10-16T14:00:00+00:00",
            "requires_action": True,
            "evidence": ["We invite you to an interview this Friday at 2pm."],
            "priority_reason": "Direct interview invitation with approaching date.",
        },
    )
    return db


def test_registry_contains_canonical_and_legacy_tools(storage: EmailStorage) -> None:
    registry = build_chat_registry(storage)
    tools = registry.list_tools()
    tool_names = {t["name"] for t in tools}

    # Canonical required tools
    assert "search_local_emails" in tool_names
    assert "get_email_details" in tool_names
    assert "refresh_gmail_messages" in tool_names
    assert "get_upcoming_deadlines" in tool_names
    assert "get_pending_actions" in tool_names
    assert "get_email_attachments" in tool_names
    assert "get_thread_context" in tool_names
    assert "explain_email_decision" in tool_names

    # Backwards-compatible aliases
    assert "search_emails" in tool_names
    assert "find_upcoming_deadlines" in tool_names
    assert "list_pending_actions" in tool_names
    assert "explain_classification" in tool_names


def test_get_email_attachments_from_storage(storage: EmailStorage) -> None:
    registry = build_chat_registry(storage)
    result = registry.execute("get_email_attachments", message_id="msg-interview-101")
    assert result["found"] is True
    assert result["attachment_count"] == 1
    assert result["attachments"][0]["filename"] == "Interview_Guide.pdf"
    assert result["freshness"] == "locally_cached"


def test_get_email_attachments_live_fetch(storage: EmailStorage) -> None:
    fake_client = MagicMock()
    fake_client.get_attachment.return_value = b"%PDF-1.4 Mock content"

    registry = build_chat_registry(storage, gmail_client=fake_client)
    result = registry.execute(
        "get_email_attachments",
        message_id="msg-interview-101",
        attachment_id="att-pdf-01",
        live_gmail_fetch=True,
    )
    assert result["found"] is True
    assert result["fetched_bytes"] == len(b"%PDF-1.4 Mock content")
    assert result["freshness"] == "freshly_verified_gmail"
    fake_client.get_attachment.assert_called_once_with("msg-interview-101", "att-pdf-01")


def test_get_thread_context_local_and_live(storage: EmailStorage) -> None:
    fake_client = MagicMock()
    fake_client.get_thread.return_value = {
        "id": "thread-int-99",
        "messages": [
            {"id": "msg-interview-101", "snippet": "Interview invitation"},
            {"id": "msg-interview-102", "snippet": "Follow up with schedule"},
        ],
    }

    registry = build_chat_registry(storage, gmail_client=fake_client)

    # 1. Local thread lookup
    local_res = registry.execute("get_thread_context", thread_id="thread-int-99", live_gmail_fetch=False)
    assert local_res["found"] is True
    assert local_res["message_count"] == 1
    assert local_res["freshness"] == "locally_cached"

    # 2. Live Gmail thread fetch
    live_res = registry.execute("get_thread_context", thread_id="thread-int-99", live_gmail_fetch=True)
    assert live_res["found"] is True
    assert live_res["message_count"] == 2
    assert live_res["freshness"] == "freshly_verified_gmail"
    fake_client.get_thread.assert_called_once_with("thread-int-99")


def test_refresh_gmail_messages_live_and_offline_handling(storage: EmailStorage) -> None:
    fake_client = MagicMock()
    fake_client.get_user_email.return_value = "user@test.com"
    fake_client.list_messages_paginated.return_value = MagicMock(messages=[], complete=True)

    registry = build_chat_registry(storage, gmail_client=fake_client)
    res = registry.execute("refresh_gmail_messages", max_messages=5)
    assert res["status"] == "success"
    assert res["freshness"] == "freshly_verified_gmail"

    # Offline / Error case
    fake_offline_client = MagicMock()
    fake_offline_client.list_messages_paginated.side_effect = RuntimeError("Gmail API network timeout")
    fake_offline_client.get_user_email.return_value = "user@test.com"
    offline_registry = build_chat_registry(storage, gmail_client=fake_offline_client)
    err_res = offline_registry.execute("refresh_gmail_messages", max_messages=5)
    assert err_res["status"] == "error"
    assert "Gmail API network timeout" in err_res["message"]
    assert err_res["freshness"] == "locally_cached_only"


def test_explain_email_decision(storage: EmailStorage) -> None:
    registry = build_chat_registry(storage)
    res = registry.execute("explain_email_decision", message_id="msg-interview-101")
    assert res["found"] is True
    assert res["decision"]["intent"] == "interview_invitation"
    assert res["decision"]["importance"] == "high"
    assert "Direct interview invitation" in res["decision"]["priority_reason"]
    assert res["freshness"] == "locally_cached"
