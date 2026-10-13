"""Comprehensive evaluation test suite for AI email intelligence, policy, and safety."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import pytest

from deadline_normalizer import normalize_deadline
from email_analyzer import analyze_email
from email_parser import ParsedEmail
from notifier import NotificationPayload, dispatch_notifications
from schemas import EmailAnalysis
from storage import EmailStorage
from urgency_policy import evaluate_priority_and_safety


class FakeOllamaClient:
    def __init__(self, response_map: dict[str, str] | None = None) -> None:
        self.response_map = response_map or {}
        self.default_response = json.dumps({
            "category": "other",
            "intent": "other",
            "importance": "normal",
            "urgency": "normal",
            "spam_status": "not_spam",
            "requires_action": False,
            "summary": "Default fake summary",
            "deadline": None,
            "recommended_action": None,
            "priority_reason": "Default reason",
            "evidence": [],
        })

    def generate(self, prompt: str) -> str:
        for key, resp in self.response_map.items():
            if key in prompt:
                return resp
        return self.default_response


def test_eval_genuine_interview_without_explicit_deadline():
    """Verify that a genuine interview without a deadline is marked high importance and actionable."""
    received = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)
    client = FakeOllamaClient({
        "HackerRank Technical Screening": json.dumps({
            "category": "job",
            "intent": "interview_invitation",
            "importance": "high",
            "urgency": "normal",
            "spam_status": "not_spam",
            "requires_action": True,
            "summary": "Technical interview screening for HackerRank SDE Intern.",
            "deadline": None,
            "recommended_action": "Check email for interview details and respond with availability.",
            "priority_reason": "Interview invitation from recruiter.",
            "evidence": ["invite you for a 60-minute technical interview screening"],
        })
    })

    analysis = analyze_email(
        subject="Software Development Engineer Intern - HackerRank Technical Screening",
        body="We would like to invite you for a 60-minute technical interview screening with our engineering lead.",
        client=client,
        received_at=received,
    )

    assert analysis.intent == "interview_invitation"
    assert analysis.importance == "high"
    assert analysis.requires_action is True
    assert analysis.spam_status == "not_spam"
    assert "HackerRank" in analysis.summary or "interview" in analysis.priority_reason.lower()


def test_eval_interview_happening_in_two_hours_is_critical_and_urgent():
    """Verify that an interview starting in 2 hours is critical importance and urgent."""
    received = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
    client = FakeOllamaClient({
        "starting at 2:00 PM today": json.dumps({
            "category": "job",
            "intent": "interview_invitation",
            "importance": "critical",
            "urgency": "urgent",
            "spam_status": "not_spam",
            "requires_action": True,
            "summary": "Technical interview starting today at 2 PM.",
            "deadline": "2026-10-14T14:00:00+00:00",
            "recommended_action": "Join Google Meet link promptly.",
            "priority_reason": "Imminent interview scheduled in 2 hours.",
            "evidence": ["starts today at 2:00 PM UTC"],
        })
    })

    analysis = analyze_email(
        subject="Reminder: Technical Interview starting at 2:00 PM today",
        body="This is a quick reminder that your technical interview with Stripe starts today at 2:00 PM UTC on Google Meet.",
        client=client,
        received_at=received,
    )

    assert analysis.intent == "interview_invitation"
    assert analysis.importance == "critical"
    assert analysis.urgency == "urgent"
    assert analysis.requires_action is True
    assert analysis.spam_status == "not_spam"


def test_eval_job_promotions_and_tracking_ads_are_suppressed():
    """Verify promotional job advertisements and profile view upsells are classified as suppressed spam."""
    received = datetime(2026, 10, 13, 11, 0, tzinfo=timezone.utc)
    client = FakeOllamaClient({
        "profile views": json.dumps({
            "category": "job",  # Model might mistakenly say job
            "intent": "promotional",
            "importance": "low",
            "urgency": "low",
            "spam_status": "suspected_spam",
            "requires_action": False,
            "summary": "Marketing promotion for profile views.",
            "deadline": None,
            "recommended_action": None,
            "priority_reason": "Promotional email.",
            "evidence": ["Get up to 4x more profile views"],
        })
    })

    analysis = analyze_email(
        subject="Get up to 4x more profile views on Naukri",
        body="Upgrade to Naukri FastForward today and get highlighted to 1000s of recruiters.",
        client=client,
        received_at=received,
    )

    assert analysis.intent == "promotional"
    assert analysis.spam_status in ("suspected_spam", "confirmed_spam")
    assert analysis.importance == "low"
    assert analysis.requires_action is False


def test_eval_job_tracking_service_suppressed():
    received = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc)
    client = FakeOllamaClient()

    analysis = analyze_email(
        subject="Track your job applications with Huntr",
        body="Tired of messy spreadsheets? Use Huntr auto-track chrome extension to organize job applications.",
        client=client,
        received_at=received,
    )

    # Policy overrides promotional noise to suspected_spam and low importance
    assert analysis.intent == "promotional"
    assert analysis.spam_status == "suspected_spam"
    assert analysis.importance == "low"
    assert analysis.requires_action is False


def test_eval_application_rejection_low_importance_no_action():
    received = datetime(2026, 10, 13, 14, 0, tzinfo=timezone.utc)
    client = FakeOllamaClient()

    analysis = analyze_email(
        subject="Update on your application for Machine Learning Engineer",
        body="Thank you for taking the time to interview. We have decided to move forward with another candidate.",
        client=client,
        received_at=received,
    )

    assert analysis.intent == "application_update"
    assert analysis.importance == "low"
    assert analysis.requires_action is False
    assert analysis.urgency == "low"
    assert analysis.spam_status == "not_spam"


def test_eval_ambiguous_opportunity_review_queue():
    received = datetime(2026, 10, 14, 14, 0, tzinfo=timezone.utc)
    client = FakeOllamaClient({
        "collaboration": json.dumps({
            "category": "uncertain",
            "intent": "uncertain",
            "importance": "normal",
            "urgency": "normal",
            "spam_status": "suspected_spam",
            "requires_action": False,
            "summary": "Ambiguous message regarding potential career synergy.",
            "deadline": None,
            "recommended_action": "Review in Review Queue.",
            "priority_reason": "Ambiguous sender and intent.",
            "evidence": ["explore potential synergies"],
        })
    })

    analysis = analyze_email(
        subject="Exciting collaboration and career opportunity inquiry",
        body="We noticed your profile online and would like to explore potential synergies with our stealth venture.",
        client=client,
        received_at=received,
    )

    assert analysis.intent == "uncertain"
    assert analysis.spam_status == "suspected_spam"
    assert analysis.importance == "normal"


def test_eval_database_migration_existing_record_compatibility(tmp_path: Path):
    """Test that existing databases from older schema versions migrate idempotently without data loss."""
    import sqlite3
    db_file = tmp_path / "legacy.db"

    # Create older legacy table without extended columns
    conn = sqlite3.connect(db_file)
    conn.execute("""
        CREATE TABLE emails (
            message_id TEXT PRIMARY KEY,
            thread_id TEXT,
            sender TEXT NOT NULL DEFAULT '',
            subject TEXT NOT NULL DEFAULT '',
            received_at TEXT NOT NULL,
            body TEXT NOT NULL DEFAULT '',
            body_source TEXT NOT NULL DEFAULT 'empty',
            status TEXT NOT NULL DEFAULT 'completed',
            category TEXT,
            urgency TEXT,
            summary TEXT,
            deadline TEXT,
            requires_action INTEGER,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        INSERT INTO emails (message_id, subject, received_at, status, category, urgency, summary, requires_action, created_at, updated_at)
        VALUES ('legacy-msg-1', 'Legacy Interview', '2026-10-10T10:00:00+00:00', 'completed', 'job', 'high', 'Legacy summary', 1, '2026-10-10T10:00:00+00:00', '2026-10-10T10:00:00+00:00')
    """)
    conn.commit()
    conn.close()

    # Open with EmailStorage: triggers automatic idempotent migration
    storage = EmailStorage(db_file)
    saved = storage.get_email("legacy-msg-1")

    assert saved is not None
    assert saved["message_id"] == "legacy-msg-1"
    assert saved["subject"] == "Legacy Interview"
    assert "intent" in saved
    assert "importance" in saved
    assert "spam_status" in saved
    assert saved["spam_status"] == "not_spam"


def test_eval_suppressed_emails_not_in_primary_inbox_and_can_be_restored(tmp_path: Path):
    storage = EmailStorage(tmp_path / "suppress_test.db")
    email = ParsedEmail(
        message_id="promo-msg",
        thread_id="t-p",
        sender="promo@marketing.com",
        subject="Get up to 4x more profile views",
        received_at=datetime.now(timezone.utc),
        body="Promo body",
        body_source="plain",
    )
    storage.save_email(email)
    storage.save_analysis(
        "promo-msg",
        {
            "category": "other",
            "urgency": "low",
            "intent": "promotional",
            "importance": "low",
            "spam_status": "suspected_spam",
            "requires_action": False,
            "summary": "Suppressed promotional noise",
            "deadline": None,
        },
    )

    # 1. Not in important inbox
    important = storage.get_important_emails()
    assert not any(i["message_id"] == "promo-msg" for i in important)

    # 2. Present in suppressed queue
    suppressed = storage.get_suppressed_emails()
    assert any(s["message_id"] == "promo-msg" for s in suppressed)

    # 3. Not deleted - can be restored
    storage.restore_email("promo-msg")
    restored = storage.get_email("promo-msg")
    assert restored["spam_status"] == "not_spam"


def test_eval_duplicate_notification_prevention(tmp_path: Path):
    storage = EmailStorage(tmp_path / "notify_prevent.db")
    email = ParsedEmail(
        message_id="urgent-interview",
        thread_id="t-u",
        sender="recruiter@faang.com",
        subject="Final Round Technical Interview Tomorrow",
        received_at=datetime.now(timezone.utc),
        body="Interview tomorrow at 10 AM",
        body_source="plain",
    )
    storage.save_email(email)
    storage.save_analysis(
        "urgent-interview",
        {
            "category": "job",
            "intent": "interview_invitation",
            "importance": "critical",
            "urgency": "urgent",
            "spam_status": "not_spam",
            "requires_action": True,
            "deadline": "2026-10-15T10:00:00+00:00",
            "summary": "Final round interview tomorrow.",
        },
    )

    dispatched = []
    report1 = dispatch_notifications(storage=storage, notifier=lambda p: dispatched.append(p) or True)
    assert report1.delivered == 1
    assert len(dispatched) == 1

    # Second dispatch does not re-deliver
    report2 = dispatch_notifications(storage=storage, notifier=lambda p: dispatched.append(p) or True)
    assert report2.discovered == 0
    assert report2.delivered == 0
    assert len(dispatched) == 1


def test_eval_completed_hackerrank_interview_requires_no_candidate_action():
    """Verify that an interview/assessment confirmation email stating completion requires no candidate action."""
    received = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)
    client = FakeOllamaClient({
        "AI TA Screen": json.dumps({
            "category": "job",
            "intent": "interview_confirmation",
            "importance": "high",
            "urgency": "normal",
            "spam_status": "not_spam",
            "requires_action": True,
            "summary": "Completed AI TA screen for SDE Intern.",
            "deadline": None,
            "recommended_action": "Confirm interview slot or review preparation instructions.",
            "priority_reason": "Interview email.",
            "evidence": ["Your interview is complete"],
        })
    })

    analysis = analyze_email(
        subject="AI TA Screen - Software Development Engineer Intern - Bangalore (Onsite) at HackerRank - Your interview is complete",
        body="Thanks for completing AI TA Screen - Software Development Engineer Intern - Bangalore (Onsite). Your interview has been submitted to HackerRank. HackerRank will review your interview and contact you directly with any next steps.",
        client=client,
        received_at=received,
    )

    assert analysis.intent == "interview_confirmation"
    assert analysis.requires_action is False
    assert analysis.urgency == "low"
    assert analysis.importance == "high"
    assert "No action needed" in analysis.recommended_action or "await review" in analysis.recommended_action
    assert "completed" in analysis.priority_reason.lower()
