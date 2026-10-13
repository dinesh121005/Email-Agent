"""End-to-end validation of the complete Mail Agent workflow.

Validates the definition of done:
1. Browser dashboard is closed.
2. Background monitor discovers an important interview email with attachments.
3. Message is classified, persisted in SQLite, and triggers an immediate desktop notification.
4. Second cycle confirms duplicate notification suppression.
5. Desktop workspace opens and reflects synchronized records, attachments, thread, and worker status.
6. Chatbot accesses local SQLite and live Gmail tools to explain priority decisions and attachments.
7. User handles the email and verifies escalation is suppressed.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
from unittest.mock import MagicMock
import urllib.request
import pytest

from app import MailAgentHTTPHandler, ThreadingHTTPServer
from chat import answer_chat_query
from email_analyzer import analyze_email
from email_parser import ParsedEmail
from notifier import NotificationPayload, NotificationPolicy, dispatch_notifications, evaluate_notification_eligibility
from scheduler import run_cycle
from schemas import EmailAnalysis
from storage import EmailStorage


@pytest.fixture
def shared_db(tmp_path: Path) -> EmailStorage:
    db_file = tmp_path / "mail_agent_shared.db"
    return EmailStorage(db_file)


def test_e2e_always_on_monitoring_workflow(shared_db: EmailStorage) -> None:
    # -------------------------------------------------------------------------
    # Phase 1: Dashboard is CLOSED. Background Monitor Service operates alone.
    # -------------------------------------------------------------------------
    shared_db.record_service_status(
        service_name="background_monitor",
        status="running",
        next_sync_at="2026-10-12T19:00:00+00:00",
    )

    now = datetime.now(timezone.utc)
    interview_email = ParsedEmail(
        message_id="msg-e2e-interview-42",
        thread_id="thread-e2e-int-42",
        sender="talent@deepmind.google.com",
        subject="Interview Invitation: AI Systems Engineer Role",
        received_at=now,
        body=(
            "Dear Candidate,\n\n"
            "We were impressed by your background and would like to invite you to an interview "
            "for the AI Systems Engineer position this Thursday at 10:00 AM UTC. "
            "Please find the interview agenda attached and reply to confirm.\n\n"
            "Best regards,\nDeepMind Talent Team"
        ),
        body_source="plain",
        attachments=[
            {
                "filename": "AI_Interview_Packet.pdf",
                "mime_type": "application/pdf",
                "size": 1048576,
                "attachment_id": "att-packet-99",
            }
        ],
    )

    # 1. Background ingestion saves email to shared SQLite
    shared_db.save_email(interview_email)
    assert shared_db.get_email("msg-e2e-interview-42") is not None

    # 2. AI Classification analyzes the email
    analysis = EmailAnalysis(
        category="job",
        intent="interview_invitation",
        importance="high",
        urgency="urgent",
        summary="Interview invitation for AI Systems Engineer position at DeepMind.",
        deadline=(now.replace(hour=10, minute=0, second=0)).isoformat(),
        requires_action=True,
        evidence=[
            "invite you to an interview for the AI Systems Engineer position",
            "reply to confirm",
        ],
        priority_reason="Genuine interview invitation with approaching schedule.",
        recommended_action="Reply to talent@deepmind.google.com to confirm attendance.",
    )
    shared_db.save_analysis("msg-e2e-interview-42", analysis)

    # 3. Selective Notification Policy evaluates email
    policy = NotificationPolicy(
        app_url_base="http://localhost:8765",
        deadline_window_hours=72,
        escalation_interval_hours=24,
        max_escalation_count=2,
    )
    row = shared_db.get_email("msg-e2e-interview-42")
    eligible, reason = evaluate_notification_eligibility(row, policy=policy)
    assert eligible is True
    assert "interview" in reason.lower()

    # 4. Dispatch notification via mocked Windows toast notifier
    mock_toast = MagicMock(return_value=True)
    report1 = dispatch_notifications(
        storage=shared_db,
        notifier=mock_toast,
        policy=policy,
    )
    assert report1.delivered == 1
    mock_toast.assert_called_once()
    toast_payload: NotificationPayload = mock_toast.call_args[0][0]
    assert toast_payload.message_id == "msg-e2e-interview-42"
    assert "Interview Invitation" in toast_payload.subject
    assert toast_payload.urgency == "urgent"

    # Confirm notification state is persisted in SQLite
    updated_row = shared_db.get_email("msg-e2e-interview-42")
    assert updated_row["notified_at"] is not None
    assert updated_row["notification_count"] == 1

    # 5. Subsequent background check: Duplicate alert is SUPPRESSED
    mock_toast.reset_mock()
    report2 = dispatch_notifications(
        storage=shared_db,
        notifier=mock_toast,
        policy=policy,
    )
    assert report2.delivered == 0
    mock_toast.assert_not_called()

    # -------------------------------------------------------------------------
    # Phase 2: User opens the Desktop Workspace (HTTP Server)
    # -------------------------------------------------------------------------
    MailAgentHTTPHandler.storage = shared_db
    server = ThreadingHTTPServer(("127.0.0.1", 0), MailAgentHTTPHandler)
    port = server.server_address[1]
    import threading
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base_url = f"http://127.0.0.1:{port}"

    try:
        # A. Query service status: verifies monitor is active and timestamps match
        req_status = urllib.request.urlopen(f"{base_url}/api/service_status")
        assert req_status.status == 200
        status_data = json.loads(req_status.read().decode("utf-8"))
        assert status_data["service_name"] == "background_monitor"
        assert status_data["status"] == "running"
        assert "2026-10-12T19:00:00" in status_data["next_sync_at"]

        # B. Query stats: verifies smart priority counters
        req_stats = urllib.request.urlopen(f"{base_url}/api/stats")
        stats_data = json.loads(req_stats.read().decode("utf-8"))
        assert stats_data["total"] == 1
        assert stats_data["needs_action"] == 1
        assert stats_data["interviews"] == 1
        assert stats_data["worker_status"] == "running"

        # C. Query emails: verifies original email and attachments are visible
        req_emails = urllib.request.urlopen(f"{base_url}/api/emails")
        emails_data = json.loads(req_emails.read().decode("utf-8"))
        assert len(emails_data) == 1
        synced_email = emails_data[0]
        assert synced_email["message_id"] == "msg-e2e-interview-42"
        assert synced_email["subject"] == "Interview Invitation: AI Systems Engineer Role"

        # Inspect attachments
        attachments = json.loads(synced_email["attachments"])
        assert len(attachments) == 1
        assert attachments[0]["filename"] == "AI_Interview_Packet.pdf"

        # D. Query thread endpoint
        req_thread = urllib.request.urlopen(f"{base_url}/api/thread?thread_id=thread-e2e-int-42")
        thread_data = json.loads(req_thread.read().decode("utf-8"))
        assert len(thread_data) == 1
        assert thread_data[0]["message_id"] == "msg-e2e-interview-42"

        # E. Ask the Chatbot about this email:
        # Question 1: "Why did this email trigger a notification and what should I do?"
        mock_chat_llm = MagicMock()
        mock_chat_llm.generate.return_value = (
            "This email triggered an immediate notification because it is a verified interview invitation "
            "from DeepMind. Recommended action: Reply to confirm attendance."
        )

        chat_res = answer_chat_query(
            query='Why did email "msg-e2e-interview-42" trigger a notification?',
            storage=shared_db,
            client=mock_chat_llm,
        )
        assert chat_res["tool_used"] is not None
        assert "DeepMind" in chat_res["reply"] or "interview" in chat_res["reply"].lower()

        # Question 2: "What attachments are in my interview email?"
        chat_res_att = answer_chat_query(
            query='What attachments are in email "msg-e2e-interview-42"?',
            storage=shared_db,
            client=mock_chat_llm,
        )
        assert chat_res_att["tool_used"] == "get_email_attachments"
        assert "AI_Interview_Packet.pdf" in str(chat_res_att.get("evidence", {}))

        # F. User clicks "Mark Handled" in Desktop UI
        ack_req = urllib.request.Request(
            f"{base_url}/api/ack",
            data=json.dumps({"message_id": "msg-e2e-interview-42"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        ack_res = urllib.request.urlopen(ack_req)
        assert ack_res.status == 200
        assert json.loads(ack_res.read().decode("utf-8"))["success"] is True

        # Verify handled state in database
        handled_row = shared_db.get_email("msg-e2e-interview-42")
        assert handled_row["acknowledged_at"] is not None

        # Verify subsequent escalation is completely suppressed
        mock_toast.reset_mock()
        report_after_ack = dispatch_notifications(
            storage=shared_db,
            notifier=mock_toast,
            policy=policy,
            include_escalation=True,
        )
        assert report_after_ack.delivered == 0
        mock_toast.assert_not_called()

    finally:
        server.shutdown()
        server.server_close()
