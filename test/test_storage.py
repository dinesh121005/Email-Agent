from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import pytest

from email_parser import ParsedEmail
from storage import EmailStorage, StorageError


@pytest.fixture
def storage(tmp_path):
    return EmailStorage(tmp_path / "test_mail_agent.db")


@pytest.fixture
def email():
    return ParsedEmail(
        message_id="gmail-123",
        thread_id="thread-456",
        sender="jobs@example.com",
        subject="Data Science Internship",
        received_at=datetime(2026, 10, 12, 4, 30, tzinfo=timezone.utc),
        body="Please submit your application by Friday.",
        body_source="plain",
    )


@pytest.fixture
def analysis():
    return {
        "category": "job",
        "urgency": "high",
        "summary": "Submit an internship application.",
        "deadline": "2026-10-16T23:59:00+00:00",
        "requires_action": True,
    }


def test_saves_new_email(storage, email):
    assert storage.save_email(email) is True

    saved = storage.get_email(email.message_id)

    assert saved is not None
    assert saved["subject"] == email.subject
    assert saved["body"] == email.body
    assert saved["status"] == "pending"


def test_duplicate_message_id_is_not_inserted(storage, email):
    assert storage.save_email(email) is True
    assert storage.save_email(email) is False


def test_missing_email_returns_none(storage):
    assert storage.get_email("unknown-id") is None


def test_new_email_appears_in_pending_queue(storage, email):
    storage.save_email(email)

    pending = storage.get_pending()

    assert len(pending) == 1
    assert pending[0]["message_id"] == email.message_id


def test_processing_claim_only_succeeds_once(storage, email):
    storage.save_email(email)

    assert storage.mark_processing(email.message_id) is True
    assert storage.mark_processing(email.message_id) is False
    assert storage.get_email(email.message_id)["status"] == "processing"


def test_saves_analysis_and_completes_email(storage, email, analysis):
    storage.save_email(email)
    storage.save_analysis(email.message_id, analysis)

    saved = storage.get_email(email.message_id)

    assert saved["status"] == "completed"
    assert saved["category"] == "job"
    assert saved["urgency"] == "high"
    assert saved["requires_action"] == 1
    assert saved["error"] is None


def test_records_failure(storage, email):
    storage.save_email(email)
    assert storage.mark_processing(email.message_id) is True
    storage.mark_failed(email.message_id, "Temporary model timeout")

    saved = storage.get_email(email.message_id)

    assert saved["status"] == "failed"
    assert saved["error"] == "Temporary model timeout"


def test_analysis_requires_valid_fields(storage, email):
    storage.save_email(email)

    with pytest.raises(ValueError, match="required classification"):
        storage.save_analysis(email.message_id, {"category": "job"})


def test_saving_analysis_for_unknown_email_fails(storage, analysis):
    with pytest.raises(StorageError, match="not found"):
        storage.save_analysis("unknown-id", analysis)


def test_pending_limit_is_validated(storage):
    with pytest.raises(ValueError, match="limit"):
        storage.get_pending(limit=0)


def test_database_persists_across_instances(tmp_path, email):
    db_path = tmp_path / "persistent.db"

    first = EmailStorage(db_path)
    first.save_email(email)

    second = EmailStorage(db_path)

    assert second.get_email(email.message_id)["subject"] == email.subject


def test_recover_stale_processing_resets_old_claims_only(storage, email):
    storage.save_email(email)
    storage.mark_processing(email.message_id)

    # Manually backdate updated_at to 30 minutes ago
    old_time = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    with storage._connect() as conn:
        conn.execute("UPDATE emails SET updated_at = ? WHERE message_id = ?", (old_time, email.message_id))

    # Add a second email claimed just now (recent, not stale)
    recent_email = ParsedEmail(
        message_id="recent-1",
        thread_id="thread-2",
        sender="other@example.com",
        subject="Recent message",
        received_at=datetime.now(timezone.utc),
        body="Body",
        body_source="plain",
    )
    storage.save_email(recent_email)
    storage.mark_processing(recent_email.message_id)

    # Recovery with max_age_minutes=15 should recover only the old email
    recovered = storage.recover_stale_processing(max_age_minutes=15.0)
    assert recovered == 1

    assert storage.get_email(email.message_id)["status"] == "pending"
    assert storage.get_email(recent_email.message_id)["status"] == "processing"


