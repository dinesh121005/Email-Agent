from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from email_parser import ParsedEmail
from llm_client import OllamaClientError
from schemas import EmailAnalysis
from storage import EmailStorage
from worker import WorkerLockError, process_pending_emails


@pytest.fixture
def storage(tmp_path):
    return EmailStorage(tmp_path / "worker_test.db")


@pytest.fixture
def pending_email(storage):
    email = ParsedEmail(
        message_id="worker-msg-1",
        thread_id="thread-1",
        sender="organizer@example.com",
        subject="AI Hackathon Registration",
        received_at=datetime(2026, 10, 12, 4, 30, tzinfo=timezone.utc),
        body="Register by tomorrow at 5 PM.",
        body_source="plain",
    )
    storage.save_email(email)
    return email


def valid_analysis(**kwargs):
    return EmailAnalysis(
        category="hackathon",
        urgency="urgent",
        summary="Complete hackathon registration.",
        deadline="tomorrow at 5 PM",
        requires_action=True,
        **kwargs,
    )


def test_worker_completes_pending_email(storage, pending_email):
    calls = []

    def fake_analyzer(*, subject, body, received_at):
        calls.append((subject, body, received_at))
        return valid_analysis()

    report = process_pending_emails(
        storage=storage,
        analyzer=fake_analyzer,
    )

    assert report.discovered == 1
    assert report.completed == 1
    assert report.failed == 0
    assert report.skipped == 0
    assert len(calls) == 1

    saved = storage.get_email(pending_email.message_id)
    assert saved["status"] == "completed"
    assert saved["category"] == "hackathon"
    assert saved["urgency"] == "urgent"
    assert saved["requires_action"] == 1
    assert saved["attempt_count"] == 1


def test_worker_records_analyzer_failure(storage, pending_email):
    def broken_analyzer(*, subject, body, received_at):
        raise TimeoutError("Model inference timed out")

    report = process_pending_emails(
        storage=storage,
        analyzer=broken_analyzer,
    )

    assert report.failed == 1
    assert report.completed == 0

    saved = storage.get_email(pending_email.message_id)
    assert saved["status"] == "failed"
    assert saved["error"] == "TimeoutError: analysis failed"
    assert "Register by tomorrow" not in saved["error"]


def test_worker_rejects_unvalidated_result(storage, pending_email):
    def invalid_analyzer(*, subject, body, received_at):
        return {"category": "hackathon"}

    report = process_pending_emails(
        storage=storage,
        analyzer=invalid_analyzer,
    )

    assert report.failed == 1
    assert storage.get_email(pending_email.message_id)["status"] == "failed"


def test_completed_email_is_not_processed_again(storage, pending_email):
    calls = []

    def fake_analyzer(*, subject, body, received_at):
        calls.append(subject)
        return valid_analysis()

    process_pending_emails(storage=storage, analyzer=fake_analyzer)
    second = process_pending_emails(storage=storage, analyzer=fake_analyzer)

    assert len(calls) == 1
    assert second.discovered == 0
    assert second.completed == 0


def test_worker_respects_batch_limit(storage):
    for index in range(3):
        storage.save_email(
            ParsedEmail(
                message_id=f"msg-{index}",
                thread_id=f"thread-{index}",
                sender="jobs@example.com",
                subject=f"Job {index}",
                received_at=datetime(
                    2026, 10, 12, index, 0, tzinfo=timezone.utc
                ),
                body="Apply now.",
                body_source="plain",
            )
        )

    report = process_pending_emails(
        storage=storage,
        analyzer=lambda **kwargs: valid_analysis(),
        limit=2,
    )

    assert report.discovered == 2
    assert report.completed == 2
    assert len(storage.get_pending()) == 1


def test_worker_rejects_invalid_limit(storage):
    with pytest.raises(ValueError, match="limit"):
        process_pending_emails(storage=storage, limit=0)


def test_worker_handles_empty_queue(storage):
    report = process_pending_emails(
        storage=storage,
        analyzer=lambda **kwargs: valid_analysis(),
    )

    assert report.discovered == 0
    assert report.completed == 0
    assert report.failed == 0
    assert report.skipped == 0


