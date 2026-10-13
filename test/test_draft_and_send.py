"""Tests for Gmail draft creation, confirmed sending, and chatbot draft tools."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from app import MailAgentHTTPHandler
from chat import build_chat_registry
from gmail_client import GmailClient, GMAIL_MODIFY_SCOPE, GMAIL_SEND_SCOPE
from storage import EmailStorage


def test_gmail_client_create_draft():
    mock_service = MagicMock()
    mock_drafts = mock_service.users.return_value.drafts.return_value
    mock_drafts.create.return_value.execute.return_value = {
        "id": "draft-123",
        "message": {"id": "msg-draft-123", "threadId": "t-1"},
    }

    client = GmailClient(service=mock_service)
    res = client.create_draft(
        to="recruiter@company.com",
        subject="Interview Acceptance",
        body="Thank you, I confirm my availability.",
        thread_id="t-1",
    )

    assert res["draft_id"] == "draft-123"
    assert res["to"] == "recruiter@company.com"
    assert res["status"] == "draft_created"
    mock_drafts.create.assert_called_once()


def test_gmail_client_send_message_with_confirmation():
    mock_service = MagicMock()
    mock_creds = MagicMock()
    mock_creds.scopes = [GMAIL_SEND_SCOPE]
    mock_service._credentials = mock_creds

    mock_messages = mock_service.users.return_value.messages.return_value
    mock_messages.send.return_value.execute.return_value = {
        "id": "sent-msg-123",
        "threadId": "t-1",
    }

    client = GmailClient(service=mock_service)
    client._credentials = mock_creds

    res = client.send_message(
        to="recruiter@company.com",
        subject="Interview Acceptance",
        body="Thank you, I confirm my availability.",
        thread_id="t-1",
    )

    assert res["id"] == "sent-msg-123"
    assert res["status"] == "sent"
    mock_messages.send.assert_called_once()


def test_chat_create_email_draft_tool(tmp_path: Path):
    storage = EmailStorage(tmp_path / "chat_draft_test.db")
    mock_gmail = MagicMock()
    mock_gmail.create_draft.return_value = {
        "draft_id": "draft-abc",
        "message_id": "msg-abc",
        "thread_id": "thread-abc",
    }

    tools = build_chat_registry(storage=storage, gmail_client=mock_gmail)
    assert "create_email_draft" in tools

    result = tools.execute(
        "create_email_draft",
        to="hr@tech.com",
        subject="My Reply",
        body="Here is my follow-up response.",
    )

    assert result["status"] == "draft_created"
    assert result["draft_id"] == "draft-abc"
    assert result["requires_confirmation"] is True
    mock_gmail.create_draft.assert_called_once_with(
        to="hr@tech.com",
        subject="My Reply",
        body="Here is my follow-up response.",
        thread_id=None,
    )


def test_api_send_email_endpoint(tmp_path: Path):
    storage = EmailStorage(tmp_path / "api_send_test.db")
    MailAgentHTTPHandler.storage = storage

    server = ThreadingHTTPServer(("127.0.0.1", 0), MailAgentHTTPHandler)
    port = server.server_address[1]
    import threading
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base_url = f"http://127.0.0.1:{port}"

    try:
        # 1. Attempt sending without confirmed flag -> must be rejected 400
        unconfirmed_data = json.dumps({
            "to": "test@domain.com",
            "subject": "Hello",
            "body": "Test message",
            "confirmed": False,
        }).encode("utf-8")

        req1 = urllib.request.Request(
            f"{base_url}/api/send_email",
            data=unconfirmed_data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req1)
        assert exc_info.value.code == 400

        # 2. Send with confirmed=True with mock client
        with patch("gmail_client.GmailClient.send_message", return_value={"id": "sent-999", "status": "sent"}):
            confirmed_data = json.dumps({
                "to": "test@domain.com",
                "subject": "Hello",
                "body": "Test message",
                "confirmed": True,
            }).encode("utf-8")

            req2 = urllib.request.Request(
                f"{base_url}/api/send_email",
                data=confirmed_data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            res2 = urllib.request.urlopen(req2)
            assert res2.status == 200
            data2 = json.loads(res2.read().decode("utf-8"))
            assert data2["success"] is True
            assert data2["sent"]["id"] == "sent-999"
    finally:
        server.shutdown()
        server.server_close()


def test_plan_tool_execution_send_and_reply_variants():
    from chat import plan_tool_execution

    tool, args = plan_tool_execution("okay you send this mail to as the replay", selected_message_id="msg-hackathon-1")
    assert tool == "create_email_draft"
    assert args["message_id"] == "msg-hackathon-1"

    tool, args = plan_tool_execution("Draft a reply", selected_message_id="msg-hackathon-1")
    assert tool in {"create_email_draft", "get_email_details"}

    tool, args = plan_tool_execution("send this mail", selected_message_id="msg-hackathon-1")
    assert tool == "create_email_draft"
    assert args["message_id"] == "msg-hackathon-1"
