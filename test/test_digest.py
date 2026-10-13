"""Tests for the local Daily Digest builder, formatting, and file export."""

from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

import cli
from digest import (
    DailyDigest,
    DigestHealthStats,
    build_daily_digest,
    format_digest_markdown,
    format_digest_text,
    save_digest_markdown,
)
from email_parser import ParsedEmail
from storage import EmailStorage


@pytest.fixture
def storage(tmp_path: Path) -> EmailStorage:
    return EmailStorage(tmp_path / "digest_test.db")


def _add_test_email(
    storage: EmailStorage,
    message_id: str,
    *,
    subject: str = "Subject",
    category: str = "other",
    urgency: str = "normal",
    summary: str = "Summary",
    deadline: str | None = None,
    requires_action: bool = False,
    received_at: str | None = None,
    created_at: str | None = None,
    status: str = "completed",
    notified: bool = False,
    acknowledged: bool = False,
) -> None:
    now_utc = datetime.now(timezone.utc).isoformat()
    rx_iso = received_at or now_utc
    rx_dt = datetime.fromisoformat(rx_iso)

    email = ParsedEmail(
        message_id=message_id,
        thread_id=f"thread-{message_id}",
        sender="sender@example.com",
        subject=subject,
        received_at=rx_dt,
        body="Secret body text that must never appear in reports",
        body_source="plain",
    )
    storage.save_email(email)

    if status == "completed":
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
    elif status == "failed":
        storage.mark_processing(message_id)
        storage.mark_failed(message_id, "Classification failed")

    if notified:
        storage.mark_notified(message_id)
    if acknowledged:
        storage.mark_acknowledged(message_id)

    # If explicit created_at or received_at is passed, update SQLite record
    if created_at or received_at:
        with storage._connect() as conn:
            if created_at:
                conn.execute(
                    "UPDATE emails SET created_at = ? WHERE message_id = ?",
                    (created_at, message_id),
                )
            if received_at:
                conn.execute(
                    "UPDATE emails SET received_at = ? WHERE message_id = ?",
                    (received_at, message_id),
                )


def test_empty_database_produces_clean_digest(storage: EmailStorage) -> None:
    now = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)
    digest = build_daily_digest(storage=storage, now=now)

    assert digest.upcoming_deadlines == []
    assert digest.new_opportunities == []
    assert digest.awaiting_action == []
    assert digest.health_stats.total_emails == 0
    assert digest.health_stats.completed == 0
    assert digest.health_stats.unacknowledged_important == 0

    text = format_digest_text(digest)
    assert "No pending deadlines." in text
    assert "No new opportunities today." in text
    assert "All important items handled!" in text


def test_upcoming_deadlines_surfaces_unacknowledged_sorted(storage: EmailStorage) -> None:
    now = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)

    # 1. Deadline on Oct 16 -> should be 2nd
    _add_test_email(
        storage,
        "msg-dl-late",
        subject="Internship Application",
        category="job",
        urgency="normal",
        deadline="2026-10-16T17:00:00+00:00",
        requires_action=True,
    )
    # 2. Deadline on Oct 13 -> should be 1st
    _add_test_email(
        storage,
        "msg-dl-early",
        subject="Registration Closing",
        category="competition",
        urgency="urgent",
        deadline="2026-10-13T12:00:00+00:00",
        requires_action=True,
    )
    # 3. Deadline on Oct 14, but already acknowledged -> excluded from active deadlines!
    _add_test_email(
        storage,
        "msg-dl-done",
        subject="Signed Form",
        category="other",
        deadline="2026-10-14T10:00:00+00:00",
        requires_action=True,
        acknowledged=True,
    )

    digest = build_daily_digest(storage=storage, now=now)
    deadline_ids = [item.message_id for item in digest.upcoming_deadlines]

    assert deadline_ids == ["msg-dl-early", "msg-dl-late"]


def test_new_opportunities_distinguished_from_older_unacknowledged(
    storage: EmailStorage,
) -> None:
    now = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)

    # Opportunity 1: Arrived 4 hours ago (New today)
    t_new = (now - timedelta(hours=4)).isoformat()
    _add_test_email(
        storage,
        "opp-fresh",
        subject="New AI Engineer Role",
        category="job",
        urgency="high",
        received_at=t_new,
        created_at=t_new,
    )

    # Opportunity 2: Arrived 48 hours ago (Older, but still unacknowledged!)
    t_old = (now - timedelta(hours=48)).isoformat()
    _add_test_email(
        storage,
        "opp-older",
        subject="Hackathon Invite",
        category="hackathon",
        urgency="normal",
        received_at=t_old,
        created_at=t_old,
    )

    digest = build_daily_digest(
        storage=storage,
        now=now,
        opportunity_window_hours=24.0,
    )

    # New opportunities section only contains the fresh item
    new_opp_ids = [item.message_id for item in digest.new_opportunities]
    assert new_opp_ids == ["opp-fresh"]

    # Still awaiting action section contains BOTH opportunities
    awaiting_ids = {item.message_id for item in digest.awaiting_action}
    assert awaiting_ids == {"opp-fresh", "opp-older"}


