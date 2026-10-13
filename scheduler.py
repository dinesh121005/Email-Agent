"""Orchestrate one Gmail polling and classification cycle."""

from collections.abc import Callable
from dataclasses import dataclass
import logging
from threading import Event
from time import monotonic

from datetime import datetime, timedelta, timezone
from typing import Any
from ingestion import IngestionReport, ingest_recent_emails
from notifier import NotificationReport, Notifier, dispatch_notifications
from storage import EmailStorage
from worker import WorkerReport, process_pending_emails

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_SECONDS: float = 60 * 60  # 60 minutes


@dataclass(frozen=True)
class ReadStateSyncReport:
    discovered: int
    modified: int
    failed: int
    skipped: int


@dataclass(frozen=True)
class CycleReport:
    ingestion: IngestionReport | None
    worker: WorkerReport | None
    errors: tuple[str, ...]
    duration_seconds: float
    notification: NotificationReport | None = None
    read_sync: ReadStateSyncReport | None = None


def sync_gmail_read_states(
    *,
    storage: EmailStorage | None = None,
    client: Any | None = None,
    limit: int = 50,
    max_retries: int = 3,
) -> ReadStateSyncReport:
    """Synchronize Gmail read status for completed emails when user setting is enabled.

    Requires explicit user setting enablement and active gmail.modify authorization.
    Never marks unanalyzed or incomplete emails as read.
    """
    database = storage or EmailStorage()
    setting = database.get_setting("mark_as_read_enabled", "false")
    is_enabled = str(setting or "").strip().lower() in ("true", "1", "yes")

    if not is_enabled:
        return ReadStateSyncReport(discovered=0, modified=0, failed=0, skipped=0)

    from gmail_client import GmailClient
    gmail = client or GmailClient()

    try:
        has_perm = gmail.has_modify_permission()
    except Exception:
        has_perm = False

    if not has_perm:
        logger.warning(
            "Automatic mark-as-read is enabled by user, but OAuth credentials lack gmail.modify permission. Leaving Gmail messages unread."
        )
        return ReadStateSyncReport(discovered=0, modified=0, failed=0, skipped=0)

    pending = database.get_pending_read_sync(limit=limit, max_retries=max_retries)
    modified = 0
    failed = 0
    skipped = 0

    # Read Decision Mode:
    # 'smart' (default): marks promotional, spam, and non-actionable emails as read automatically;
    # preserves unhandled action items and interviews as unread in Gmail until handled.
    # 'all': marks all analyzed completed emails as read.
    read_policy = database.get_setting("auto_read_policy", "smart")

    for item in pending:
        msg_id = item["message_id"]
        rec = database.get_email(msg_id)
        if not rec or rec.get("status") != "completed":
            skipped += 1
            continue

        if read_policy == "smart":
            requires_action = bool(rec.get("requires_action"))
            is_acknowledged = bool(rec.get("acknowledged_at"))
            is_urgent = rec.get("urgency") in ("urgent", "high")
            is_interview = rec.get("intent") in ("interview_invitation", "interview_scheduling", "interview_confirmation")
            is_spam = rec.get("spam_status") in ("confirmed_spam", "suspected_spam") or rec.get("intent") == "promotional"

            # Check if this email requires user action or is an interview
            if requires_action and not is_acknowledged and not is_spam:
                skipped += 1
                continue

        try:
            success = gmail.mark_as_read(msg_id)
            if success:
                database.mark_read_sync_completed(msg_id)
                modified += 1
            else:
                database.mark_read_sync_failed(msg_id, "ModificationRejected")
                failed += 1
        except Exception as exc:
            database.mark_read_sync_failed(msg_id, type(exc).__name__)
            failed += 1
            logger.error(
                "Failed to mark message %s read in Gmail (%s)",
                msg_id,
                type(exc).__name__,
            )

    return ReadStateSyncReport(
        discovered=len(pending),
        modified=modified,
        failed=failed,
        skipped=skipped,
    )