def test_worker_prevents_concurrent_runs(storage, pending_email):
    # Acquire lock with another worker
    assert storage.acquire_lock(owner_id="other_worker") is True

    with pytest.raises(WorkerLockError, match="Another worker process"):
        process_pending_emails(
            storage=storage,
            analyzer=lambda **kwargs: valid_analysis(),
        )


def test_worker_recovers_stale_processing_before_batch(storage, pending_email):
    storage.mark_processing(pending_email.message_id)

    # Backdate the updated_at timestamp to 30 minutes ago
    old_time = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    with storage._connect() as conn:
        conn.execute("UPDATE emails SET updated_at = ? WHERE message_id = ?", (old_time, pending_email.message_id))

    # Worker should recover it from processing to pending and complete it
    report = process_pending_emails(
        storage=storage,
        analyzer=lambda **kwargs: valid_analysis(),
        recover_stale_minutes=15.0,
    )

    assert report.discovered == 1
    assert report.completed == 1
    assert storage.get_email(pending_email.message_id)["status"] == "completed"


def test_worker_retryable_ollama_failure_schedules_retry(storage, pending_email):
    def retryable_analyzer(**kwargs):
        raise OllamaClientError("Ollama returned HTTP 500", retryable=True)

    report = process_pending_emails(storage=storage, analyzer=retryable_analyzer)

    assert report.discovered == 1
    assert report.failed == 1
    assert report.completed == 0

    saved = storage.get_email(pending_email.message_id)
    assert saved["status"] == "failed"
    assert saved["attempt_count"] == 1
    assert saved["last_error_type"] == "OllamaClientError"
    assert saved["next_retry_at"] is not None


def test_worker_non_retryable_exception_does_not_schedule_retry(storage, pending_email):
    def non_retryable_analyzer(**kwargs):
        raise ValueError("Invalid parse structure")

    report = process_pending_emails(storage=storage, analyzer=non_retryable_analyzer)

    assert report.discovered == 1
    assert report.failed == 1
    assert report.completed == 0

    saved = storage.get_email(pending_email.message_id)
    assert saved["status"] == "failed"
    assert saved["attempt_count"] == 1
    assert saved["last_error_type"] == "ValueError"
    assert saved["next_retry_at"] is None


def test_worker_non_retryable_ollama_error_does_not_schedule_retry(storage, pending_email):
    def bad_request_analyzer(**kwargs):
        raise OllamaClientError("Ollama returned HTTP 400", retryable=False)

    report = process_pending_emails(storage=storage, analyzer=bad_request_analyzer)

    assert report.failed == 1
    saved = storage.get_email(pending_email.message_id)
    assert saved["status"] == "failed"
    assert saved["attempt_count"] == 1
    assert saved["last_error_type"] == "OllamaClientError"
    assert saved["next_retry_at"] is None


def test_worker_ignores_failed_email_with_future_retry(storage, pending_email):
    # Claim and fail with retryable=True (attempt 1 sets next_retry_at +30s)
    storage.mark_processing(pending_email.message_id, max_attempts=3)
    storage.mark_failed(
        pending_email.message_id,
        "Temporary timeout",
        error_type="OllamaClientError",
        retryable=True,
        max_attempts=3,
    )

    calls = []
    def fake_analyzer(**kwargs):
        calls.append(kwargs)
        return valid_analysis()

    report = process_pending_emails(storage=storage, analyzer=fake_analyzer)

    assert report.discovered == 0
    assert report.completed == 0
    assert report.failed == 0
    assert len(calls) == 0
    assert storage.get_email(pending_email.message_id)["status"] == "failed"


def test_worker_retries_due_failed_email(storage, pending_email):
    # Fail once and schedule retry
    storage.mark_processing(pending_email.message_id, max_attempts=3)
    storage.mark_failed(
        pending_email.message_id,
        "Temporary timeout",
        error_type="OllamaClientError",
        retryable=True,
        max_attempts=3,
    )

    # Backdate next_retry_at to past
    past_time = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    with storage._connect() as conn:
        conn.execute(
            "UPDATE emails SET next_retry_at = ? WHERE message_id = ?",
            (past_time, pending_email.message_id),
        )

    report = process_pending_emails(
        storage=storage,
        analyzer=lambda **kwargs: valid_analysis(),
    )

    assert report.discovered == 1
    assert report.completed == 1
    assert report.failed == 0

    saved = storage.get_email(pending_email.message_id)
    assert saved["status"] == "completed"
    assert saved["attempt_count"] == 2
    assert saved["next_retry_at"] is None
    assert saved["error"] is None