def test_recover_stale_processing_never_affects_completed(storage, email, analysis):
    storage.save_email(email)
    storage.save_analysis(email.message_id, analysis)

    old_time = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
    with storage._connect() as conn:
        conn.execute("UPDATE emails SET updated_at = ? WHERE message_id = ?", (old_time, email.message_id))

    recovered = storage.recover_stale_processing(max_age_minutes=15.0)
    assert recovered == 0
    assert storage.get_email(email.message_id)["status"] == "completed"


def test_worker_lock_prevents_concurrent_acquisition(storage):
    assert storage.acquire_lock("worker", "worker-1", lease_seconds=60) is True
    # Second worker cannot acquire while held
    assert storage.acquire_lock("worker", "worker-2", lease_seconds=60) is False

    # Once released, worker-2 can acquire
    assert storage.release_lock("worker", "worker-1") is True
    assert storage.acquire_lock("worker", "worker-2", lease_seconds=60) is True


def test_worker_lock_expires_after_lease(storage):
    # Acquire lock with 0.001s lease
    assert storage.acquire_lock("worker", "worker-1", lease_seconds=0.001) is True

    # Manually backdate the lock's expiration
    expired_time = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    with storage._connect() as conn:
        conn.execute("UPDATE worker_locks SET expires_at = ? WHERE lock_name = 'worker'", (expired_time,))

    # Worker-2 should be able to acquire because previous lock expired
    assert storage.acquire_lock("worker", "worker-2", lease_seconds=60) is True


def test_fresh_database_contains_retry_columns(storage):
    with storage._connect() as conn:
        columns = [row["name"] for row in conn.execute("PRAGMA table_info(emails)").fetchall()]

    assert "attempt_count" in columns
    assert "next_retry_at" in columns
    assert "last_error_type" in columns


OLD_EMAILS_SCHEMA = """
    CREATE TABLE emails (
        message_id TEXT PRIMARY KEY,
        thread_id TEXT,
        sender TEXT NOT NULL DEFAULT '',
        subject TEXT NOT NULL DEFAULT '',
        received_at TEXT NOT NULL,
        body TEXT NOT NULL DEFAULT '',
        body_source TEXT NOT NULL DEFAULT 'empty',
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
        category TEXT,
        urgency TEXT,
        summary TEXT,
        deadline TEXT,
        requires_action INTEGER,
        error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
"""


def test_existing_database_is_migrated_and_preserves_records(tmp_path):
    db_path = tmp_path / "old_schema.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(OLD_EMAILS_SCHEMA)
        conn.execute("""
            INSERT INTO emails (
                message_id, thread_id, sender, subject,
                received_at, body, body_source, status,
                category, urgency, summary, deadline, requires_action,
                created_at, updated_at
            ) VALUES (
                'old-1', 'thread-1', 'user@example.com', 'Subject',
                '2026-10-12T00:00:00+00:00', 'Body', 'plain', 'completed',
                'job', 'urgent', 'Test summary', '2026-10-15T00:00:00+00:00', 1,
                '2026-10-12T00:00:00+00:00', '2026-10-12T00:00:00+00:00'
            )
        """)

    # Initializing EmailStorage should migrate the table without errors
    storage = EmailStorage(db_path)

    with storage._connect() as conn:
        columns = [row["name"] for row in conn.execute("PRAGMA table_info(emails)").fetchall()]
    assert "attempt_count" in columns
    assert "next_retry_at" in columns
    assert "last_error_type" in columns
    assert "notified_at" in columns
    assert "acknowledged_at" in columns

    # Verify existing record retains status, classification, and has default retry metadata
    record = storage.get_email("old-1")
    assert record is not None
    assert record["status"] == "completed"
    assert record["category"] == "job"
    assert record["urgency"] == "urgent"
    assert record["summary"] == "Test summary"
    assert record["deadline"] == "2026-10-15T00:00:00+00:00"
    assert record["requires_action"] == 1
    assert record["attempt_count"] == 0
    assert record["next_retry_at"] is None
    assert record["last_error_type"] is None
    assert record["notified_at"] is None
    assert record["acknowledged_at"] is None


