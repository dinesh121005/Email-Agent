"""Tests for the important-message inbox queries, ordering, and CLI."""

from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

import cli
from email_parser import ParsedEmail
from inbox import (
    InboxItem,
    acknowledge_message,
    format_inbox_table,
    format_item_details,
    get_inbox_item,
    list_important_messages,
)
from storage import EmailStorage


@pytest.fixture
def storage(tmp_path: Path) -> EmailStorage:
    return EmailStorage(tmp_path / "inbox_test.db")


def _add_classified_email(
    storage: EmailStorage,
    message_id: str,
    *,
    subject: str = "Subject",
    category: str = "other",
    urgency: str = "normal",
    summary: str = "Summary text",
    deadline: str | None = None,
    requires_action: bool = False,
    received_at: str = "2026-10-12T10:00:00+00:00",
    body: str = "Super confidential email body text that must never be printed",
) -> None:
    dt = datetime.fromisoformat(received_at)
    email = ParsedEmail(
        message_id=message_id,
        thread_id=f"thread-{message_id}",
        sender="sender@example.com",
        subject=subject,
        received_at=dt,
        body=body,
        body_source="plain",
    )
    storage.save_email(email)
    storage.save_analysis(
        message_id,
        {
            "category": category,
            "urgency": urgency,
            "summary": summary,
            "deadline": deadline,
            "requires_action": requires_action,
        },
    )


def test_empty_inbox_returns_empty_list_and_clean_table(storage: EmailStorage) -> None:
    items = list_important_messages(storage=storage)
    assert items == []
    table = format_inbox_table(items)
    assert table == "No important messages found in inbox."


def test_list_important_messages_includes_opportunities_and_actionables(
    storage: EmailStorage,
) -> None:
    # 1. Actionable email -> included
    _add_classified_email(storage, "msg-act", requires_action=True, urgency="normal")
    # 2. Job opportunity -> included
    _add_classified_email(storage, "msg-job", category="job", urgency="low")
    # 3. Hackathon -> included
    _add_classified_email(storage, "msg-hack", category="hackathon", urgency="low")
    # 4. Competition -> included
    _add_classified_email(storage, "msg-comp", category="competition", urgency="normal")
    # 5. Routine non-actionable email -> excluded
    _add_classified_email(
        storage, "msg-routine", category="other", urgency="normal", requires_action=False
    )

    items = list_important_messages(storage=storage, sort_by_deadline=False)
    ids = {item.message_id for item in items}

    assert ids == {"msg-act", "msg-job", "msg-hack", "msg-comp"}


def test_sorting_by_deadline_and_urgency(storage: EmailStorage) -> None:
    # B: Imminent deadline (2026-10-14) -> should be #1
    _add_classified_email(
        storage,
        "msg-B",
        subject="Deadline Soon",
        urgency="low",
        deadline="2026-10-14T09:00:00+00:00",
        requires_action=True,
    )
    # A: Later deadline (2026-10-18) -> should be #2
    _add_classified_email(
        storage,
        "msg-A",
        subject="Deadline Later",
        urgency="high",
        deadline="2026-10-18T17:00:00+00:00",
        requires_action=True,
    )
    # C: No deadline, urgent urgency -> should be #3
    _add_classified_email(
        storage,
        "msg-C",
        subject="Urgent No Deadline",
        urgency="urgent",
        deadline=None,
        requires_action=True,
    )
    # D: No deadline, high urgency -> should be #4
    _add_classified_email(
        storage,
        "msg-D",
        subject="High No Deadline",
        urgency="high",
        deadline=None,
        requires_action=True,
    )
    # E: No deadline, normal urgency -> should be #5
    _add_classified_email(
        storage,
        "msg-E",
        subject="Normal No Deadline",
        urgency="normal",
        deadline=None,
        requires_action=True,
    )

    items = list_important_messages(storage=storage, sort_by_deadline=True)
    ordered_ids = [item.message_id for item in items]

    assert ordered_ids == ["msg-B", "msg-A", "msg-C", "msg-D", "msg-E"]


