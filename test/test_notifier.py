"""Unit and integration tests for the notification subsystem."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from email_parser import ParsedEmail
from notifier import (
    NotificationPayload,
    NotificationReport,
    dispatch_notifications,
    windows_toast_notifier,
)
from storage import EmailStorage


@pytest.fixture
def storage(tmp_path: Path) -> EmailStorage:
    return EmailStorage(tmp_path / "notifier_test.db")


def _create_important_email(
    storage: EmailStorage,
    message_id: str = "msg-1",
    *,
    subject: str = "Interview Invitation",
    category: str = "job",
    urgency: str = "high",
    requires_action: bool = True,
    summary: str = "Technical interview scheduled for Friday.",
    deadline: str | None = "2026-10-16T10:00:00+00:00",
) -> None:
    email = ParsedEmail(
        message_id=message_id,
        thread_id=f"thread-{message_id}",
        sender="recruiter@example.com",
        subject=subject,
        received_at=datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc),
        body="Secret email body that must never appear in toasts or logs",
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


def test_successful_delivery_marks_email_as_notified(storage: EmailStorage) -> None:
    _create_important_email(storage, "msg-1")
    delivered_payloads: list[NotificationPayload] = []

    def fake_notifier(payload: NotificationPayload) -> bool:
        delivered_payloads.append(payload)
        return True

    report = dispatch_notifications(storage=storage, notifier=fake_notifier)

    assert report.discovered == 1
    assert report.delivered == 1
    assert report.failed == 0
    assert len(delivered_payloads) == 1

    payload = delivered_payloads[0]
    assert payload.message_id == "msg-1"
    assert payload.subject == "Interview Invitation"
    assert payload.category == "job"
    assert payload.urgency == "high"
    assert payload.summary == "Technical interview scheduled for Friday."
    assert payload.deadline == "2026-10-16T10:00:00+00:00"

    saved = storage.get_email("msg-1")
    assert saved is not None
    assert saved["notified_at"] is not None


def test_second_dispatch_does_not_resend_notified_email(storage: EmailStorage) -> None:
    _create_important_email(storage, "msg-1")
    calls: list[str] = []

    def fake_notifier(payload: NotificationPayload) -> bool:
        calls.append(payload.message_id)
        return True

    # First dispatch delivers
    report1 = dispatch_notifications(storage=storage, notifier=fake_notifier)
    assert report1.delivered == 1
    assert len(calls) == 1

    # Second dispatch skips already notified email
    report2 = dispatch_notifications(storage=storage, notifier=fake_notifier)
    assert report2.discovered == 0
    assert report2.delivered == 0
    assert report2.failed == 0
    assert len(calls) == 1


def test_false_delivery_leaves_email_unnotified_and_eligible(storage: EmailStorage) -> None:
    _create_important_email(storage, "msg-fail")

    def failing_notifier(payload: NotificationPayload) -> bool:
        return False

    report = dispatch_notifications(storage=storage, notifier=failing_notifier)

    assert report.discovered == 1
    assert report.delivered == 0
    assert report.failed == 1

    saved = storage.get_email("msg-fail")
    assert saved is not None
    assert saved["notified_at"] is None

    # Remains eligible in unnotified query and persistent inbox
    assert len(storage.get_unnotified_important()) == 1
    assert len(storage.get_unacknowledged_important()) == 1


def test_notifier_exception_does_not_abort_subsequent_deliveries(storage: EmailStorage) -> None:
    _create_important_email(storage, "msg-exc-1", subject="Subject 1")
    _create_important_email(storage, "msg-ok-2", subject="Subject 2")

    delivered: list[str] = []

    def flaky_notifier(payload: NotificationPayload) -> bool:
        if payload.message_id == "msg-exc-1":
            raise RuntimeError("Simulated notification service failure")
        delivered.append(payload.message_id)
        return True

    report = dispatch_notifications(storage=storage, notifier=flaky_notifier)

    assert report.discovered == 2
    assert report.delivered == 1
    assert report.failed == 1
    assert delivered == ["msg-ok-2"]

    # msg-exc-1 failed -> remains unnotified
    assert storage.get_email("msg-exc-1")["notified_at"] is None
    # msg-ok-2 succeeded -> marked notified
    assert storage.get_email("msg-ok-2")["notified_at"] is not None


def test_non_important_emails_are_never_dispatched(storage: EmailStorage) -> None:
    email = ParsedEmail(
        message_id="msg-routine",
        thread_id="thread-routine",
        sender="newsletter@example.com",
        subject="Weekly Digest",
        received_at=datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc),
        body="Digest body",
        body_source="plain",
    )
    storage.save_email(email)
    storage.save_analysis(
        "msg-routine",
        {
            "category": "other",
            "urgency": "low",
            "summary": "Routine weekly digest.",
            "deadline": None,
            "requires_action": False,
        },
    )

    calls: list[str] = []
    report = dispatch_notifications(storage=storage, notifier=lambda p: calls.append(p.message_id) or True)

    assert report.discovered == 0
    assert report.delivered == 0
    assert len(calls) == 0


def test_dispatcher_respects_batch_limit(storage: EmailStorage) -> None:
    for i in range(5):
        _create_important_email(storage, f"msg-batch-{i}", subject=f"Job {i}")

    calls: list[str] = []
    report = dispatch_notifications(
        storage=storage,
        notifier=lambda p: calls.append(p.message_id) or True,
        limit=2,
    )

    assert report.discovered == 2
    assert report.delivered == 2
    assert len(calls) == 2

    # 3 items remain unnotified
    remaining = storage.get_unnotified_important(limit=10)
    assert len(remaining) == 3


def test_dispatcher_validates_limit_parameter(storage: EmailStorage) -> None:
    with pytest.raises(ValueError, match="limit"):
        dispatch_notifications(storage=storage, limit=0)

    with pytest.raises(ValueError, match="limit"):
        dispatch_notifications(storage=storage, limit=501)


def test_restarting_preserves_unnotified_eligibility(tmp_path: Path) -> None:
    db_path = tmp_path / "restart_notify.db"
    first = EmailStorage(db_path)
    _create_important_email(first, "msg-survive")

    # Second instance simulates agent restart before notification
    second = EmailStorage(db_path)
    calls: list[str] = []
    report = dispatch_notifications(
        storage=second,
        notifier=lambda p: calls.append(p.message_id) or True,
    )

    assert report.delivered == 1
    assert calls == ["msg-survive"]
    assert second.get_email("msg-survive")["notified_at"] is not None


def test_windows_toast_notifier_success_with_mock() -> None:
    payload = NotificationPayload(
        message_id="msg-toast",
        subject="Hackathon Registration",
        category="hackathon",
        urgency="urgent",
        summary="Registration deadline tomorrow.",
        deadline="2026-10-13T18:00:00+00:00",
    )

    with patch("windows_toasts.WindowsToaster.show_toast") as mock_show:
        result = windows_toast_notifier(payload)

    assert result is True
    mock_show.assert_called_once()
    toast_arg = mock_show.call_args[0][0]
    # Verify title and summary format without body leak
    assert "[URGENT] Hackathon Registration" in toast_arg.text_fields[0]
    assert "[hackathon] Registration deadline tomorrow." in toast_arg.text_fields[1]
    assert "Deadline: 2026-10-13T18:00:00+00:00" in toast_arg.text_fields[1]


def test_windows_toast_notifier_failure_returns_false_without_raising() -> None:
    payload = NotificationPayload(
        message_id="msg-err",
        subject="Important Alert",
        category="competition",
        urgency="high",
        summary="Competition starts now.",
        deadline=None,
    )

    with patch(
        "windows_toasts.WindowsToaster.show_toast",
        side_effect=RuntimeError("WinRT ToastNotificationManager unavailable"),
    ):
        result = windows_toast_notifier(payload)

    assert result is False