def test_initialization_is_idempotent(tmp_path):
    db_path = tmp_path / "idempotent.db"
    # First initialization on fresh db
    storage1 = EmailStorage(db_path)
    # Second initialization on same db
    storage2 = EmailStorage(db_path)
    storage2._initialize()

    with storage2._connect() as conn:
        columns = [row["name"] for row in conn.execute("PRAGMA table_info(emails)").fetchall()]

    assert columns.count("attempt_count") == 1
    assert columns.count("next_retry_at") == 1
    assert columns.count("last_error_type") == 1
    assert columns.count("notified_at") == 1
    assert columns.count("acknowledged_at") == 1


def test_old_database_reinitialization_is_idempotent(tmp_path):
    db_path = tmp_path / "old_reinit.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(OLD_EMAILS_SCHEMA)

    storage = EmailStorage(db_path)
    storage._initialize()

    with storage._connect() as conn:
        columns = [row["name"] for row in conn.execute("PRAGMA table_info(emails)").fetchall()]

    assert columns.count("attempt_count") == 1
    assert columns.count("next_retry_at") == 1
    assert columns.count("last_error_type") == 1
    assert columns.count("notified_at") == 1
    assert columns.count("acknowledged_at") == 1


def test_pending_email_returned_by_get_ready_for_processing(storage, email):
    storage.save_email(email)
    ready = storage.get_ready_for_processing()
    assert len(ready) == 1
    assert ready[0]["message_id"] == email.message_id


def test_failed_email_excluded_before_retry_time(storage, email):
    storage.save_email(email)
    assert storage.mark_processing(email.message_id) is True
    # retryable=True on attempt 1 schedules next_retry_at 30s in the future
    storage.mark_failed(email.message_id, "Temporary network timeout", retryable=True)

    ready = storage.get_ready_for_processing()
    assert len(ready) == 0


def test_failed_email_eligible_after_retry_time(storage, email):
    storage.save_email(email)
    assert storage.mark_processing(email.message_id) is True
    storage.mark_failed(email.message_id, "Temporary network timeout", retryable=True)

    # Backdate next_retry_at to past
    past_time = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    with storage._connect() as conn:
        conn.execute(
            "UPDATE emails SET next_retry_at = ? WHERE message_id = ?",
            (past_time, email.message_id),
        )

    ready = storage.get_ready_for_processing()
    assert len(ready) == 1
    assert ready[0]["message_id"] == email.message_id


def test_mark_processing_increments_attempt_count_once(storage, email):
    storage.save_email(email)
    assert storage.get_email(email.message_id)["attempt_count"] == 0

    assert storage.mark_processing(email.message_id) is True
    assert storage.get_email(email.message_id)["attempt_count"] == 1

    # Second concurrent claim while processing returns False and does not increment
    assert storage.mark_processing(email.message_id) is False
    assert storage.get_email(email.message_id)["attempt_count"] == 1


def test_retry_cannot_be_claimed_before_next_retry_at(storage, email):
    storage.save_email(email)
    assert storage.mark_processing(email.message_id) is True
    storage.mark_failed(email.message_id, "Temporary timeout", retryable=True)

    # next_retry_at is in the future: cannot claim
    assert storage.mark_processing(email.message_id) is False

    # Backdate next_retry_at to past: can now claim
    past_time = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    with storage._connect() as conn:
        conn.execute(
            "UPDATE emails SET next_retry_at = ? WHERE message_id = ?",
            (past_time, email.message_id),
        )

    assert storage.mark_processing(email.message_id) is True
    updated = storage.get_email(email.message_id)
    assert updated["attempt_count"] == 2
    assert updated["status"] == "processing"
    assert updated["next_retry_at"] is None
    assert updated["error"] is None