def test_acknowledgment_marks_handled_and_preserves_classification(
    storage: EmailStorage,
) -> None:
    _add_classified_email(
        storage,
        "msg-1",
        subject="Job Offer",
        category="job",
        urgency="urgent",
        summary="Please sign offer",
        deadline="2026-10-20T00:00:00+00:00",
        requires_action=True,
    )

    # Initial state: in unacknowledged list
    unack_before = list_important_messages(storage=storage, unacknowledged_only=True)
    assert len(unack_before) == 1
    assert unack_before[0].acknowledged_at is None

    # Acknowledge
    assert acknowledge_message("msg-1", storage=storage) is True

    # After: removed from unacknowledged list
    unack_after = list_important_messages(storage=storage, unacknowledged_only=True)
    assert len(unack_after) == 0

    # Still in all list
    all_items = list_important_messages(storage=storage, unacknowledged_only=False)
    assert len(all_items) == 1
    item = all_items[0]
    assert item.acknowledged_at is not None

    # Classification fields preserved exactly
    assert item.subject == "Job Offer"
    assert item.category == "job"
    assert item.urgency == "urgent"
    assert item.summary == "Please sign offer"
    assert item.deadline == "2026-10-20T00:00:00+00:00"
    assert item.requires_action is True


def test_acknowledge_invalid_or_missing_id_returns_false(storage: EmailStorage) -> None:
    assert acknowledge_message("unknown-id-123", storage=storage) is False
    assert acknowledge_message("   ", storage=storage) is False
    assert get_inbox_item("unknown-id-123", storage=storage) is None
    assert get_inbox_item("  ", storage=storage) is None


def test_inbox_persistence_across_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox_restart.db"

    # Session 1: Agent saves important email
    first = EmailStorage(db_path)
    _add_classified_email(first, "restart-msg", category="job", urgency="high")

    # Session 2: Laptop restarts -> new storage instance
    second = EmailStorage(db_path)
    items = list_important_messages(storage=second)
    assert len(items) == 1
    assert items[0].message_id == "restart-msg"
    assert items[0].category == "job"

    # Acknowledge in session 2
    assert acknowledge_message("restart-msg", storage=second) is True

    # Session 3: Reopen -> verified acknowledged
    third = EmailStorage(db_path)
    assert len(list_important_messages(storage=third, unacknowledged_only=True)) == 0
    all_items = list_important_messages(storage=third, unacknowledged_only=False)
    assert len(all_items) == 1
    assert all_items[0].acknowledged_at is not None


def test_body_is_never_exposed_in_table_or_details(storage: EmailStorage) -> None:
    secret_text = "CONFIDENTIAL_PAYROLL_NUMBERS_778899"
    _add_classified_email(
        storage,
        "msg-secret",
        subject="Payroll Update",
        summary="Payroll review required.",
        body=secret_text,
        requires_action=True,
    )

    items = list_important_messages(storage=storage)
    table = format_inbox_table(items)
    details = format_item_details(items[0])

    assert secret_text not in table
    assert secret_text not in details
    assert "Payroll Update" in table
    assert "Payroll review required." in details


def test_cli_list_command(storage: EmailStorage) -> None:
    _add_classified_email(storage, "msg-cli", subject="Hackathon Invite", category="hackathon")

    with patch("cli.EmailStorage", return_value=storage), patch(
        "sys.stdout", new_callable=StringIO
    ) as mock_stdout:
        code = cli.main(["list"])

    assert code == 0
    output = mock_stdout.getvalue()
    assert "msg-cli" in output
    assert "Hackathon Invite" in output
    assert "hackathon" in output


