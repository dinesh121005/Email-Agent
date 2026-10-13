"""Unit tests for Phase 5 — Gmail Read-State Synchronization and User Controls."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from email_parser import ParsedEmail
from gmail_client import (
    GMAIL_MODIFY_SCOPE,
    GMAIL_READONLY_SCOPE,
    GmailClient,
    GmailClientError,
)
from scheduler import (
    CycleReport,
    ReadStateSyncReport,
    run_cycle,
    sync_gmail_read_states,
)
from storage import EmailStorage


@pytest.fixture
def storage(tmp_path: Path) -> EmailStorage:
    db = EmailStorage(tmp_path / "read_sync_test.db")
    # Save a completed email
    email1 = ParsedEmail(
        message_id="msg-completed-1",
        thread_id="t1",
        sender="lead@stripe.com",
        subject="Interview Confirmed",
        received_at=datetime.now(timezone.utc),
        body="Your interview is confirmed.",
        body_source="plain",
    )
    db.save_email(email1)
    db.save_analysis(
        "msg-completed-1",
        {
            "category": "job",
            "intent": "interview_confirmation",
            "importance": "high",
            "urgency": "normal",
            "summary": "Interview confirmed.",
            "requires_action": False,
        },
    )

    # Save a pending email (not completed)
    email2 = ParsedEmail(
        message_id="msg-pending-2",
        thread_id="t2",
        sender="news@digest.com",
        subject="Weekly Digest",
        received_at=datetime.now(timezone.utc),
        body="Digest content",
        body_source="plain",
    )
    db.save_email(email2)
    return db


class MockGmailClient:
    def __init__(self, has_modify: bool = False, fail_modify: bool = False) -> None:
        self._has_modify = has_modify
        self._fail_modify = fail_modify
        self.marked_read_ids: list[str] = []

    def has_modify_permission(self) -> bool:
        return self._has_modify

    def mark_as_read(self, message_id: str) -> bool:
        if not self._has_modify:
            raise GmailClientError("Missing gmail.modify authorization")
        if self._fail_modify:
            raise GmailClientError("Simulated Gmail API network error")
        self.marked_read_ids.append(message_id)
        return True


def test_setting_defaults_to_disabled(storage: EmailStorage) -> None:
    """Verify that mark_as_read_enabled defaults to disabled."""
    setting = storage.get_setting("mark_as_read_enabled")
    assert setting is None  # Not enabled by default
    report = sync_gmail_read_states(storage=storage)
    assert report.discovered == 0
    assert report.modified == 0


def test_setting_persists_across_storage_instances(tmp_path: Path) -> None:
    """Verify setting persists across database reconnections."""
    db_file = tmp_path / "settings_persist.db"
    db1 = EmailStorage(db_file)
    db1.set_setting("mark_as_read_enabled", "true")

    db2 = EmailStorage(db_file)
    assert db2.get_setting("mark_as_read_enabled") == "true"

    db2.set_setting("mark_as_read_enabled", "false")
    db3 = EmailStorage(db_file)
    assert db3.get_setting("mark_as_read_enabled") == "false"


def test_read_sync_requires_modify_permission(storage: EmailStorage) -> None:
    """Verify that even if user enables the setting, read-only scope prevents modification."""
    storage.set_setting("mark_as_read_enabled", "true")
    client = MockGmailClient(has_modify=False)

    report = sync_gmail_read_states(storage=storage, client=client)
    assert report.modified == 0
    assert len(client.marked_read_ids) == 0

    record = storage.get_email("msg-completed-1")
    assert record["gmail_read_sync_status"] == "pending"


def test_successful_read_state_synchronization(storage: EmailStorage) -> None:
    """Verify eligible completed emails are marked read when enabled and authorized."""
    storage.set_setting("mark_as_read_enabled", "true")
    client = MockGmailClient(has_modify=True)

    report = sync_gmail_read_states(storage=storage, client=client)
    assert report.discovered == 1
    assert report.modified == 1
    assert "msg-completed-1" in client.marked_read_ids

    record = storage.get_email("msg-completed-1")
    assert record["gmail_read_sync_status"] == "completed"
    assert record["gmail_read_sync_attempted_at"] is not None


def test_pending_unprocessed_emails_are_never_marked_read(storage: EmailStorage) -> None:
    """Verify pending emails are excluded from read synchronization."""
    storage.set_setting("mark_as_read_enabled", "true")
    client = MockGmailClient(has_modify=True)

    report = sync_gmail_read_states(storage=storage, client=client)
    # Only msg-completed-1 is processed, not msg-pending-2
    assert "msg-pending-2" not in client.marked_read_ids

    rec_pending = storage.get_email("msg-pending-2")
    assert rec_pending["gmail_read_sync_status"] == "pending"


def test_failed_gmail_modify_records_error_and_preserves_local_inference(storage: EmailStorage) -> None:
    """Verify Gmail modification failure does not invalidate local completed analysis."""
    storage.set_setting("mark_as_read_enabled", "true")
    client = MockGmailClient(has_modify=True, fail_modify=True)

    report = sync_gmail_read_states(storage=storage, client=client)
    assert report.failed == 1
    assert report.modified == 0

    rec = storage.get_email("msg-completed-1")
    # Completed local analysis remains untouched
    assert rec["status"] == "completed"
    assert rec["summary"] == "Interview confirmed."
    # Failure recorded for retry
    assert rec["gmail_read_sync_status"] == "failed"
    assert rec["gmail_read_sync_retry_count"] == 1
    assert "GmailClientError" in rec["gmail_read_sync_error_type"]


def test_bounded_retries_prevent_infinite_loops(storage: EmailStorage) -> None:
    """Verify that repeatedly failing emails stop attempting after max_retries."""
    storage.set_setting("mark_as_read_enabled", "true")
    client = MockGmailClient(has_modify=True, fail_modify=True)

    for _ in range(3):
        sync_gmail_read_states(storage=storage, client=client, max_retries=3)

    rec = storage.get_email("msg-completed-1")
    assert rec["gmail_read_sync_retry_count"] == 3

    # On next attempt, it is skipped because max_retries is reached
    report = sync_gmail_read_states(storage=storage, client=client, max_retries=3)
    assert report.discovered == 0
    assert report.failed == 0


def test_gmail_client_mark_as_read_scope_enforcement() -> None:
    """Verify GmailClient.mark_as_read raises error when modify scope is absent."""
    client = GmailClient(credentials_path="nonexistent.json", token_path="nonexistent.json")
    client._service = MagicMock()
    # Mock credentials with only read-only scope
    mock_creds = MagicMock()
    mock_creds.scopes = [GMAIL_READONLY_SCOPE]
    client._credentials = mock_creds

    assert client.has_modify_permission() is False
    with pytest.raises(GmailClientError, match="Missing gmail.modify authorization"):
        client.mark_as_read("msg-123")


def test_gmail_client_mark_as_read_success_with_modify_scope() -> None:
    """Verify GmailClient.mark_as_read successfully calls modify when scope is present."""
    client = GmailClient(credentials_path="nonexistent.json", token_path="nonexistent.json")
    mock_service = MagicMock()
    client._service = mock_service
    mock_creds = MagicMock()
    mock_creds.scopes = [GMAIL_READONLY_SCOPE, GMAIL_MODIFY_SCOPE]
    client._credentials = mock_creds

    assert client.has_modify_permission() is True
    res = client.mark_as_read("msg-123")
    assert res is True
    mock_service.users().messages().modify.assert_called_once_with(
        userId="me",
        id="msg-123",
        body={"removeLabelIds": ["UNREAD"]},
    )
