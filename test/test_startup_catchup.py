"""Unit tests for Phase 4 — Startup Gmail Synchronization & Catch-up."""

from datetime import datetime, timezone
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import urllib.request
import pytest

from app import MailAgentHTTPHandler, ThreadingHTTPServer
from email_parser import ParsedEmail
from ingestion import (
    IngestionReport,
    build_startup_catchup_query,
    build_sync_query,
    ingest_recent_emails,
)
from monitor_service import LOCK_NAME, ServiceLockHolder
from scheduler import CycleReport, run_cycle, run_startup_cycle
from storage import EmailStorage


def test_startup_catchup_query_syntax():
    """Verify build_startup_catchup_query generates overlap query with is:unread fallback."""
    # When checkpoint is None: returns initial 7d query
    assert build_startup_catchup_query(None) == "newer_than:7d"

    # When checkpoint is present: combines after:epoch with OR is:unread
    dt = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    expected_epoch = int(dt.timestamp()) - 7200
    query = build_startup_catchup_query(dt.isoformat())
    assert f"after:{expected_epoch}" in query
    assert "is:unread" in query


def test_startup_cycle_discovers_accumulated_messages(tmp_path: Path):
    """Verify that run_startup_cycle discovers accumulated unread messages on launch."""
    storage = EmailStorage(tmp_path / "startup_test.db")
    # Persist older checkpoint
    dt = datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc)
    storage.set_checkpoint(synced_at=dt)

    mock_gmail = MagicMock()
    mock_gmail.list_messages_paginated.return_value = MagicMock(
        messages=[
            {"id": "msg-downtime-1", "threadId": "t1"},
            {"id": "msg-downtime-2", "threadId": "t2"},
        ],
        complete=True,
    )

    def fake_get_msg(msg_id):
        return {
            "id": msg_id,
            "threadId": f"thread-{msg_id}",
            "internalDate": "1791806400000",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "recruiter@tech.com"},
                    {"name": "Subject", "value": f"Subject for {msg_id}"},
                    {"name": "Date", "value": "Wed, 14 Oct 2026 10:00:00 +0000"},
                ],
                "body": {"data": "VGVzdCBib2R5"},
            },
        }

    mock_gmail.get_message.side_effect = fake_get_msg
    mock_gmail.get_user_email.return_value = "user@test.com"

    report = run_startup_cycle(
        storage=storage,
        gmail_client=mock_gmail,
        batch_limit=5,
    )

    # Ingestion was executed with startup catch-up query
    assert report.ingestion is not None
    assert report.ingestion.discovered == 2
    assert report.ingestion.inserted == 2
    assert storage.get_email("msg-downtime-1") is not None
    assert storage.get_email("msg-downtime-2") is not None

    # Checkpoint was advanced because discovery was complete and no errors occurred
    new_checkpoint = storage.get_checkpoint()
    assert new_checkpoint is not None
    assert new_checkpoint != dt.isoformat()


def test_startup_incomplete_discovery_does_not_advance_checkpoint(tmp_path: Path):
    """Verify checkpoint does NOT advance if pagination was incomplete."""
    storage = EmailStorage(tmp_path / "incomplete_test.db")
    dt = datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc)
    storage.set_checkpoint(synced_at=dt)

    mock_gmail = MagicMock()
    # incomplete = False
    mock_gmail.list_messages_paginated.return_value = MagicMock(
        messages=[{"id": "msg-1", "threadId": "t1"}],
        complete=False,
    )
    mock_gmail.get_message.side_effect = Exception("Network disconnect during pagination")

    report = run_startup_cycle(
        storage=storage,
        gmail_client=mock_gmail,
    )

    # Checkpoint was NOT advanced
    assert storage.get_checkpoint() == dt.isoformat()


def test_single_instance_lock_prevents_duplicate_monitor_startup(tmp_path: Path):
    """Verify single-instance lock prevents two monitor services from running concurrently."""
    storage = EmailStorage(tmp_path / "lock_test.db")
    lock1 = ServiceLockHolder(storage, owner_id="proc_1")
    lock2 = ServiceLockHolder(storage, owner_id="proc_2")

    assert lock1.acquire() is True
    # Second process fails to acquire active lock
    assert lock2.acquire() is False

    lock1.release()
    # Now second process can acquire
    assert lock2.acquire() is True
    lock2.release()


def test_settings_api_endpoints(tmp_path: Path):
    """Verify GET and POST /api/settings endpoints."""
    storage = EmailStorage(tmp_path / "api_settings_test.db")
    MailAgentHTTPHandler.storage = storage

    server = ThreadingHTTPServer(("127.0.0.1", 0), MailAgentHTTPHandler)
    port = server.server_address[1]
    import threading
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base_url = f"http://127.0.0.1:{port}"

    try:
        # 1. GET default settings
        req = urllib.request.urlopen(f"{base_url}/api/settings")
        assert req.status == 200
        data = json.loads(req.read().decode("utf-8"))
        assert data["mark_as_read_enabled"] is False

        # 2. POST update setting to True
        post_data = json.dumps({"mark_as_read_enabled": True}).encode("utf-8")
        post_req = urllib.request.Request(
            f"{base_url}/api/settings",
            data=post_data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        post_res = urllib.request.urlopen(post_req)
        assert post_res.status == 200
        post_json = json.loads(post_res.read().decode("utf-8"))
        assert post_json["success"] is True
        assert post_json["mark_as_read_enabled"] is True

        # 3. Verify persisted in database
        assert storage.get_setting("mark_as_read_enabled") == "true"
    finally:
        server.shutdown()
        server.server_close()
