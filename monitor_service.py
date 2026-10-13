"""Independent background email monitor service.

Runs scheduled Gmail synchronization, AI classification, and selective notifications
every 60 minutes independently of the desktop dashboard.
"""

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import signal
import sys
from threading import Event, Thread
import time
from typing import Any

from scheduler import run_forever
from storage import EmailStorage

# Ensure logs directory exists
LOGS_DIR = Path(__file__).resolve().parent / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOGS_DIR / "monitor_service.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("mail_agent_monitor")

LOCK_NAME = "monitor_service_process"
LEASE_SECONDS = 120.0
HEARTBEAT_INTERVAL = 30.0


class ServiceLockHolder:
    """Manages an active single-instance process lock with heartbeat renewal."""

    def __init__(self, storage: EmailStorage, owner_id: str) -> None:
        self.storage = storage
        self.owner_id = owner_id
        self._stop_heartbeat = Event()
        self._thread: Thread | None = None

    def acquire(self) -> bool:
        acquired = self.storage.acquire_lock(
            lock_name=LOCK_NAME,
            owner_id=self.owner_id,
            lease_seconds=LEASE_SECONDS,
        )
        if acquired:
            self._start_heartbeat()
        return acquired

    def _start_heartbeat(self) -> None:
        def _loop() -> None:
            while not self._stop_heartbeat.wait(HEARTBEAT_INTERVAL):
                try:
                    # Renew lock and update heartbeat in service_status
                    self.storage.acquire_lock(
                        lock_name=LOCK_NAME,
                        owner_id=self.owner_id,
                        lease_seconds=LEASE_SECONDS,
                    )
                    self.storage.record_heartbeat("background_monitor")
                except Exception as exc:
                    logger.warning("Heartbeat renewal failed: %s", type(exc).__name__)

        self._thread = Thread(target=_loop, daemon=True, name="ServiceLockHeartbeat")
        self._thread.start()

    def release(self) -> None:
        self._stop_heartbeat.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        try:
            self.storage.release_lock(lock_name=LOCK_NAME, owner_id=self.owner_id)
        except Exception:
            pass


def run_monitor(
    storage: EmailStorage | None = None,
    poll_interval_seconds: float = 60 * 60,
    stop_event: Event | None = None,
    startup_cycle_runner: Any | None = None,
) -> int:
    """Start the background monitoring service."""
    db = storage or EmailStorage()
    owner_id = f"pid_{os.getpid()}_{int(time.time())}"

    lock_holder = ServiceLockHolder(db, owner_id)
    if not lock_holder.acquire():
        logger.warning(
            "Another background monitor service instance is already running. Exiting."
        )
        return 0

    stop = stop_event or Event()

    def request_shutdown(signum: int, frame: Any) -> None:
        logger.info("Shutdown signal received (%s); stopping background monitor...", signum)
        stop.set()

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)

    logger.info("=======================================================")
    logger.info("  Local Mail Agent: Background Monitoring Service")
    logger.info("  Sync interval: every %d seconds (%.1f hours)", int(poll_interval_seconds), poll_interval_seconds / 3600.0)
    logger.info("  Target database: %s", db.db_path)
    logger.info("  Process ID: %s", os.getpid())
    logger.info("=======================================================")

    try:
        # Startup catch-up: discover accumulated unread & checkpoint messages and process pending work
        if not stop.is_set():
            try:
                from scheduler import run_startup_cycle
                runner = startup_cycle_runner or run_startup_cycle
                logger.info("Executing startup catch-up synchronization...")
                report = runner(storage=db)

                # When machine turns on, Wi-Fi and DNS can take 10-30 seconds to establish.
                # Retry catch-up if initial ingestion failed due to temporary network unavailability.
                startup_retries = 0
                max_startup_retries = 3
                retry_delay_seconds = 10
                while (
                    report
                    and any("ingestion" in err for err in report.errors)
                    and startup_retries < max_startup_retries
                    and not stop.is_set()
                ):
                    startup_retries += 1
                    logger.info(
                        "Network may still be establishing at startup; retrying catch-up in %ds (attempt %d/%d)...",
                        retry_delay_seconds,
                        startup_retries,
                        max_startup_retries,
                    )
                    if stop.wait(retry_delay_seconds):
                        break
                    report = runner(storage=db)
            except Exception as exc:
                logger.warning(
                    "Startup catch-up encountered warning (%s); continuing to hourly scheduler.",
                    type(exc).__name__,
                )

        run_forever(
            poll_interval_seconds=poll_interval_seconds,
            stop_event=stop,
            storage=db,
            run_immediately=False,
            align_to_hour=True,
        )
        return 0
    finally:
        lock_holder.release()
        try:
            db.record_service_status(
                service_name="background_monitor",
                status="stopped",
            )
        except Exception:
            pass
        logger.info("Background monitor service shut down cleanly.")


if __name__ == "__main__":
    interval = 60 * 60
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        interval = float(sys.argv[1])
    sys.exit(run_monitor(poll_interval_seconds=interval))