def test_email_with_max_attempts_cannot_be_claimed(storage, email):
    storage.save_email(email)
    past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()

    # Attempt 1
    assert storage.mark_processing(email.message_id, max_attempts=3) is True
    storage.mark_failed(email.message_id, "Err 1", retryable=True, max_attempts=3)
    with storage._connect() as conn:
        conn.execute("UPDATE emails SET next_retry_at = ? WHERE message_id = ?", (past, email.message_id))

    # Attempt 2
    assert storage.mark_processing(email.message_id, max_attempts=3) is True
    storage.mark_failed(email.message_id, "Err 2", retryable=True, max_attempts=3)
    with storage._connect() as conn:
        conn.execute("UPDATE emails SET next_retry_at = ? WHERE message_id = ?", (past, email.message_id))

    # Attempt 3
    assert storage.mark_processing(email.message_id, max_attempts=3) is True
    # At attempt 3, attempt < max_attempts is false, so next_retry_at remains None
    storage.mark_failed(email.message_id, "Err 3", retryable=True, max_attempts=3)

    record = storage.get_email(email.message_id)
    assert record["attempt_count"] == 3
    assert record["next_retry_at"] is None

    # Even if next_retry_at were somehow set, attempt_count < max_attempts prevents claim
    with storage._connect() as conn:
        conn.execute("UPDATE emails SET next_retry_at = ? WHERE message_id = ?", (past, email.message_id))
    assert storage.mark_processing(email.message_id, max_attempts=3) is False
    assert len(storage.get_ready_for_processing(max_attempts=3)) == 0


def test_permanent_failure_leaves_next_retry_at_null(storage, email):
    storage.save_email(email)
    assert storage.mark_processing(email.message_id) is True
    storage.mark_failed(
        email.message_id,
        "Unrecoverable parse error",
        error_type="ParseError",
        retryable=False,
    )

    record = storage.get_email(email.message_id)
    assert record["status"] == "failed"
    assert record["error"] == "Unrecoverable parse error"
    assert record["last_error_type"] == "ParseError"
    assert record["next_retry_at"] is None


def test_completed_emails_cannot_be_claimed_or_marked_failed(storage, email, analysis):
    storage.save_email(email)
    storage.save_analysis(email.message_id, analysis)

    assert storage.mark_processing(email.message_id) is False
    with pytest.raises(StorageError, match="not processing"):
        storage.mark_failed(email.message_id, "Some failure")


def test_mark_failed_requires_processing_status(storage, email):
    storage.save_email(email)
    with pytest.raises(StorageError, match="not processing"):
        storage.mark_failed(email.message_id, "Premature failure")


def test_fresh_database_has_sync_checkpoints_table(storage):
    with storage._connect() as conn:
        tables = [
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
    assert "sync_checkpoints" in tables


def test_missing_checkpoint_returns_none(storage):
    assert storage.get_checkpoint("non_existent") is None


def test_checkpoint_can_be_inserted_and_retrieved(storage):
    dt = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc)
    storage.set_checkpoint("gmail_sync", dt)
    assert storage.get_checkpoint("gmail_sync") == "2026-10-12T10:00:00+00:00"


def test_updating_checkpoint_replaces_timestamp_without_duplicates(storage):
    dt1 = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc)
    dt2 = datetime(2026, 10, 12, 11, 0, tzinfo=timezone.utc)

    storage.set_checkpoint("gmail_sync", dt1)
    storage.set_checkpoint("gmail_sync", dt2)

    assert storage.get_checkpoint("gmail_sync") == "2026-10-12T11:00:00+00:00"

    with storage._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sync_checkpoints WHERE checkpoint_name = 'gmail_sync'"
        ).fetchall()
    assert len(rows) == 1


def test_checkpoint_non_utc_offset_normalized_to_utc(storage):
    ist = timezone(timedelta(hours=5, minutes=30))
    dt_ist = datetime(2026, 10, 12, 15, 30, tzinfo=ist)

    storage.set_checkpoint("gmail_sync", dt_ist)
    assert storage.get_checkpoint("gmail_sync") == "2026-10-12T10:00:00+00:00"

    storage.set_checkpoint("gmail_sync", "2026-10-12T15:30:00+05:30")
    assert storage.get_checkpoint("gmail_sync") == "2026-10-12T10:00:00+00:00"