def test_worker_ignores_email_at_max_attempts(storage, pending_email):
    past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    with storage._connect() as conn:
        conn.execute("""
            UPDATE emails
            SET status = 'failed',
                attempt_count = 3,
                next_retry_at = ?,
                error = 'Terminal error'
            WHERE message_id = ?
        """, (past, pending_email.message_id))

    calls = []
    report = process_pending_emails(
        storage=storage,
        analyzer=lambda **kwargs: calls.append(kwargs) or valid_analysis(),
    )

    assert report.discovered == 0
    assert report.completed == 0
    assert len(calls) == 0
    assert storage.get_email(pending_email.message_id)["status"] == "failed"


def test_worker_releases_lock_on_failure(storage, pending_email):
    def crashing_analyzer(**kwargs):
        raise RuntimeError("Fatal worker error")

    report = process_pending_emails(
        storage=storage,
        analyzer=crashing_analyzer,
    )
    assert report.failed == 1

    # Lock must be released and re-acquirable
    assert storage.acquire_lock(owner_id="subsequent_worker") is True


def test_worker_releases_lock_on_unhandled_exception(storage):
    with patch.object(storage, "get_ready_for_processing", side_effect=RuntimeError("Database failure")):
        with pytest.raises(RuntimeError, match="Database failure"):
            process_pending_emails(storage=storage)

    # Lock must still be released
    assert storage.acquire_lock(owner_id="subsequent_worker") is True


def test_retry_due_while_offline_is_claimed_on_restart(storage, pending_email):
    # Email failed on attempt 1 with retryable=True before laptop turned off
    storage.mark_processing(pending_email.message_id)
    storage.mark_failed(
        pending_email.message_id,
        "OllamaClientError: Request timed out",
        error_type="OllamaClientError",
        retryable=True,
    )

    # Laptop was off for several hours; next_retry_at is now far in the past
    offline_retry_time = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    with storage._connect() as conn:
        conn.execute(
            "UPDATE emails SET next_retry_at = ? WHERE message_id = ?",
            (offline_retry_time, pending_email.message_id),
        )

    # Laptop powers on -> worker runs its first catch-up cycle
    report = process_pending_emails(
        storage=storage,
        analyzer=lambda **kwargs: valid_analysis(),
        limit=1,
    )

    assert report.discovered == 1
    assert report.completed == 1
    assert report.failed == 0

    record = storage.get_email(pending_email.message_id)
    assert record["status"] == "completed"
    assert record["attempt_count"] == 2
    assert record["next_retry_at"] is None


def test_worker_auto_acknowledges_expired_deadline(storage):
    # Email arrived yesterday with a deadline that expired yesterday
    yesterday = datetime.now(timezone.utc) - timedelta(days=2)
    expired_email = ParsedEmail(
        message_id="expired-msg-1",
        thread_id="thread-exp",
        sender="hr@company.com",
        subject="Submit Assignment by yesterday",
        received_at=yesterday,
        body="Deadline was yesterday.",
        body_source="plain",
    )
    storage.save_email(expired_email)

    expired_deadline_str = (yesterday + timedelta(hours=2)).isoformat()

    def expired_analyzer(*, subject, body, received_at):
        return EmailAnalysis(
            category="job",
            urgency="urgent",
            summary="Assignment deadline is expired.",
            deadline=expired_deadline_str,
            requires_action=True,
        )

    report = process_pending_emails(
        storage=storage,
        analyzer=expired_analyzer,
    )

    assert report.completed == 1
    record = storage.get_email("expired-msg-1")
    assert record["status"] == "completed"
    # Auto-marked as handled because deadline has passed
    assert record["acknowledged_at"] is not None