def test_still_awaiting_action_excludes_routine_and_acknowledged(
    storage: EmailStorage,
) -> None:
    # 1. Actionable unacknowledged -> included
    _add_test_email(storage, "msg-act", requires_action=True, urgency="normal")
    # 2. Urgent unacknowledged -> included
    _add_test_email(storage, "msg-urg", urgency="urgent", requires_action=False)
    # 3. Actionable acknowledged -> excluded
    _add_test_email(
        storage, "msg-done", requires_action=True, urgency="high", acknowledged=True
    )
    # 4. Routine other low urgency -> excluded
    _add_test_email(
        storage, "msg-routine", category="other", urgency="low", requires_action=False
    )

    digest = build_daily_digest(storage=storage)
    awaiting_ids = {item.message_id for item in digest.awaiting_action}

    assert awaiting_ids == {"msg-act", "msg-urg"}


def test_agent_health_statistics_reporting(storage: EmailStorage) -> None:
    # 2 completed, 1 pending, 1 failed
    _add_test_email(storage, "msg-c1", status="completed", requires_action=True)
    _add_test_email(storage, "msg-c2", status="completed", category="job", notified=True)
    _add_test_email(storage, "msg-p1", status="pending")
    _add_test_email(storage, "msg-f1", status="failed")

    storage.set_checkpoint("gmail_sync", "2026-10-12T06:00:00+00:00")

    digest = build_daily_digest(storage=storage)
    h = digest.health_stats

    assert h.total_emails == 4
    assert h.completed == 2
    assert h.pending == 1
    assert h.failed == 1
    # msg-c1 is unnotified, msg-c2 is notified
    assert h.unnotified_important == 1
    assert h.unacknowledged_important == 2
    assert h.last_sync_at == "2026-10-12T06:00:00+00:00"


def test_digest_persistence_across_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "digest_restart.db"
    now = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc)

    # Session 1: Populate database
    first = EmailStorage(db_path)
    _add_test_email(first, "msg-persist", category="job", urgency="urgent")

    # Session 2: Reopen after agent restart
    second = EmailStorage(db_path)
    digest = build_daily_digest(storage=second, now=now)

    assert len(digest.awaiting_action) == 1
    assert digest.awaiting_action[0].message_id == "msg-persist"
    assert digest.health_stats.total_emails == 1


def test_save_digest_markdown_never_overwrites_existing_files(
    storage: EmailStorage, tmp_path: Path
) -> None:
    now = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc)
    _add_test_email(
        storage,
        "msg-doc",
        subject="Python Fellowship",
        category="job",
        urgency="high",
        summary="Fellowship application open.",
    )

    digest = build_daily_digest(storage=storage, now=now)
    output_dir = tmp_path / "test_digests"

    # Save 1st time
    file1 = save_digest_markdown(digest, output_dir=output_dir)
    assert file1.name == "digest_2026-10-12.md"
    content1 = file1.read_text(encoding="utf-8")
    assert "Python Fellowship" in content1
    assert "Secret body text" not in content1

    # Save 2nd time on same date -> does not overwrite, increments suffix
    file2 = save_digest_markdown(digest, output_dir=output_dir)
    assert file2.name == "digest_2026-10-12_1.md"
    assert file1.exists()
    assert file2.exists()


def test_cli_digest_command(storage: EmailStorage, tmp_path: Path) -> None:
    _add_test_email(
        storage,
        "msg-cli-d",
        subject="Hackathon Project",
        category="hackathon",
        urgency="high",
        summary="Submit project idea.",
    )

    out_dir = str(tmp_path / "cli_digests")

    with patch("cli.EmailStorage", return_value=storage), patch(
        "sys.stdout", new_callable=StringIO
    ) as mock_stdout:
        code = cli.main(["digest", "--save", "--output-dir", out_dir])

    assert code == 0
    output = mock_stdout.getvalue()
    assert "MAIL AGENT DAILY DIGEST" in output
    assert "Hackathon Project" in output
    assert "Digest saved to:" in output
    assert Path(out_dir).exists()
