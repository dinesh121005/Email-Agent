"""Process pending emails using the existing local email analyzer."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from email_analyzer import analyze_email
from llm_client import OllamaClientError
from schemas import EmailAnalysis
from storage import EmailStorage

MAX_ATTEMPTS = 3


class WorkerLockError(RuntimeError):
    """Raised when worker lock cannot be acquired because another worker is running."""


@dataclass(frozen=True)
class WorkerReport:
    discovered: int
    completed: int
    failed: int
    skipped: int


Analyzer = Callable[..., EmailAnalysis]


def process_pending_emails(
    *,
    storage: EmailStorage | None = None,
    analyzer: Analyzer | None = None,
    limit: int = 5,
    recover_stale_minutes: float = 15.0,
    worker_id: str = "default_worker",
) -> WorkerReport:
    """Analyze pending emails and persist their results."""
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")

    database = storage or EmailStorage()
    analyze = analyzer or analyze_email

    # Protect against concurrent worker processes
    if not database.acquire_lock(owner_id=worker_id):
        raise WorkerLockError("Another worker process is currently active")

    try:
        # Recover stale processing claims before picking up the batch
        database.recover_stale_processing(max_age_minutes=recover_stale_minutes)

        pending = database.get_ready_for_processing(
            limit=limit,
            max_attempts=MAX_ATTEMPTS,
        )

        completed = 0
        failed = 0
        skipped = 0

        for email in pending:
            message_id = email["message_id"]

            # Claim only if this message is still pending.
            # This helps avoid duplicate work if another worker claimed it first.
            if not database.mark_processing(
                message_id,
                max_attempts=MAX_ATTEMPTS,
            ):
                skipped += 1
                continue

            try:
                received_at = datetime.fromisoformat(email["received_at"])

                if received_at.tzinfo is None:
                    raise ValueError("Stored received_at must be timezone-aware")

                analysis = analyze(
                    subject=email["subject"],
                    body=email["body"],
                    received_at=received_at,
                )

                # Save only validated EmailAnalysis results.
                if not isinstance(analysis, EmailAnalysis):
                    raise TypeError(
                        "Analyzer must return a validated EmailAnalysis object"
                    )

                database.save_analysis(message_id, analysis)

                # If the email's deadline has already passed, automatically mark it as Handled
                # and queue it for Gmail read sync so it does not clutter the user's actionable queue.
                try:
                    if analysis.deadline:
                        from deadline_normalizer import normalize_deadline
                        norm_deadline = normalize_deadline(analysis.deadline, received_at)
                        now_utc = datetime.now(timezone.utc)
                        if norm_deadline:
                            dl_check = norm_deadline if norm_deadline.tzinfo else norm_deadline.replace(tzinfo=timezone.utc)
                            if dl_check < now_utc:
                                database.mark_acknowledged(message_id)
                except Exception:
                    pass

                completed += 1

            except Exception as exc:
                retryable = (
                    isinstance(exc, OllamaClientError)
                    and exc.retryable
                )
                database.mark_failed(
                    message_id,
                    f"{type(exc).__name__}: analysis failed",
                    error_type=type(exc).__name__,
                    retryable=retryable,
                    max_attempts=MAX_ATTEMPTS,
                )
                failed += 1

        return WorkerReport(
            discovered=len(pending),
            completed=completed,
            failed=failed,
            skipped=skipped,
        )

    finally:
        database.release_lock(owner_id=worker_id)