def run_cycle(
    *,
    storage: EmailStorage | None = None,
    query: str = "newer_than:7d",
    max_results: int = 10,
    batch_limit: int = 1,
    notifier: Notifier | None = None,
    notification_limit: int = 50,
    gmail_client: Any | None = None,
) -> CycleReport:
    """Ingest recent messages, process eligible emails, and dispatch alerts."""
    database = storage or EmailStorage()
    started = monotonic()
    errors: list[str] = []
    ingestion_report = None
    worker_report = None
    notification_report = None
    read_sync_report = None

    try:
        database.record_service_status(
            service_name="background_monitor",
            status="syncing",
        )
    except Exception:
        pass

    try:
        ingest_kwargs = {
            "storage": database,
            "query": query,
            "max_results": max_results,
        }
        if gmail_client is not None:
            ingest_kwargs["client"] = gmail_client
        ingestion_report = ingest_recent_emails(**ingest_kwargs)
    except Exception as exc:
        # Do not log email content, credentials, or raw exception messages.
        errors.append(f"ingestion: {type(exc).__name__}")
        logger.error("Ingestion cycle failed (%s)", type(exc).__name__)

    try:
        worker_report = process_pending_emails(
            storage=database,
            limit=batch_limit,
        )
    except Exception as exc:
        errors.append(f"worker: {type(exc).__name__}")
        logger.error("Worker cycle failed (%s)", type(exc).__name__)

    try:
        notification_report = dispatch_notifications(
            storage=database,
            notifier=notifier,
            limit=notification_limit,
        )
    except Exception as exc:
        errors.append(f"notification: {type(exc).__name__}")
        logger.error("Notification cycle failed (%s)", type(exc).__name__)

    try:
        read_sync_report = sync_gmail_read_states(
            storage=database,
            client=gmail_client,
        )
    except Exception as exc:
        errors.append(f"read_sync: {type(exc).__name__}")
        logger.error("Gmail read-state synchronization failed (%s)", type(exc).__name__)

    duration = monotonic() - started
    logger.info(
        "Cycle finished: inserted=%s duplicates=%s ingestion_failed=%s "
        "processed=%s processing_failed=%s notifications_delivered=%s "
        "notifications_failed=%s read_sync_modified=%s duration_seconds=%.2f",
        ingestion_report.inserted if ingestion_report else 0,
        ingestion_report.duplicates if ingestion_report else 0,
        ingestion_report.failed if ingestion_report else 0,
        worker_report.completed if worker_report else 0,
        worker_report.failed if worker_report else 0,
        notification_report.delivered if notification_report else 0,
        notification_report.failed if notification_report else 0,
        read_sync_report.modified if read_sync_report else 0,
        duration,
    )

    try:
        last_err = "; ".join(errors) if errors else None
        now_dt = datetime.now(timezone.utc)
        database.record_service_status(
            service_name="background_monitor",
            status="error" if (errors and not ingestion_report) else "idle",
            last_sync_at=now_dt if ingestion_report and ingestion_report.failed == 0 else None,
            last_error=last_err,
            details={
                "inserted": ingestion_report.inserted if ingestion_report else 0,
                "completed": worker_report.completed if worker_report else 0,
                "delivered": notification_report.delivered if notification_report else 0,
                "read_sync_modified": read_sync_report.modified if read_sync_report else 0,
            },
        )
    except Exception:
        pass

    return CycleReport(
        ingestion=ingestion_report,
        worker=worker_report,
        errors=tuple(errors),
        duration_seconds=duration,
        notification=notification_report,
        read_sync=read_sync_report,
    )


def run_startup_cycle(
    *,
    storage: EmailStorage | None = None,
    gmail_client: Any | None = None,
    batch_limit: int = 10,
    notifier: Notifier | None = None,
    notification_limit: int = 50,
) -> CycleReport:
    """Execute one startup catch-up cycle discovering accumulated messages and processing incomplete work."""
    from ingestion import build_startup_catchup_query
    database = storage or EmailStorage()
    checkpoint = database.get_checkpoint()
    startup_query = build_startup_catchup_query(checkpoint)
    logger.info("Executing startup catch-up cycle (query=%r)...", startup_query)
    return run_cycle(
        storage=database,
        query=startup_query,
        max_results=50,
        batch_limit=batch_limit,
        notifier=notifier,
        notification_limit=notification_limit,
        gmail_client=gmail_client,
    )



def get_seconds_until_next_hour(now: datetime | None = None) -> float:
    """Calculate the remaining seconds until the top of the next clock hour (:00:00)."""
    now_dt = now or datetime.now(timezone.utc)
    next_hour = (now_dt + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    delay = (next_hour - now_dt).total_seconds()
    return delay if delay > 5 else 3600.0


CycleRunner = Callable[..., CycleReport]


def run_forever(
    *,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    stop_event: Event | None = None,
    cycle_runner: CycleRunner = run_cycle,
    storage: EmailStorage | None = None,
    run_immediately: bool = True,
    align_to_hour: bool = False,
) -> None:
    """Run polling cycles until a shutdown signal is received."""
    if poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be positive")

    stop = stop_event or Event()
    database = storage or EmailStorage()
    logger.info(
        "Mail agent scheduler started; interval_seconds=%.0f (align_to_hour=%s)",
        poll_interval_seconds,
        align_to_hour,
    )

    try:
        database.record_service_status(
            service_name="background_monitor",
            status="running",
        )
    except Exception:
        pass

    first_run = True
    while not stop.is_set():
        if first_run and not run_immediately:
            # Wait until the top of the clock hour before executing the first scheduled cycle
            wait_seconds = get_seconds_until_next_hour() if align_to_hour else poll_interval_seconds
            try:
                next_sync_time = datetime.now(timezone.utc) + timedelta(seconds=wait_seconds)
                database.record_service_status(
                    service_name="background_monitor",
                    status="idle",
                    next_sync_at=next_sync_time,
                )
                database.record_heartbeat("background_monitor")
            except Exception:
                pass
            if stop.wait(wait_seconds):
                break

        # Execute scheduled cycle
        try:
            cycle_runner()
        except Exception as exc:
            # Keep logs safe: do not log raw exception messages.
            logger.error(
                "Unexpected scheduler cycle failure (%s)",
                type(exc).__name__,
            )
            try:
                database.record_service_status(
                    service_name="background_monitor",
                    status="error",
                    last_error=type(exc).__name__,
                )
            except Exception:
                pass

        first_run = False
        wait_seconds = get_seconds_until_next_hour() if align_to_hour else poll_interval_seconds

        try:
            next_sync_time = datetime.now(timezone.utc) + timedelta(seconds=wait_seconds)
            database.record_service_status(
                service_name="background_monitor",
                status="idle",
                next_sync_at=next_sync_time,
            )
            database.record_heartbeat("background_monitor")
        except Exception:
            pass

        # Wait interruptibly; shutdown does not need to wait the full poll interval.
        if stop.wait(wait_seconds):
            break

    try:
        database.record_service_status(
            service_name="background_monitor",
            status="stopped",
        )
        database.record_heartbeat("background_monitor")
    except Exception:
        pass

    logger.info("Mail agent scheduler stopped")

