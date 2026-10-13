"""Unit tests for the desktop web application HTTP server and API endpoints."""

from datetime import datetime, timezone
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import urllib.request
import urllib.error

import pytest

from app import MailAgentHTTPHandler, ThreadingHTTPServer
from email_parser import ParsedEmail
from storage import EmailStorage


@pytest.fixture
def storage(tmp_path: Path) -> EmailStorage:
    db = EmailStorage(tmp_path / "app_test.db")
    email1 = ParsedEmail(
        message_id="app-msg-1",
        thread_id="t1",
        sender="internships@tech.com",
        subject="ML Internship Offer",
        received_at=datetime.now(timezone.utc),
        body="Secret body text",
        body_source="plain",
    )
    db.save_email(email1)
    db.save_analysis(
        "app-msg-1",
        {
            "category": "job",
            "urgency": "high",
            "summary": "Internship offer details.",
            "deadline": "2026-10-25T12:00:00+00:00",
            "requires_action": True,
        },
    )
    return db


@pytest.fixture
def test_server(storage: EmailStorage):
    MailAgentHTTPHandler.storage = storage
    # Bind to ephemeral port on localhost
    server = ThreadingHTTPServer(("127.0.0.1", 0), MailAgentHTTPHandler)
    port = server.server_address[1]
    import threading
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


def test_app_serves_html_index(test_server: str) -> None:
    req = urllib.request.urlopen(f"{test_server}/")
    assert req.status == 200
    assert "text/html" in req.headers.get("Content-Type", "")
    content = req.read().decode("utf-8")
    assert "Mail Agent" in content
    assert "Needs Action" in content
    assert "Opportunities" in content


def test_api_emails_endpoint(test_server: str) -> None:
    req = urllib.request.urlopen(f"{test_server}/api/emails")
    assert req.status == 200
    data = json.loads(req.read().decode("utf-8"))
    assert len(data) == 1
    assert data[0]["message_id"] == "app-msg-1"
    assert data[0]["category"] == "job"
    assert data[0]["urgency"] == "high"


def test_api_stats_endpoint(test_server: str) -> None:
    req = urllib.request.urlopen(f"{test_server}/api/stats")
    assert req.status == 200
    stats = json.loads(req.read().decode("utf-8"))
    assert stats["total"] == 1
    assert stats["needs_action"] == 1
    assert stats["opportunities"] == 1
    assert stats["urgent"] == 1
    assert stats["handled"] == 0


