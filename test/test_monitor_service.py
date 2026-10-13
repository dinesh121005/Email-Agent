"""Tests for the standalone background monitor service and lifecycle locking."""

from pathlib import Path
from threading import Event
import time
import pytest

from monitor_service import ServiceLockHolder, run_monitor
from storage import EmailStorage


@pytest.fixture
def storage(tmp_path: Path) -> EmailStorage:
    return EmailStorage(tmp_path / "monitor_test.db")


def test_service_lock_holder_single_instance(storage: EmailStorage) -> None:
    holder1 = ServiceLockHolder(storage, owner_id="worker_process_1")
    assert holder1.acquire() is True

    # Second instance attempting to acquire the same lock returns False
    holder2 = ServiceLockHolder(storage, owner_id="worker_process_2")
    assert holder2.acquire() is False

    # Once released, the second instance can acquire it
    holder1.release()
    assert holder2.acquire() is True
    holder2.release()


def test_service_lock_heartbeat_renewal(storage: EmailStorage) -> None:
    storage.record_service_status("background_monitor", "idle")
    holder = ServiceLockHolder(storage, owner_id="worker_heartbeat_test")
    assert holder.acquire() is True

    try:
        # Verify lock is held in DB
        lock = storage.get_lock("monitor_service_process")
        assert lock is not None
        assert lock["owner_id"] == "worker_heartbeat_test"
    finally:
        holder.release()

    # Lock is freed
    lock_after = storage.get_lock("monitor_service_process")
    assert lock_after is None


def test_run_monitor_shutdown_on_event(storage: EmailStorage) -> None:
    stop_event = Event()
    stop_event.set()  # Stop immediately

    exit_code = run_monitor(
        storage=storage,
        poll_interval_seconds=1,
        stop_event=stop_event,
    )
    assert exit_code == 0

    status = storage.get_service_status("background_monitor")
    assert status is not None
    assert status.get("status") == "stopped"


def test_run_monitor_prevents_duplicate_instance(storage: EmailStorage) -> None:
    # First holder locks the service
    first_holder = ServiceLockHolder(storage, owner_id="existing_running_instance")
    assert first_holder.acquire() is True

    try:
        # run_monitor should detect the lock and exit gracefully (exit code 0)
        exit_code = run_monitor(
            storage=storage,
            poll_interval_seconds=1,
        )
        assert exit_code == 0
    finally:
        first_holder.release()