def test_cli_show_command(storage: EmailStorage) -> None:
    _add_classified_email(
        storage, "msg-show", subject="Job App", category="job", summary="Review resume"
    )

    with patch("cli.EmailStorage", return_value=storage), patch(
        "sys.stdout", new_callable=StringIO
    ) as mock_stdout:
        code = cli.main(["show", "msg-show"])

    assert code == 0
    output = mock_stdout.getvalue()
    assert "Message ID:     msg-show" in output
    assert "Review resume" in output

    # Unknown message returns 1
    with patch("cli.EmailStorage", return_value=storage):
        code_err = cli.main(["show", "unknown-msg"])
    assert code_err == 1


def test_cli_ack_command(storage: EmailStorage) -> None:
    _add_classified_email(storage, "msg-ack", category="job", requires_action=True)

    with patch("cli.EmailStorage", return_value=storage), patch(
        "sys.stdout", new_callable=StringIO
    ) as mock_stdout:
        code = cli.main(["ack", "msg-ack"])

    assert code == 0
    assert "marked as acknowledged" in mock_stdout.getvalue()
    assert storage.get_email("msg-ack")["acknowledged_at"] is not None

    # Acknowledging non-existent returns 1
    with patch("cli.EmailStorage", return_value=storage):
        code_err = cli.main(["ack", "missing-id"])
    assert code_err == 1


def test_cli_sync_command(storage: EmailStorage) -> None:
    from ingestion import IngestionReport
    mock_report = IngestionReport(discovered=3, inserted=2, duplicates=1, failed=0)

    with patch("cli.EmailStorage", return_value=storage), \
         patch("ingestion.ingest_recent_emails", return_value=mock_report) as mock_ingest, \
         patch("gmail_client.GmailClient") as mock_gmail, \
         patch("sys.stdout", new_callable=StringIO) as mock_stdout:

        mock_gmail.return_value.get_user_email.return_value = "user@example.com"
        code = cli.main(["sync", "--limit", "5"])

    assert code == 0
    output = mock_stdout.getvalue()
    assert "Synchronizing up to 5 messages" in output
    assert "discovered=3, inserted=2, duplicates=1, failed=0" in output
    mock_ingest.assert_called_once()


def test_cli_db_flag_configures_storage(tmp_path: Path) -> None:
    custom_db = tmp_path / "custom_cli.db"
    with patch("cli.list_important_messages", return_value=[]) as mock_list, \
         patch("sys.stdout", new_callable=StringIO):
        code = cli.main(["--db", str(custom_db), "list"])

    assert code == 0
    mock_list.assert_called_once()
    assert mock_list.call_args.kwargs["storage"].db_path == custom_db


def test_cli_auth_check_displays_identity(storage: EmailStorage) -> None:
    storage.set_checkpoint("gmail_sync", datetime.now(timezone.utc), account_email="active@example.com")
    with patch("cli.EmailStorage", return_value=storage), \
         patch("gmail_client.GmailClient") as mock_gmail, \
         patch("sys.stdout", new_callable=StringIO) as mock_stdout:

        mock_gmail.return_value.get_user_email.return_value = "active@example.com"
        code = cli.main(["auth", "--check"])

    assert code == 0
    output = mock_stdout.getvalue()
    assert "Currently authenticated Gmail account: active@example.com" in output
    assert "account: active@example.com" in output


def test_cli_reanalyze_command(storage: EmailStorage) -> None:
    _add_classified_email(storage, "msg-re", subject="Re-analysis target", category="job")
    with patch("cli.EmailStorage", return_value=storage), \
         patch("worker.process_pending_emails") as mock_process, \
         patch("sys.stdout", new_callable=StringIO) as mock_stdout:

        from worker import WorkerReport
        mock_process.return_value = WorkerReport(discovered=1, completed=1, failed=0, skipped=0)

        code = cli.main(["reanalyze", "--message-id", "msg-re"])

    assert code == 0
    assert "Reset email msg-re to pending" in mock_stdout.getvalue()
    assert storage.get_email("msg-re")["status"] == "pending"