def test_api_ack_and_unack(test_server: str, storage: EmailStorage) -> None:
    # 1. Acknowledge
    ack_data = json.dumps({"message_id": "app-msg-1"}).encode("utf-8")
    req_ack = urllib.request.Request(
        f"{test_server}/api/ack",
        data=ack_data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    res_ack = urllib.request.urlopen(req_ack)
    assert res_ack.status == 200
    body = json.loads(res_ack.read().decode("utf-8"))
    assert body["success"] is True

    record = storage.get_email("app-msg-1")
    assert record["acknowledged_at"] is not None

    # 2. Unacknowledge
    req_unack = urllib.request.Request(
        f"{test_server}/api/unack",
        data=ack_data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    res_unack = urllib.request.urlopen(req_unack)
    assert res_unack.status == 200
    body_unack = json.loads(res_unack.read().decode("utf-8"))
    assert body_unack["success"] is True

    record_after = storage.get_email("app-msg-1")
    assert record_after["acknowledged_at"] is None


def test_api_digest_endpoint(test_server: str) -> None:
    req = urllib.request.urlopen(f"{test_server}/api/digest")
    assert req.status == 200
    text = req.read().decode("utf-8")
    assert "MAIL AGENT DAILY DIGEST" in text


def test_api_sync_endpoint(test_server: str) -> None:
    with patch("app.ingest_recent_emails") as mock_ingest:
        from ingestion import IngestionReport
        mock_ingest.return_value = IngestionReport(discovered=3, inserted=1, duplicates=2, failed=0)

        req = urllib.request.Request(f"{test_server}/api/sync", data=b"{}", method="POST")
        res = urllib.request.urlopen(req)
        assert res.status == 200
        data = json.loads(res.read().decode("utf-8"))
        assert data["inserted"] == 1
        assert data["duplicates"] == 2


def test_api_classify_endpoint(test_server: str) -> None:
    with patch("app.process_pending_emails") as mock_classify:
        from worker import WorkerReport
        mock_classify.return_value = WorkerReport(discovered=1, completed=1, failed=0, skipped=0)

        req = urllib.request.Request(f"{test_server}/api/classify", data=b"{}", method="POST")
        res = urllib.request.urlopen(req)
        assert res.status == 200
        data = json.loads(res.read().decode("utf-8"))
        assert data["completed"] == 1


def test_api_chat_endpoint(test_server: str) -> None:
    chat_payload = json.dumps({"message": "Which interviews are coming up?", "history": []}).encode("utf-8")
    with patch("llm_client.OllamaClient.generate", return_value="Here are the interviews:"):
        req = urllib.request.Request(
            f"{test_server}/api/chat",
            data=chat_payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        res = urllib.request.urlopen(req)
        assert res.status == 200
        data = json.loads(res.read().decode("utf-8"))
        assert "reply" in data
        assert "tool_used" in data
        assert data["tool_used"] == "find_interviews"


def test_api_spam_and_restore(test_server: str, storage: EmailStorage) -> None:
    spam_payload = json.dumps({"message_id": "app-msg-1"}).encode("utf-8")
    # Mark spam
    req_spam = urllib.request.Request(
        f"{test_server}/api/spam",
        data=spam_payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    res_spam = urllib.request.urlopen(req_spam)
    assert res_spam.status == 200
    assert json.loads(res_spam.read().decode("utf-8"))["success"] is True
    assert storage.get_email("app-msg-1")["spam_status"] == "confirmed_spam"

    # Restore
    req_restore = urllib.request.Request(
        f"{test_server}/api/restore",
        data=spam_payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    res_restore = urllib.request.urlopen(req_restore)
    assert res_restore.status == 200
    assert json.loads(res_restore.read().decode("utf-8"))["success"] is True
    assert storage.get_email("app-msg-1")["spam_status"] == "not_spam"


def test_api_reanalyze(test_server: str, storage: EmailStorage) -> None:
    payload = json.dumps({"message_id": "app-msg-1"}).encode("utf-8")
    from schemas import EmailAnalysis
    mock_analysis = EmailAnalysis(
        category="job",
        intent="job_opportunity",
        importance="high",
        urgency="high",
        summary="Reanalyzed summary",
        requires_action=True,
    )
    with patch("email_analyzer.analyze_email", return_value=mock_analysis):
        req = urllib.request.Request(
            f"{test_server}/api/reanalyze",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        res = urllib.request.urlopen(req)
        assert res.status == 200
        assert json.loads(res.read().decode("utf-8"))["success"] is True


def test_api_service_status(test_server: str, storage: EmailStorage) -> None:
    storage.record_service_status("background_monitor", "running", next_sync_at="2026-10-12T19:00:00Z")
    req = urllib.request.urlopen(f"{test_server}/api/service_status")
    assert req.status == 200
    data = json.loads(req.read().decode("utf-8"))
    assert data["service_name"] == "background_monitor"
    assert data["status"] == "running"
    assert "2026-10-12T19:00:00" in data["next_sync_at"]


def test_api_thread_endpoint(test_server: str, storage: EmailStorage) -> None:
    # Add a second email in thread t1
    email2 = ParsedEmail(
        message_id="app-msg-2",
        thread_id="t1",
        sender="internships@tech.com",
        subject="Re: ML Internship Offer",
        received_at=datetime.now(timezone.utc),
        body="Follow up on offer",
        body_source="plain",
    )
    storage.save_email(email2)
    req = urllib.request.urlopen(f"{test_server}/api/thread?thread_id=t1")
    assert req.status == 200
    data = json.loads(req.read().decode("utf-8"))
    assert len(data) == 2
    assert {m["message_id"] for m in data} == {"app-msg-1", "app-msg-2"}

