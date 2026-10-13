"""Tests for the database-backed Personal AI Email Assistant chat service."""

from datetime import datetime, timezone
from pathlib import Path
import pytest

from chat import (
    answer_chat_query,
    build_chat_registry,
    format_context_records,
    plan_tool_execution,
)
from email_parser import ParsedEmail
from storage import EmailStorage


@pytest.fixture
def storage(tmp_path: Path) -> EmailStorage:
    db = EmailStorage(tmp_path / "chat_test.db")
    now = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)

    # 1. Genuine Interview
    email1 = ParsedEmail(
        message_id="msg-interview-1",
        thread_id="t-1",
        sender="recruiter@acme.com",
        subject="Interview Invitation: Software Engineer at Acme",
        received_at=now,
        body="We invite you to a technical interview this Friday at 2 PM.",
        body_source="plain",
    )
    db.save_email(email1)
    db.save_analysis(
        "msg-interview-1",
        {
            "category": "job",
            "urgency": "high",
            "intent": "interview_invitation",
            "importance": "high",
            "spam_status": "not_spam",
            "requires_action": True,
            "deadline": "2026-10-16T14:00:00+00:00",
            "summary": "Technical interview invitation for Software Engineer role.",
            "recommended_action": "Confirm availability for Friday 2 PM.",
            "priority_reason": "Verified interview invitation requiring candidate confirmation.",
            "evidence": ["interview this Friday at 2 PM"],
        },
    )

    # 2. Suppressed promotional email
    email2 = ParsedEmail(
        message_id="msg-promo-1",
        thread_id="t-2",
        sender="alerts@jobportal.com",
        subject="Get up to 4x more profile views",
        received_at=now,
        body="Boost your job search with our premium tracking service.",
        body_source="plain",
    )
    db.save_email(email2)
    db.save_analysis(
        "msg-promo-1",
        {
            "category": "other",
            "urgency": "low",
            "intent": "promotional",
            "importance": "low",
            "spam_status": "suspected_spam",
            "requires_action": False,
            "deadline": None,
            "summary": "Marketing promotion for profile views.",
            "recommended_action": "No action needed.",
            "priority_reason": "Promotional noise suppressed.",
            "evidence": ["4x more profile views"],
        },
    )

    # 3. Hackathon with open deadline
    email3 = ParsedEmail(
        message_id="msg-hack-1",
        thread_id="t-3",
        sender="team@hackai.org",
        subject="HackAI 2026 Registration Open",
        received_at=now,
        body="Registration closes in 24 hours.",
        body_source="plain",
    )
    db.save_email(email3)
    db.save_analysis(
        "msg-hack-1",
        {
            "category": "hackathon",
            "urgency": "urgent",
            "intent": "hackathon",
            "importance": "high",
            "spam_status": "not_spam",
            "requires_action": True,
            "deadline": "2026-10-15T10:00:00+00:00",
            "summary": "Registration deadline for HackAI 2026.",
            "recommended_action": "Complete team registration.",
            "priority_reason": "Hackathon registration deadline approaching.",
            "evidence": ["Registration closes in 24 hours"],
        },
    )

    return db


def test_plan_tool_execution_routing():
    assert plan_tool_execution("Show my upcoming interviews")[0] == "find_interviews"
    assert plan_tool_execution("What deadlines are approaching?")[0] == "find_upcoming_deadlines"
    assert plan_tool_execution("What do I need to do today?")[0] == "list_pending_actions"
    assert plan_tool_execution("Show the promotions you suppressed")[0] == "inspect_suppressed_promotions"
    assert plan_tool_execution("Find hackathons and competitions")[0] == "find_opportunities"
    assert plan_tool_execution("Give me a summary of my inbox")[0] == "get_daily_summary"
    assert plan_tool_execution("search for Acme")[0] == "search_emails"


def test_chat_registry_find_interviews(storage):
    registry = build_chat_registry(storage)
    interviews = registry.execute("find_interviews", limit=5)
    assert len(interviews) == 1
    assert interviews[0]["message_id"] == "msg-interview-1"
    assert "Acme" in interviews[0]["subject"]


def test_chat_registry_inspect_suppressed(storage):
    registry = build_chat_registry(storage)
    suppressed = registry.execute("inspect_suppressed_promotions", limit=5)
    assert len(suppressed) == 1
    assert suppressed[0]["message_id"] == "msg-promo-1"


def test_chat_registry_explain_classification(storage):
    registry = build_chat_registry(storage)
    explanation = registry.execute("explain_classification", message_id="msg-interview-1")
    assert explanation["intent"] == "interview_invitation"
    assert explanation["importance"] == "high"
    assert "interview this Friday at 2 PM" in explanation["evidence"]


def test_answer_chat_query_interviews(storage):
    res = answer_chat_query("Which interviews are coming up?", storage=storage)
    assert res["tool_used"] == "find_interviews"
    assert len(res["evidence"]) == 1
    assert "msg-interview-1" in res["reply"] or "Acme" in res["reply"]


def test_answer_chat_query_suppressed_promotions(storage):
    res = answer_chat_query("Which emails did you suppress as promotions?", storage=storage)
    assert res["tool_used"] == "inspect_suppressed_promotions"
    assert len(res["evidence"]) == 1
    assert "msg-promo-1" in res["reply"] or "profile views" in res["reply"]


def test_answer_chat_query_empty_database(tmp_path):
    empty_db = EmailStorage(tmp_path / "empty.db")
    res = answer_chat_query("What interviews are upcoming?", storage=empty_db)
    assert res["tool_used"] == "find_interviews"
    assert "No matching database records found" in res["reply"]
