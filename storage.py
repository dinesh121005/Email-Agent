"""Local SQLite persistence for the Gmail mail agent."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

from email_parser import ParsedEmail


class StorageError(RuntimeError):
    """Raised when a storage operation fails."""


class EmailStorage:
    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            db_path = os.environ.get("MAIL_AGENT_DB", "mail_agent.db")
        self.db_path = Path(db_path)
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        if str(self.db_path) != ":memory:":
            try:
                connection.execute("PRAGMA journal_mode = WAL")
            except sqlite3.Error:
                pass
            connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    def _initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS emails (
                        message_id TEXT PRIMARY KEY,
                        thread_id TEXT,
                        sender TEXT NOT NULL DEFAULT '',
                        subject TEXT NOT NULL DEFAULT '',
                        received_at TEXT NOT NULL,
                        body TEXT NOT NULL DEFAULT '',
                        body_source TEXT NOT NULL DEFAULT 'empty',

                        status TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN (
                                'pending', 'processing', 'completed', 'failed'
                            )),

                        category TEXT,
                        urgency TEXT,
                        summary TEXT,
                        deadline TEXT,
                        requires_action INTEGER,

                        error TEXT,
                        attempt_count INTEGER NOT NULL DEFAULT 0
                            CHECK (attempt_count >= 0),
                        next_retry_at TEXT,
                        last_error_type TEXT,
                        notified_at TEXT,
                        acknowledged_at TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                # Migrate databases created by earlier versions.
                existing_columns = {
                    row["name"]
                    for row in connection.execute(
                        "PRAGMA table_info(emails)"
                    ).fetchall()
                }
                migrations = {
                    "attempt_count": (
                        "ALTER TABLE emails ADD COLUMN "
                        "attempt_count INTEGER NOT NULL DEFAULT 0 "
                        "CHECK (attempt_count >= 0)"
                    ),
                    "next_retry_at": (
                        "ALTER TABLE emails ADD COLUMN next_retry_at TEXT"
                    ),
                    "last_error_type": (
                        "ALTER TABLE emails ADD COLUMN last_error_type TEXT"
                    ),
                    "notified_at": (
                        "ALTER TABLE emails ADD COLUMN notified_at TEXT"
                    ),
                    "acknowledged_at": (
                        "ALTER TABLE emails ADD COLUMN acknowledged_at TEXT"
                    ),
                    "intent": (
                        "ALTER TABLE emails ADD COLUMN intent TEXT"
                    ),
                    "importance": (
                        "ALTER TABLE emails ADD COLUMN importance TEXT"
                    ),
                    "spam_status": (
                        "ALTER TABLE emails ADD COLUMN spam_status TEXT NOT NULL DEFAULT 'not_spam'"
                    ),
                    "priority_reason": (
                        "ALTER TABLE emails ADD COLUMN priority_reason TEXT"
                    ),
                    "recommended_action": (
                        "ALTER TABLE emails ADD COLUMN recommended_action TEXT"
                    ),
                    "evidence": (
                        "ALTER TABLE emails ADD COLUMN evidence TEXT"
                    ),
                    "analysis_version": (
                        "ALTER TABLE emails ADD COLUMN analysis_version TEXT DEFAULT '2.0.0'"
                    ),
                    "attachments": (
                        "ALTER TABLE emails ADD COLUMN attachments TEXT DEFAULT '[]'"
                    ),
                    "notification_count": (
                        "ALTER TABLE emails ADD COLUMN notification_count INTEGER NOT NULL DEFAULT 0"
                    ),
                    "last_notification_reason": (
                        "ALTER TABLE emails ADD COLUMN last_notification_reason TEXT"
                    ),
                    "gmail_read_sync_status": (
                        "ALTER TABLE emails ADD COLUMN gmail_read_sync_status TEXT DEFAULT 'pending'"
                    ),
                    "gmail_read_sync_attempted_at": (
                        "ALTER TABLE emails ADD COLUMN gmail_read_sync_attempted_at TEXT"
                    ),
                    "gmail_read_sync_error_type": (
                        "ALTER TABLE emails ADD COLUMN gmail_read_sync_error_type TEXT"
                    ),
                    "gmail_read_sync_retry_count": (
                        "ALTER TABLE emails ADD COLUMN gmail_read_sync_retry_count INTEGER NOT NULL DEFAULT 0"
                    ),
                }
                for column_name, statement in migrations.items():
                    if column_name not in existing_columns:
                        connection.execute(statement)

                connection.execute("""
                    CREATE TABLE IF NOT EXISTS app_settings (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)

                connection.execute("""
                    CREATE INDEX IF NOT EXISTS idx_emails_status_received
                    ON emails(status, received_at)
                """)
                connection.execute("""
                    CREATE INDEX IF NOT EXISTS idx_emails_notifications
                    ON emails(status, requires_action, urgency, category, acknowledged_at)
                """)
                connection.execute("""
                    CREATE INDEX IF NOT EXISTS idx_emails_spam_status
                    ON emails(spam_status, status)
                """)
                connection.execute("""
                    CREATE INDEX IF NOT EXISTS idx_emails_importance
                    ON emails(importance, status)
                """)
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS worker_locks (
                        lock_name TEXT PRIMARY KEY,
                        owner_id TEXT NOT NULL,
                        acquired_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL
                    )
                """)
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS sync_checkpoints (
                        checkpoint_name TEXT PRIMARY KEY,
                        last_synced_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        account_email TEXT
                    )
                """)
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS service_status (
                        service_name TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        last_sync_at TEXT,
                        next_sync_at TEXT,
                        last_error TEXT,
                        last_error_at TEXT,
                        heartbeat_at TEXT,
                        details TEXT,
                        updated_at TEXT NOT NULL
                    )
                """)
                existing_checkpoint_columns = {
                    row["name"]
                    for row in connection.execute(
                        "PRAGMA table_info(sync_checkpoints)"
                    ).fetchall()
                }
                if "account_email" not in existing_checkpoint_columns:
                    connection.execute(
                        "ALTER TABLE sync_checkpoints ADD COLUMN account_email TEXT"
                    )
        except sqlite3.Error as exc:
            raise StorageError("Could not initialize the email database.") from exc

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _normalize_timestamp(
        value: str | datetime | None,
        param_name: str = "timestamp",
    ) -> str:
        """Validate and normalize timestamps into ISO-8601 UTC strings."""
        if value is None:
            return datetime.now(timezone.utc).isoformat()
        if isinstance(value, str):
            try:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(
                    f"{param_name} must be a valid ISO-8601 timestamp"
                ) from exc
        elif isinstance(value, datetime):
            dt = value
        else:
            raise TypeError(
                f"{param_name} must be a datetime, ISO-8601 string, or None"
            )

        if dt.tzinfo is None or dt.utcoffset() is None:
            raise ValueError(f"{param_name} must be timezone-aware")

        return dt.astimezone(timezone.utc).isoformat()

    def save_email(self, email: ParsedEmail) -> bool:
        """Insert a new email; return False if its Gmail ID already exists."""
        now = self._now()
        attachments_json = "[]"
        if hasattr(email, "attachments") and email.attachments:
            try:
                attachments_json = json.dumps([
                    a if isinstance(a, dict) else (a.model_dump() if hasattr(a, "model_dump") else dict(a))
                    for a in email.attachments
                ])
            except Exception:
                attachments_json = "[]"

        try:
            with self._connect() as connection:
                cols = {
                    r["name"]
                    for r in connection.execute("PRAGMA table_info(emails)").fetchall()
                }
                if "attachments" in cols:
                    cursor = connection.execute("""
                        INSERT OR IGNORE INTO emails (
                            message_id, thread_id, sender, subject,
                            received_at, body, body_source, attachments,
                            status, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                    """, (
                        email.message_id,
                        email.thread_id,
                        email.sender,
                        email.subject,
                        email.received_at.isoformat(),
                        email.body,
                        email.body_source,
                        attachments_json,
                        now,
                        now,
                    ))
                else:
                    cursor = connection.execute("""
                        INSERT OR IGNORE INTO emails (
                            message_id, thread_id, sender, subject,
                            received_at, body, body_source,
                            status, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                    """, (
                        email.message_id,
                        email.thread_id,
                        email.sender,
                        email.subject,
                        email.received_at.isoformat(),
                        email.body,
                        email.body_source,
                        now,
                        now,
                    ))
                return cursor.rowcount == 1
        except sqlite3.Error as exc:
            raise StorageError("Could not save the email.") from exc

    def get_email(self, message_id: str) -> dict[str, Any] | None:
        """Return a stored email as a dictionary, or None if not found."""
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM emails WHERE message_id = ?",
                    (message_id,),
                ).fetchone()
                return dict(row) if row else None
        except sqlite3.Error as exc:
            raise StorageError("Could not retrieve the email.") from exc

    def get_thread_emails(self, thread_id: str) -> list[dict[str, Any]]:
        """Return all emails belonging to a thread, ordered chronologically."""
        clean_id = str(thread_id).strip()
        if not clean_id:
            return []
        try:
            with self._connect() as connection:
                rows = connection.execute("""
                    SELECT * FROM emails
                    WHERE thread_id = ?
                    ORDER BY received_at ASC, message_id ASC
                """, (clean_id,)).fetchall()
                return [dict(row) for row in rows]
        except sqlite3.Error as exc:
            raise StorageError("Could not retrieve thread emails.") from exc

    def get_pending(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return pending emails in received-time order."""
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")

        try:
            with self._connect() as connection:
                rows = connection.execute("""
                    SELECT * FROM emails
                    WHERE status = 'pending'
                    ORDER BY received_at ASC, message_id ASC
                    LIMIT ?
                """, (limit,)).fetchall()
                return [dict(row) for row in rows]
        except sqlite3.Error as exc:
            raise StorageError("Could not retrieve pending emails.") from exc

    def get_ready_for_processing(
        self,
        limit: int = 20,
        max_attempts: int = 3,
    ) -> list[dict[str, Any]]:
        """Return pending emails and failed emails whose retry time has arrived."""
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")

        now = self._now()
        try:
            with self._connect() as connection:
                rows = connection.execute("""
                    SELECT *
                    FROM emails
                    WHERE (status = 'pending' AND attempt_count < ?)
                       OR (
                            status = 'failed'
                            AND attempt_count < ?
                            AND next_retry_at IS NOT NULL
                            AND next_retry_at <= ?
                       )
                    ORDER BY received_at ASC, message_id ASC
                    LIMIT ?
                """, (max_attempts, max_attempts, now, limit)).fetchall()
                return [dict(row) for row in rows]
        except sqlite3.Error as exc:
            raise StorageError(
                "Could not retrieve emails ready for processing."
            ) from exc

    def mark_processing(
        self,
        message_id: str,
        max_attempts: int = 3,
    ) -> bool:
        """Claim a new email or a retry that is due; increment its attempt count."""
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")

        now = self._now()
        try:
            with self._connect() as connection:
                cursor = connection.execute("""
                    UPDATE emails
                    SET status = 'processing',
                        attempt_count = attempt_count + 1,
                        next_retry_at = NULL,
                        error = NULL,
                        last_error_type = NULL,
                        updated_at = ?
                    WHERE message_id = ?
                      AND attempt_count < ?
                      AND (
                          status = 'pending'
                          OR (
                              status = 'failed'
                              AND next_retry_at IS NOT NULL
                              AND next_retry_at <= ?
                          )
                      )
                """, (now, message_id, max_attempts, now))
                return cursor.rowcount == 1
        except sqlite3.Error as exc:
            raise StorageError(
                "Could not mark email as processing."
            ) from exc

    def save_analysis(self, message_id: str, analysis: Any) -> None:
        """Persist a validated EmailAnalysis and mark the email completed."""
        category = getattr(analysis, "category", None)
        urgency = getattr(analysis, "urgency", None)
        summary = getattr(analysis, "summary", None)
        deadline = getattr(analysis, "deadline", None)
        requires_action = getattr(analysis, "requires_action", None)
        intent = getattr(analysis, "intent", None)
        importance = getattr(analysis, "importance", None)
        spam_status = getattr(analysis, "spam_status", None)
        priority_reason = getattr(analysis, "priority_reason", None)
        recommended_action = getattr(analysis, "recommended_action", None)
        evidence = getattr(analysis, "evidence", None)
        analysis_version = getattr(analysis, "analysis_version", None)

        if isinstance(analysis, dict):
            category = analysis.get("category")
            urgency = analysis.get("urgency")
            summary = analysis.get("summary")
            deadline = analysis.get("deadline")
            requires_action = analysis.get("requires_action")
            intent = analysis.get("intent")
            importance = analysis.get("importance")
            spam_status = analysis.get("spam_status")
            priority_reason = analysis.get("priority_reason")
            recommended_action = analysis.get("recommended_action")
            evidence = analysis.get("evidence")
            analysis_version = analysis.get("analysis_version")

        if (
            category is None
            or urgency is None
            or summary is None
            or not isinstance(requires_action, bool)
        ):
            raise ValueError("analysis is missing required classification fields")

        # Fallbacks for backwards compatibility
        if not intent:
            intent = category if category in ("hackathon", "competition", "uncertain") else ("job_opportunity" if category == "job" else "other")
        if not importance:
            importance = "high" if urgency in ("urgent", "high") else "normal"
        if not spam_status:
            spam_status = "not_spam"
        if not analysis_version:
            analysis_version = "2.0.0"

        evidence_str = (
            json.dumps(evidence) if isinstance(evidence, (list, dict)) else (str(evidence) if evidence else "[]")
        )

        try:
            with self._connect() as connection:
                cursor = connection.execute("""
                    UPDATE emails
                    SET category = ?,
                        urgency = ?,
                        summary = ?,
                        deadline = ?,
                        requires_action = ?,
                        intent = ?,
                        importance = ?,
                        spam_status = ?,
                        priority_reason = ?,
                        recommended_action = ?,
                        evidence = ?,
                        analysis_version = ?,
                        status = 'completed',
                        error = NULL,
                        updated_at = ?
                    WHERE message_id = ?
                """, (
                    category,
                    urgency,
                    summary,
                    deadline,
                    int(requires_action),
                    intent,
                    importance,
                    spam_status,
                    priority_reason,
                    recommended_action,
                    evidence_str,
                    analysis_version,
                    self._now(),
                    message_id,
                ))

                if cursor.rowcount != 1:
                    raise StorageError(
                        f"Cannot save analysis: message {message_id!r} not found."
                    )
        except sqlite3.Error as exc:
            raise StorageError("Could not save email analysis.") from exc

    def mark_failed(
        self,
        message_id: str,
        error: str,
        *,
        error_type: str | None = None,
        retryable: bool = False,
        max_attempts: int = 3,
    ) -> None:
        """Record failure and schedule a retry only when policy permits it."""
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")

        safe_error = error[:500]
        safe_error_type = (
            error_type[:100] if error_type else None
        )
        now = datetime.now(timezone.utc)
        retry_at = None

        try:
            with self._connect() as connection:
                row = connection.execute("""
                    SELECT attempt_count, status
                    FROM emails
                    WHERE message_id = ?
                """, (message_id,)).fetchone()

                if row is None:
                    raise StorageError("Cannot record failure: email not found.")

                if row["status"] != "processing":
                    raise StorageError(
                        "Cannot record failure: email is not processing."
                    )

                attempt = row["attempt_count"]
                if retryable and attempt < max_attempts:
                    delay_seconds = 30 if attempt == 1 else 120
                    retry_at = (
                        now + timedelta(seconds=delay_seconds)
                    ).isoformat()

                connection.execute("""
                    UPDATE emails
                    SET status = 'failed',
                        error = ?,
                        last_error_type = ?,
                        next_retry_at = ?,
                        updated_at = ?
                    WHERE message_id = ?
                      AND status = 'processing'
                """, (
                    safe_error,
                    safe_error_type,
                    retry_at,
                    now.isoformat(),
                    message_id,
                ))
        except sqlite3.Error as exc:
            raise StorageError("Could not record email failure.") from exc

    def recover_stale_processing(self, max_age_minutes: float = 15.0) -> int:
        """Reset claims stuck in 'processing' longer than max_age_minutes back to 'pending'.

        Returns the number of recovered records.
        """
        if max_age_minutes <= 0:
            raise ValueError("max_age_minutes must be positive")

        now = datetime.now(timezone.utc)
        threshold = (now - timedelta(minutes=max_age_minutes)).isoformat()

        try:
            with self._connect() as connection:
                cursor = connection.execute("""
                    UPDATE emails
                    SET status = 'pending',
                        updated_at = ?
                    WHERE status = 'processing' AND updated_at < ?
                """, (now.isoformat(), threshold))
                return cursor.rowcount
        except sqlite3.Error as exc:
            raise StorageError("Could not recover stale processing records.") from exc

    def acquire_lock(
        self,
        lock_name: str = "email_worker",
        owner_id: str = "default",
        lease_seconds: float = 900.0,
    ) -> bool:
        """Acquire a named lock with an expiration lease.

        Returns True if acquired, False if already actively held by another owner.
        """
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")

        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(seconds=lease_seconds)).isoformat()
        now_str = now.isoformat()

        try:
            with self._connect() as connection:
                # Remove expired locks
                connection.execute("""
                    DELETE FROM worker_locks
                    WHERE lock_name = ? AND expires_at < ?
                """, (lock_name, now_str))

                cursor = connection.execute("""
                    INSERT OR IGNORE INTO worker_locks (
                        lock_name, owner_id, acquired_at, expires_at
                    )
                    VALUES (?, ?, ?, ?)
                """, (lock_name, owner_id, now_str, expires_at))
                return cursor.rowcount == 1
        except sqlite3.Error as exc:
            raise StorageError("Could not acquire worker lock.") from exc

    def release_lock(
        self,
        lock_name: str = "email_worker",
        owner_id: str = "default",
    ) -> bool:
        """Release a held lock by owner."""
        try:
            with self._connect() as connection:
                cursor = connection.execute("""
                    DELETE FROM worker_locks
                    WHERE lock_name = ? AND owner_id = ?
                """, (lock_name, owner_id))
                return cursor.rowcount == 1
        except sqlite3.Error as exc:
            raise StorageError("Could not release worker lock.") from exc

    def get_lock(self, lock_name: str = "email_worker") -> dict[str, Any] | None:
        """Inspect the current active state of a named worker lock."""
        now_str = datetime.now(timezone.utc).isoformat()
        try:
            with self._connect() as connection:
                row = connection.execute("""
                    SELECT lock_name, owner_id, acquired_at, expires_at
                    FROM worker_locks
                    WHERE lock_name = ? AND expires_at >= ?
                """, (lock_name, now_str)).fetchone()
                return dict(row) if row else None
        except sqlite3.Error as exc:
            raise StorageError("Could not inspect worker lock.") from exc

    def backup_to(self, target_path: str | Path) -> None:
        """Create a consistent SQLite backup, flushing any active transactions."""
        dest_path = Path(target_path)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as src, sqlite3.connect(dest_path) as dst:
            src.backup(dst)

    def get_checkpoint(
        self,
        checkpoint_name: str = "gmail_sync",
    ) -> str | None:
        """Return the stored synchronization timestamp, if present."""
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT last_synced_at
                    FROM sync_checkpoints
                    WHERE checkpoint_name = ?
                    """,
                    (checkpoint_name,),
                ).fetchone()
                return row["last_synced_at"] if row else None
        except sqlite3.Error as exc:
            raise StorageError(
                "Could not retrieve synchronization checkpoint."
            ) from exc

    def get_checkpoint_account(
        self,
        checkpoint_name: str = "gmail_sync",
    ) -> str | None:
        """Return the stored synchronization account email, if present."""
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT account_email
                    FROM sync_checkpoints
                    WHERE checkpoint_name = ?
                    """,
                    (checkpoint_name,),
                ).fetchone()
                return row["account_email"] if row else None
        except sqlite3.Error as exc:
            raise StorageError(
                "Could not retrieve synchronization checkpoint account."
            ) from exc

    def set_checkpoint(
        self,
        checkpoint_name: str = "gmail_sync",
        synced_at: str | datetime | None = None,
        account_email: str | None = None,
    ) -> None:
        """Insert or update a synchronization checkpoint."""
        if not checkpoint_name.strip():
            raise ValueError("checkpoint_name must not be empty")

        timestamp = self._normalize_timestamp(synced_at, "synced_at")
        now = self._now()

        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO sync_checkpoints (
                        checkpoint_name, last_synced_at, updated_at, account_email
                    )
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(checkpoint_name) DO UPDATE SET
                        last_synced_at = excluded.last_synced_at,
                        updated_at = excluded.updated_at,
                        account_email = COALESCE(excluded.account_email, sync_checkpoints.account_email)
                    """,
                    (checkpoint_name, timestamp, now, account_email),
                )
        except sqlite3.Error as exc:
            raise StorageError(
                "Could not save synchronization checkpoint."
            ) from exc

    def get_unnotified_important(
        self,
        limit: int = 50,
        include_escalation: bool = False,
        escalation_hours: float = 24.0,
        max_escalations: int = 2,
    ) -> list[dict[str, Any]]:
        """Return completed important emails that have not yet been notified, or eligible for escalation."""
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")

        now = datetime.now(timezone.utc)
        escalation_threshold = (now - timedelta(hours=escalation_hours)).isoformat()

        try:
            with self._connect() as connection:
                cols = {
                    r["name"]
                    for r in connection.execute("PRAGMA table_info(emails)").fetchall()
                }
                has_escalation_cols = "notification_count" in cols

                if not include_escalation or not has_escalation_cols:
                    rows = connection.execute("""
                        SELECT *
                        FROM emails
                        WHERE status = 'completed'
                          AND (spam_status = 'not_spam' OR spam_status IS NULL)
                          AND (
                              requires_action = 1
                              OR urgency IN ('urgent', 'high')
                              OR importance IN ('critical', 'high')
                              OR category IN ('job', 'hackathon', 'competition')
                          )
                          AND notified_at IS NULL
                          AND acknowledged_at IS NULL
                        ORDER BY received_at ASC, message_id ASC
                        LIMIT ?
                    """, (limit,)).fetchall()
                else:
                    rows = connection.execute("""
                        SELECT *
                        FROM emails
                        WHERE status = 'completed'
                          AND (spam_status = 'not_spam' OR spam_status IS NULL)
                          AND (
                              requires_action = 1
                              OR urgency IN ('urgent', 'high')
                              OR importance IN ('critical', 'high')
                              OR category IN ('job', 'hackathon', 'competition')
                          )
                          AND acknowledged_at IS NULL
                          AND (
                              notified_at IS NULL
                              OR (
                                  (urgency = 'urgent' OR importance = 'critical')
                                  AND notification_count < ?
                                  AND notified_at <= ?
                              )
                          )
                        ORDER BY received_at ASC, message_id ASC
                        LIMIT ?
                    """, (max_escalations, escalation_threshold, limit)).fetchall()
                return [dict(row) for row in rows]
        except sqlite3.Error as exc:
            raise StorageError(
                "Could not retrieve unnotified important emails."
            ) from exc

    def get_unacknowledged_important(
        self,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return completed important emails awaiting user acknowledgment (newest first)."""
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")

        try:
            with self._connect() as connection:
                rows = connection.execute("""
                    SELECT *
                    FROM emails
                    WHERE status = 'completed'
                      AND (spam_status = 'not_spam' OR spam_status IS NULL)
                      AND (
                          requires_action = 1
                          OR urgency IN ('urgent', 'high')
                          OR importance IN ('critical', 'high')
                          OR category IN ('job', 'hackathon', 'competition')
                      )
                      AND acknowledged_at IS NULL
                    ORDER BY received_at DESC, message_id ASC
                    LIMIT ?
                """, (limit,)).fetchall()
                return [dict(row) for row in rows]
        except sqlite3.Error as exc:
            raise StorageError(
                "Could not retrieve unacknowledged important emails."
            ) from exc

    def get_important_emails(
        self,
        *,
        unacknowledged_only: bool = True,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return completed important emails, optionally filtering for unacknowledged only."""
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")

        query = """
            SELECT *
            FROM emails
            WHERE status = 'completed'
              AND (spam_status = 'not_spam' OR spam_status IS NULL)
              AND (
                  requires_action = 1
                  OR urgency IN ('urgent', 'high')
                  OR importance IN ('critical', 'high')
                  OR category IN ('job', 'hackathon', 'competition')
              )
        """
        if unacknowledged_only:
            query += " AND acknowledged_at IS NULL"
        query += " ORDER BY received_at DESC, message_id ASC LIMIT ?"

        try:
            with self._connect() as connection:
                rows = connection.execute(query, (limit,)).fetchall()
                return [dict(row) for row in rows]
        except sqlite3.Error as exc:
            raise StorageError("Could not retrieve important emails.") from exc

    def mark_notified(
        self,
        message_id: str,
        notified_at: str | datetime | None = None,
        reason: str | None = None,
    ) -> bool:
        """Record when a notification was delivered for an email."""
        timestamp = self._normalize_timestamp(notified_at, "notified_at")
        now = self._now()

        try:
            with self._connect() as connection:
                cols = {
                    r["name"]
                    for r in connection.execute("PRAGMA table_info(emails)").fetchall()
                }
                if "notification_count" in cols:
                    cursor = connection.execute("""
                        UPDATE emails
                        SET notified_at = ?,
                            notification_count = COALESCE(notification_count, 0) + 1,
                            last_notification_reason = COALESCE(?, last_notification_reason),
                            updated_at = ?
                        WHERE message_id = ?
                    """, (timestamp, reason, now, message_id))
                else:
                    cursor = connection.execute("""
                        UPDATE emails
                        SET notified_at = ?,
                            updated_at = ?
                        WHERE message_id = ?
                    """, (timestamp, now, message_id))
                return cursor.rowcount == 1
        except sqlite3.Error as exc:
            raise StorageError("Could not mark email as notified.") from exc

    def record_service_status(
        self,
        service_name: str = "background_monitor",
        status: str = "running",
        last_sync_at: str | datetime | None = None,
        next_sync_at: str | datetime | None = None,
        last_error: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Upsert current operational service status, heartbeat, and error records."""
        now = self._now()
        last_sync_str = (
            self._normalize_timestamp(last_sync_at, "last_sync_at")
            if last_sync_at else None
        )
        next_sync_str = (
            self._normalize_timestamp(next_sync_at, "next_sync_at")
            if next_sync_at else None
        )
        details_json = json.dumps(details) if details is not None else None
        last_err_at = now if last_error else None

        try:
            with self._connect() as connection:
                connection.execute("""
                    INSERT INTO service_status (
                        service_name, status, last_sync_at, next_sync_at,
                        last_error, last_error_at, heartbeat_at, details, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(service_name) DO UPDATE SET
                        status = excluded.status,
                        last_sync_at = COALESCE(excluded.last_sync_at, service_status.last_sync_at),
                        next_sync_at = COALESCE(excluded.next_sync_at, service_status.next_sync_at),
                        last_error = CASE
                            WHEN excluded.status = 'idle' AND excluded.last_error IS NULL THEN NULL
                            WHEN excluded.last_error IS NOT NULL THEN excluded.last_error
                            ELSE service_status.last_error
                        END,
                        last_error_at = CASE
                            WHEN excluded.status = 'idle' AND excluded.last_error IS NULL THEN NULL
                            WHEN excluded.last_error IS NOT NULL THEN excluded.last_error_at
                            ELSE service_status.last_error_at
                        END,
                        heartbeat_at = excluded.heartbeat_at,
                        details = COALESCE(excluded.details, service_status.details),
                        updated_at = excluded.updated_at
                """, (
                    service_name,
                    status,
                    last_sync_str,
                    next_sync_str,
                    last_error,
                    last_err_at,
                    now,
                    details_json,
                    now,
                ))
        except sqlite3.Error as exc:
            raise StorageError("Could not record service status.") from exc

    def get_service_status(
        self,
        service_name: str = "background_monitor",
    ) -> dict[str, Any] | None:
        """Retrieve operational service status record."""
        try:
            with self._connect() as connection:
                row = connection.execute("""
                    SELECT * FROM service_status WHERE service_name = ?
                """, (service_name,)).fetchone()
                if not row:
                    return None
                data = dict(row)
                if data.get("details"):
                    try:
                        data["details"] = json.loads(data["details"])
                    except Exception:
                        pass
                return data
        except sqlite3.Error as exc:
            raise StorageError("Could not retrieve service status.") from exc

    def record_heartbeat(
        self,
        service_name: str = "background_monitor",
    ) -> None:
        """Update worker heartbeat timestamp."""
        now = self._now()
        try:
            with self._connect() as connection:
                connection.execute("""
                    UPDATE service_status
                    SET heartbeat_at = ?, updated_at = ?
                    WHERE service_name = ?
                """, (now, now, service_name))
        except sqlite3.Error as exc:
            raise StorageError("Could not record service heartbeat.") from exc

    def mark_acknowledged(
        self,
        message_id: str,
        acknowledged_at: str | datetime | None = None,
    ) -> bool:
        """Mark an important email as acknowledged / handled by the user."""
        timestamp = self._normalize_timestamp(acknowledged_at, "acknowledged_at")
        now = self._now()

        try:
            with self._connect() as connection:
                cursor = connection.execute("""
                    UPDATE emails
                    SET acknowledged_at = ?,
                        updated_at = ?
                    WHERE message_id = ?
                """, (timestamp, now, message_id))
                return cursor.rowcount == 1
        except sqlite3.Error as exc:
            raise StorageError("Could not mark email as acknowledged.") from exc

    def acknowledge(
        self,
        message_id: str,
        acknowledged_at: str | datetime | None = None,
    ) -> bool:
        """Convenience alias for mark_acknowledged."""
        return self.mark_acknowledged(message_id, acknowledged_at)

    def mark_spam(self, message_id: str, status: str = "confirmed_spam") -> bool:
        """Mark an email as spam/suppressed."""
        if status not in ("confirmed_spam", "suspected_spam", "not_spam"):
            raise ValueError(f"Invalid spam status: {status}")
        now = self._now()
        try:
            with self._connect() as connection:
                cursor = connection.execute("""
                    UPDATE emails
                    SET spam_status = ?,
                        updated_at = ?
                    WHERE message_id = ?
                """, (status, now, message_id))
                return cursor.rowcount == 1
        except sqlite3.Error as exc:
            raise StorageError("Could not update email spam status.") from exc

    def restore_email(self, message_id: str) -> bool:
        """Restore a previously suppressed or suspected email to normal inbox status."""
        now = self._now()
        try:
            with self._connect() as connection:
                cursor = connection.execute("""
                    UPDATE emails
                    SET spam_status = 'not_spam',
                        updated_at = ?
                    WHERE message_id = ?
                """, (now, message_id))
                return cursor.rowcount == 1
        except sqlite3.Error as exc:
            raise StorageError("Could not restore email.") from exc

    def reset_for_reanalysis(self, message_id: str) -> bool:
        """Reset a completed or failed email to pending for safe re-analysis."""
        now = self._now()
        try:
            with self._connect() as connection:
                cursor = connection.execute("""
                    UPDATE emails
                    SET status = 'pending',
                        error = NULL,
                        attempt_count = 0,
                        next_retry_at = NULL,
                        updated_at = ?
                    WHERE message_id = ?
                """, (now, message_id))
                return cursor.rowcount == 1
        except sqlite3.Error as exc:
            raise StorageError("Could not reset email for re-analysis.") from exc

    def get_suppressed_emails(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return emails suppressed as promotional or spam candidates."""
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        try:
            with self._connect() as connection:
                rows = connection.execute("""
                    SELECT *
                    FROM emails
                    WHERE spam_status IN ('suspected_spam', 'confirmed_spam')
                       OR intent = 'promotional'
                    ORDER BY received_at DESC, message_id ASC
                    LIMIT ?
                """, (limit,)).fetchall()
                return [dict(row) for row in rows]
        except sqlite3.Error as exc:
            raise StorageError("Could not retrieve suppressed emails.") from exc

    def get_review_emails(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return ambiguous emails needing human review."""
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        try:
            with self._connect() as connection:
                rows = connection.execute("""
                    SELECT *
                    FROM emails
                    WHERE status = 'completed'
                      AND (intent = 'uncertain' OR spam_status = 'suspected_spam')
                    ORDER BY received_at DESC, message_id ASC
                    LIMIT ?
                """, (limit,)).fetchall()
                return [dict(row) for row in rows]
        except sqlite3.Error as exc:
            raise StorageError("Could not retrieve review queue emails.") from exc

    def search_emails(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Search emails by query across subject, sender, summary, and body."""
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        q = f"%{query.strip()}%"
        try:
            with self._connect() as connection:
                rows = connection.execute("""
                    SELECT *
                    FROM emails
                    WHERE subject LIKE ?
                       OR sender LIKE ?
                       OR summary LIKE ?
                       OR body LIKE ?
                    ORDER BY received_at DESC
                    LIMIT ?
                """, (q, q, q, q, limit)).fetchall()
                return [dict(row) for row in rows]
        except sqlite3.Error as exc:
            raise StorageError("Could not search emails.") from exc

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        """Retrieve a persisted configuration setting."""
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT value FROM app_settings WHERE key = ?",
                    (key.strip(),),
                ).fetchone()
                return row["value"] if row else default
        except sqlite3.Error:
            return default

    def set_setting(self, key: str, value: str) -> None:
        """Persist or update a configuration setting."""
        now = self._now()
        try:
            with self._connect() as connection:
                connection.execute("""
                    INSERT INTO app_settings (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                """, (key.strip(), str(value), now))
        except sqlite3.Error as exc:
            raise StorageError(f"Could not persist setting {key!r}.") from exc

    def get_pending_read_sync(
        self,
        limit: int = 50,
        max_retries: int = 3,
    ) -> list[dict[str, Any]]:
        """Retrieve completed emails eligible for Gmail read-state synchronization."""
        safe_limit = max(1, min(limit, 500))
        try:
            with self._connect() as connection:
                rows = connection.execute("""
                    SELECT message_id, thread_id, subject, sender, status,
                           gmail_read_sync_status, gmail_read_sync_retry_count
                    FROM emails
                    WHERE status = 'completed'
                      AND (
                          gmail_read_sync_status IS NULL
                          OR gmail_read_sync_status IN ('pending', 'failed')
                      )
                      AND (
                          gmail_read_sync_retry_count IS NULL
                          OR gmail_read_sync_retry_count < ?
                      )
                    ORDER BY received_at DESC
                    LIMIT ?
                """, (max_retries, safe_limit)).fetchall()
                return [dict(r) for r in rows]
        except sqlite3.Error as exc:
            raise StorageError("Could not retrieve pending read-sync emails.") from exc

    def mark_read_sync_completed(self, message_id: str) -> None:
        """Record successful Gmail read-state synchronization."""
        now = self._now()
        try:
            with self._connect() as connection:
                connection.execute("""
                    UPDATE emails
                    SET gmail_read_sync_status = 'completed',
                        gmail_read_sync_attempted_at = ?,
                        gmail_read_sync_error_type = NULL,
                        updated_at = ?
                    WHERE message_id = ?
                """, (now, now, message_id))
        except sqlite3.Error as exc:
            raise StorageError(f"Could not mark read-sync completed for {message_id!r}.") from exc

    def mark_read_sync_failed(self, message_id: str, error_type: str) -> None:
        """Record failed Gmail read-state synchronization with bounded retry tracking."""
        now = self._now()
        try:
            with self._connect() as connection:
                connection.execute("""
                    UPDATE emails
                    SET gmail_read_sync_status = 'failed',
                        gmail_read_sync_attempted_at = ?,
                        gmail_read_sync_error_type = ?,
                        gmail_read_sync_retry_count = COALESCE(gmail_read_sync_retry_count, 0) + 1,
                        updated_at = ?
                    WHERE message_id = ?
                """, (now, error_type, now, message_id))
        except sqlite3.Error as exc:
            raise StorageError(f"Could not record read-sync failure for {message_id!r}.") from exc

    def mark_read_sync_disabled(self, message_id: str) -> None:
        """Record that Gmail read-state synchronization is disabled for this email."""
        now = self._now()
        try:
            with self._connect() as connection:
                connection.execute("""
                    UPDATE emails
                    SET gmail_read_sync_status = 'disabled',
                        updated_at = ?
                    WHERE message_id = ?
                """, (now, message_id))
        except sqlite3.Error as exc:
            raise StorageError(f"Could not record read-sync disabled for {message_id!r}.") from exc