def test_checkpoint_rejects_naive_and_invalid_timestamps(storage):
    with pytest.raises(ValueError, match="timezone-aware"):
        storage.set_checkpoint("gmail_sync", datetime(2026, 10, 12, 10, 0))

    with pytest.raises(ValueError, match="valid ISO-8601"):
        storage.set_checkpoint("gmail_sync", "not-a-timestamp")

    with pytest.raises(TypeError, match="datetime, ISO-8601 string, or None"):
        storage.set_checkpoint("gmail_sync", 12345)

    with pytest.raises(ValueError, match="checkpoint_name"):
        storage.set_checkpoint("   ", datetime.now(timezone.utc))


def test_reinitializing_storage_preserves_checkpoints(tmp_path):
    db_path = tmp_path / "checkpoint_preserve.db"
    first = EmailStorage(db_path)
    dt = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    first.set_checkpoint("gmail_sync", dt)

    second = EmailStorage(db_path)
    assert second.get_checkpoint("gmail_sync") == "2026-10-12T12:00:00+00:00"


def test_checkpoint_records_and_retrieves_account_email(storage):
    assert storage.get_checkpoint_account("gmail_sync") is None
    dt = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc)
    storage.set_checkpoint("gmail_sync", dt, account_email="personal@gmail.com")
    assert storage.get_checkpoint("gmail_sync") == "2026-10-12T10:00:00+00:00"
    assert storage.get_checkpoint_account("gmail_sync") == "personal@gmail.com"


def test_storage_backup_to_creates_atomic_copy(tmp_path, email):
    src_path = tmp_path / "source.db"
    backup_path = tmp_path / "backup.db"
    store = EmailStorage(src_path)
    store.save_email(email)

    store.backup_to(backup_path)
    assert backup_path.exists()

    backup_store = EmailStorage(backup_path)
    assert backup_store.get_email(email.message_id) is not None


def test_storage_honors_mail_agent_db_env(monkeypatch, tmp_path):
    env_db = tmp_path / "custom_env.db"
    monkeypatch.setenv("MAIL_AGENT_DB", str(env_db))
    store = EmailStorage()
    assert store.db_path == env_db



def test_interrupted_analysis_recovery_after_power_loss(storage, email):
    # Worker 1 claims email before machine powers off
    storage.save_email(email)
    assert storage.acquire_lock("email_worker", owner_id="worker_before_shutdown", lease_seconds=900) is True
    assert storage.mark_processing(email.message_id) is True
    assert storage.get_email(email.message_id)["status"] == "processing"

    # Simulate 2 hours of power loss:
    # 1. Backdate the worker lock expiration to 1 hour ago (expired)
    # 2. Backdate the email updated_at timestamp to 2 hours ago
    past_1h = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    past_2h = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    with storage._connect() as conn:
        conn.execute(
            "UPDATE worker_locks SET expires_at = ? WHERE lock_name = 'email_worker'",
            (past_1h,),
        )
        conn.execute(
            "UPDATE emails SET updated_at = ? WHERE message_id = ?",
            (past_2h, email.message_id),
        )

    # Machine reboots -> new worker starts up
    # 1. New worker successfully acquires lock (expired lock was removed)
    assert storage.acquire_lock("email_worker", owner_id="worker_after_reboot", lease_seconds=900) is True

    # 2. Recovery routine resets orphaned claim back to pending
    recovered = storage.recover_stale_processing(max_age_minutes=15.0)
    assert recovered == 1

    # 3. Email is now pending and can be claimed by the rebooted worker
    assert storage.get_email(email.message_id)["status"] == "pending"
    assert storage.mark_processing(email.message_id) is True
    assert storage.get_email(email.message_id)["status"] == "processing"


def test_fresh_database_contains_notification_columns(storage):
    with storage._connect() as conn:
        columns = [
            row["name"]
            for row in conn.execute("PRAGMA table_info(emails)").fetchall()
        ]
    assert "notified_at" in columns
    assert "acknowledged_at" in columns


def test_mark_notified_sets_timestamp_and_returns_true(storage, email):
    storage.save_email(email)
    assert storage.get_email(email.message_id)["notified_at"] is None

    result = storage.mark_notified(email.message_id)
    assert result is True

    record = storage.get_email(email.message_id)
    assert record["notified_at"] is not None
    # Verify ISO-8601 format
    parsed = datetime.fromisoformat(record["notified_at"])
    assert parsed.tzinfo is not None


