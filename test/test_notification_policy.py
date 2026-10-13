"""Tests for the selective notification policy, priority matrix, deduplication, and escalation."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from notifier import (
    NotificationPayload,
    NotificationPolicy,
    dispatch_notifications,
    evaluate_notification_eligibility,
    windows_toast_notifier,
)
from storage import EmailStorage


@pytest.fixture
def storage(tmp_path: Path) -> EmailStorage:
    return EmailStorage(tmp_path / "notify_policy_test.db")


def test_immediate_priority_interview() -> None:
    policy = NotificationPolicy()
    eligible, reason = evaluate_notification_eligibility(
        {
            "intent": "interview_invitation",
            "importance": "high",
            "urgency": "urgent",
            "spam_status": "not_spam",
            "requires_action": 1,
            "subject": "Invitation: Technical Interview with Anthropic",
        },
        policy=policy,
    )
    assert eligible is True
    assert "interview" in reason.lower()


def test_immediate_priority_urgent_actionable_request() -> None:
    policy = NotificationPolicy()
    eligible, reason = evaluate_notification_eligibility(
        {
            "intent": "job_opportunity",
            "importance": "high",
            "urgency": "urgent",
            "spam_status": "not_spam",
            "requires_action": 1,
            "subject": "Action Required: Complete Security Assessment",
        },
        policy=policy,
    )
    assert eligible is True
    assert "urgent" in reason.lower()


def test_deadline_sensitive_within_policy_window() -> None:
    policy = NotificationPolicy(deadline_window_hours=72)
    approaching = datetime.now(timezone.utc) + timedelta(hours=24)
    eligible, reason = evaluate_notification_eligibility(
        {
            "category": "competition",
            "intent": "competition",
            "importance": "normal",
            "urgency": "normal",
            "spam_status": "not_spam",
            "requires_action": 1,
            "deadline": approaching.isoformat(),
            "subject": "Hackathon Submission Deadline Approaching",
        },
        policy=policy,
    )
    assert eligible is True
    assert "deadline" in reason.lower()


def test_silent_for_promotional_and_spam() -> None:
    policy = NotificationPolicy()
    # Promotional newsletter
    el_p, reason_p = evaluate_notification_eligibility(
        {
            "intent": "promotional",
            "category": "newsletter",
            "importance": "normal",
            "urgency": "normal",
            "spam_status": "not_spam",
            "requires_action": 0,
            "subject": "Weekly Tech Digest & Deals",
        },
        policy=policy,
    )
    assert el_p is False
    assert "suppressed" in reason_p.lower()

    # Confirmed spam
    el_s, reason_s = evaluate_notification_eligibility(
        {
            "intent": "job_opportunity",
            "importance": "high",
            "urgency": "urgent",
            "spam_status": "confirmed_spam",
            "requires_action": 1,
            "subject": "Urgent Work from Home Scam",
        },
        policy=policy,
    )
    assert el_s is False
    assert "suppressed" in reason_s.lower()


def test_review_queue_uncertain_email() -> None:
    policy_no_alert = NotificationPolicy(notify_uncertain_review=False)
    el, reason = evaluate_notification_eligibility(
        {
            "intent": "uncertain",
            "importance": "normal",
            "urgency": "normal",
            "spam_status": "not_spam",
            "requires_action": 1,
            "subject": "Unknown update from external contact",
        },
        policy=policy_no_alert,
    )
    assert el is False
    assert "review queue" in reason.lower()

    policy_with_alert = NotificationPolicy(notify_uncertain_review=True)
    el2, reason2 = evaluate_notification_eligibility(
        {
            "intent": "uncertain",
            "importance": "normal",
            "urgency": "normal",
            "spam_status": "not_spam",
            "requires_action": 1,
            "subject": "Unknown update from external contact",
        },
        policy=policy_with_alert,
    )
    assert el2 is True
    assert "review" in reason2.lower()


def test_deduplication_and_bounded_escalation(storage: EmailStorage) -> None:
    now = datetime.now(timezone.utc)
    from email_parser import ParsedEmail
    email = ParsedEmail(
        message_id="msg-esc-1",
        thread_id="t1",
        sender="lead@company.com",
        subject="Urgent Interview Confirmation",
        received_at=now,
        body="Please confirm your availability.",
        body_source="plain",
    )
    storage.save_email(email)
    storage.save_analysis(
        "msg-esc-1",
        {
            "category": "job",
            "intent": "interview_invitation",
            "importance": "high",
            "urgency": "urgent",
            "summary": "Urgent interview schedule confirmation.",
            "requires_action": True,
            "spam_status": "not_spam",
        },
    )

    mock_notifier = MagicMock(return_value=True)

    # First cycle -> Notifies
    policy = NotificationPolicy(escalation_interval_hours=24, max_escalation_count=2)
    report1 = dispatch_notifications(storage=storage, notifier=mock_notifier, policy=policy)
    assert report1.delivered == 1
    mock_notifier.assert_called_once()

    row = storage.get_email("msg-esc-1")
    assert row["notified_at"] is not None
    assert row["notification_count"] == 1

    # Immediate second cycle (no 24h elapsed) -> Suppressed (deduplication across restarts/runs)
    mock_notifier.reset_mock()
    report2 = dispatch_notifications(storage=storage, notifier=mock_notifier, policy=policy)
    assert report2.discovered == 0
    assert report2.delivered == 0
    mock_notifier.assert_not_called()

    # Fast forward 25 hours later (unhandled) -> Escalates (notification 2)
    past_notified = (now - timedelta(hours=25)).isoformat()
    with storage._connect() as conn:
        conn.execute("UPDATE emails SET notified_at = ? WHERE message_id = ?", (past_notified, "msg-esc-1"))

    report3 = dispatch_notifications(storage=storage, notifier=mock_notifier, policy=policy)
    assert report3.delivered == 1
    mock_notifier.assert_called_once()
    assert storage.get_email("msg-esc-1")["notification_count"] == 2

    # Fast forward another 25 hours -> Max escalations reached (2), so suppressed
    mock_notifier.reset_mock()
    with storage._connect() as conn:
        conn.execute("UPDATE emails SET notified_at = ? WHERE message_id = ?", (past_notified, "msg-esc-1"))
    report4 = dispatch_notifications(storage=storage, notifier=mock_notifier, policy=policy)
    assert report4.discovered == 0
    assert report4.delivered == 0
    mock_notifier.assert_not_called()
