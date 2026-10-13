"""Unit tests for the run_digest_job scheduled execution module."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from digest import build_daily_digest, send_digest_toast
from email_parser import ParsedEmail
from run_digest_job import DigestJobReport, run_digest_job
from storage import EmailStorage


@pytest.fixture
def storage(tmp_path: Path) -> EmailStorage:
    db = EmailStorage(tmp_path / "job_test.db")
    email = ParsedEmail(
        message_id="msg-job-1",
        thread_id="t1",
        sender="fellowships@example.com",
        subject="AI Fellowship Opening",
        received_at=datetime.now(timezone.utc),
        body="Secret body text",
        body_source="plain",
    )
    db.save_email(email)
    db.save_analysis(
        "msg-job-1",
        {
            "category": "job",
            "urgency": "high",
            "summary": "Fellowship deadline soon.",
            "deadline": "2026-10-20T12:00:00+00:00",
            "requires_action": True,
        },
    )
    return db


def test_send_digest_toast_invokes_notifier(storage: EmailStorage) -> None:
    digest = build_daily_digest(storage)
    calls = []

    def mock_notifier(payload):
        calls.append(payload)
        return True

    res = send_digest_toast(digest, notifier=mock_notifier)
    assert res is True
    assert len(calls) == 1
    assert "Daily Digest" in calls[0].subject
    assert "1 deadline(s)" in calls[0].summary


def test_send_digest_toast_handles_notifier_exception_safely(storage: EmailStorage) -> None:
    digest = build_daily_digest(storage)

    def failing_notifier(payload):
        raise RuntimeError("Notification subsystem unavailable")

    res = send_digest_toast(digest, notifier=failing_notifier)
    assert res is False


def test_run_digest_job_successful_execution(storage: EmailStorage, tmp_path: Path) -> None:
    out_dir = tmp_path / "digests"
    log_dir = tmp_path / "logs"

    notifier_mock = MagicMock(return_value=True)

    report = run_digest_job(
        storage=storage,
        output_dir=out_dir,
        log_dir=log_dir,
        enable_toast=True,
        notifier=notifier_mock,
    )

    assert report.success is True
    assert report.saved_path is not None
    assert report.saved_path.exists()
    assert report.toast_delivered is True
    assert report.error is None
    notifier_mock.assert_called_once()

    # Check that log file was created
    log_file = log_dir / "digest_job.log"
    assert log_file.exists()
    log_content = log_file.read_text(encoding="utf-8")
    assert "Daily Digest successfully generated" in log_content
    assert "Secret body text" not in log_content


def test_run_digest_job_toast_failure_does_not_abort_saving(
    storage: EmailStorage, tmp_path: Path
) -> None:
    out_dir = tmp_path / "digests"
    log_dir = tmp_path / "logs"

    failing_notifier = MagicMock(side_effect=RuntimeError("WinRT crash"))

    report = run_digest_job(
        storage=storage,
        output_dir=out_dir,
        log_dir=log_dir,
        enable_toast=True,
        notifier=failing_notifier,
    )

    # Markdown report was still saved despite toast crash
    assert report.success is True
    assert report.saved_path is not None
    assert report.saved_path.exists()
    assert report.toast_delivered is False


def test_run_digest_job_captures_storage_exceptions(tmp_path: Path) -> None:
    out_dir = tmp_path / "digests"
    log_dir = tmp_path / "logs"

    bad_storage = MagicMock()
    bad_storage._connect.side_effect = RuntimeError("Database locked")

    report = run_digest_job(
        storage=bad_storage,
        output_dir=out_dir,
        log_dir=log_dir,
    )

    assert report.success is False
    assert report.saved_path is None
    assert report.error == "RuntimeError"