def test_mark_notified_unknown_email_returns_false(storage):
    assert storage.mark_notified("unknown-id") is False


def test_mark_notified_normalizes_timezone_aware_datetime(storage, email):
    storage.save_email(email)
    ist = timezone(timedelta(hours=5, minutes=30))
    dt_ist = datetime(2026, 10, 12, 18, 0, tzinfo=ist)

    assert storage.mark_notified(email.message_id, dt_ist) is True
    record = storage.get_email(email.message_id)
    assert record["notified_at"] == "2026-10-12T12:30:00+00:00"


def test_mark_acknowledged_and_alias(storage, email):
    storage.save_email(email)
    assert storage.get_email(email.message_id)["acknowledged_at"] is None

    # Test mark_acknowledged
    assert storage.mark_acknowledged(email.message_id) is True
    record = storage.get_email(email.message_id)
    assert record["acknowledged_at"] is not None

    # Test acknowledge alias returns True and updates
    dt = datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc)
    assert storage.acknowledge(email.message_id, dt) is True
    assert (
        storage.get_email(email.message_id)["acknowledged_at"]
        == "2026-10-12T14:00:00+00:00"
    )


def test_mark_acknowledged_unknown_email_returns_false(storage):
    assert storage.mark_acknowledged("unknown-id") is False
    assert storage.acknowledge("unknown-id") is False


def test_notification_methods_validate_arguments(storage, email):
    storage.save_email(email)

    with pytest.raises(ValueError, match="limit"):
        storage.get_unnotified_important(limit=0)

    with pytest.raises(ValueError, match="limit"):
        storage.get_unnotified_important(limit=501)

    with pytest.raises(ValueError, match="limit"):
        storage.get_unacknowledged_important(limit=0)

    with pytest.raises(ValueError, match="limit"):
        storage.get_important_emails(limit=0)

    with pytest.raises(ValueError, match="timezone-aware"):
        storage.mark_notified(
            email.message_id,
            datetime(2026, 10, 12, 10, 0),
        )

    with pytest.raises(ValueError, match="valid ISO-8601"):
        storage.mark_acknowledged(email.message_id, "invalid-timestamp")

    with pytest.raises(TypeError, match="datetime, ISO-8601 string, or None"):
        storage.mark_notified(email.message_id, 12345)


def _create_and_classify(
    storage,
    msg_id,
    *,
    category="other",
    urgency="normal",
    requires_action=False,
    status="completed",
    notified=False,
    acknowledged=False,
):
    email = ParsedEmail(
        message_id=msg_id,
        thread_id=f"thread-{msg_id}",
        sender="sender@example.com",
        subject=f"Subject {msg_id}",
        received_at=datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc),
        body="Body",
        body_source="plain",
    )
    storage.save_email(email)

    if status == "completed":
        storage.save_analysis(
            msg_id,
            {
                "category": category,
                "urgency": urgency,
                "summary": f"Summary {msg_id}",
                "deadline": None,
                "requires_action": requires_action,
            },
        )
    elif status == "failed":
        storage.mark_processing(msg_id)
        storage.mark_failed(msg_id, "Some failure")

    if notified:
        storage.mark_notified(msg_id)
    if acknowledged:
        storage.mark_acknowledged(msg_id)


def test_get_unnotified_important_filters_correctly(storage):
    # 1. Actionable normal email -> eligible
    _create_and_classify(storage, "msg-1", urgency="normal", requires_action=True)
    # 2. Urgent non-actionable email -> eligible
    _create_and_classify(storage, "msg-2", urgency="urgent", requires_action=False)
    # 3. High urgency actionable email -> eligible
    _create_and_classify(storage, "msg-3", urgency="high", requires_action=True)
    # 4. Job opportunity with normal urgency, no required action -> eligible
    _create_and_classify(storage, "msg-job", category="job", urgency="normal", requires_action=False)
    # 5. Hackathon with low urgency, no required action -> eligible
    _create_and_classify(storage, "msg-hack", category="hackathon", urgency="low", requires_action=False)
    # 6. Competition with normal urgency, no required action -> eligible
    _create_and_classify(storage, "msg-comp", category="competition", urgency="normal", requires_action=False)
    # 7. Normal non-actionable other email -> excluded
    _create_and_classify(storage, "msg-4", category="other", urgency="normal", requires_action=False)
    # 8. Low urgency non-actionable other email -> excluded
    _create_and_classify(storage, "msg-5", category="other", urgency="low", requires_action=False)
    # 9. Urgent actionable email but pending -> excluded
    _create_and_classify(
        storage, "msg-6", urgency="urgent", requires_action=True, status="pending"
    )
    # 10. Urgent actionable email but failed -> excluded
    _create_and_classify(
        storage, "msg-7", urgency="urgent", requires_action=True, status="failed"
    )
    # 11. Urgent actionable email already notified -> excluded from unnotified
    _create_and_classify(
        storage, "msg-8", urgency="urgent", requires_action=True, notified=True
    )
    # 12. Urgent actionable email already acknowledged -> excluded from unnotified
    _create_and_classify(
        storage, "msg-9", urgency="urgent", requires_action=True, acknowledged=True
    )

    unnotified = storage.get_unnotified_important()
    ids = [item["message_id"] for item in unnotified]

    assert set(ids) == {"msg-1", "msg-2", "msg-3", "msg-job", "msg-hack", "msg-comp"}


def test_get_unacknowledged_important_retains_items_after_notification(storage):
    _create_and_classify(storage, "msg-attn", urgency="urgent", requires_action=True)

    # Initially in unacknowledged inbox
    unack = storage.get_unacknowledged_important()
    assert len(unack) == 1
    assert unack[0]["message_id"] == "msg-attn"

    # Notification delivered (e.g. desktop toast)
    assert storage.mark_notified("msg-attn") is True

    # No longer in unnotified
    assert len(storage.get_unnotified_important()) == 0

    # Still retained in unacknowledged inbox
    unack_after_notify = storage.get_unacknowledged_important()
    assert len(unack_after_notify) == 1
    assert unack_after_notify[0]["message_id"] == "msg-attn"
    assert unack_after_notify[0]["notified_at"] is not None
    assert unack_after_notify[0]["acknowledged_at"] is None

    # User acknowledges
    assert storage.mark_acknowledged("msg-attn") is True

    # Now removed from unacknowledged inbox
    assert len(storage.get_unacknowledged_important()) == 0


def test_get_important_emails_unacknowledged_flag(storage):
    _create_and_classify(
        storage, "msg-active", urgency="high", requires_action=True
    )
    _create_and_classify(
        storage, "msg-done", urgency="urgent", requires_action=True, acknowledged=True
    )

    unack_only = storage.get_important_emails(unacknowledged_only=True)
    assert [m["message_id"] for m in unack_only] == ["msg-active"]

    all_important = storage.get_important_emails(unacknowledged_only=False)
    all_ids = {m["message_id"] for m in all_important}
    assert all_ids == {"msg-active", "msg-done"}


def test_notification_state_persists_across_restart(tmp_path):
    db_path = tmp_path / "notify_restart.db"

    # Session 1: Receive, analyze, notify
    first = EmailStorage(db_path)
    _create_and_classify(
        first, "restart-1", urgency="urgent", requires_action=True
    )
    first.mark_notified("restart-1")

    # Session 2: Laptop turns off and restarts -> new EmailStorage instance
    second = EmailStorage(db_path)
    record = second.get_email("restart-1")
    assert record["notified_at"] is not None
    assert record["acknowledged_at"] is None

    # Does not re-trigger unnotified query
    assert len(second.get_unnotified_important()) == 0

    # Still visible in unacknowledged inbox
    inbox = second.get_unacknowledged_important()
    assert len(inbox) == 1
    assert inbox[0]["message_id"] == "restart-1"

    # User acknowledges in session 2
    second.acknowledge("restart-1")

    # Session 3: Another restart
    third = EmailStorage(db_path)
    assert len(third.get_unacknowledged_important()) == 0
    final_record = third.get_email("restart-1")
    assert final_record["acknowledged_at"] is not None





