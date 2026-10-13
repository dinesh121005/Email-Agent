"""Tests for the single-cycle scheduler."""

from threading import Event
from time import monotonic
from unittest.mock import MagicMock, patch

import pytest

from ingestion import IngestionReport
from notifier import NotificationReport
from scheduler import CycleReport, run_cycle, run_forever
from storage import EmailStorage
from worker import WorkerReport


@pytest.fixture
def storage(tmp_path):
    return EmailStorage(tmp_path / "scheduler_test.db")


@pytest.fixture
def fake_ingestion_report():
    return IngestionReport(discovered=5, inserted=3, duplicates=2, failed=0)


@pytest.fixture
def fake_worker_report():
    return WorkerReport(discovered=3, completed=3, failed=0, skipped=0)


def test_both_stages_succeed_and_receive_same_storage(
    storage, fake_ingestion_report, fake_worker_report
):
    with patch(
        "scheduler.ingest_recent_emails", return_value=fake_ingestion_report
    ) as mock_ingest, patch(
        "scheduler.process_pending_emails", return_value=fake_worker_report
    ) as mock_worker:
        report = run_cycle(storage=storage)

    assert report.ingestion == fake_ingestion_report
    assert report.worker == fake_worker_report
    assert report.errors == ()
    assert report.duration_seconds >= 0.0

    mock_ingest.assert_called_once_with(
        storage=storage,
        query="newer_than:7d",
        max_results=10,
    )
    mock_worker.assert_called_once_with(
        storage=storage,
        limit=1,
    )


def test_ingestion_fails_worker_still_attempted(storage, fake_worker_report):
    with patch(
        "scheduler.ingest_recent_emails",
        side_effect=RuntimeError("Gmail API token expired"),
    ), patch(
        "scheduler.process_pending_emails", return_value=fake_worker_report
    ) as mock_worker:
        report = run_cycle(storage=storage)

    assert report.ingestion is None
    assert report.worker == fake_worker_report
    assert report.errors == ("ingestion: RuntimeError",)
    assert "Gmail API token expired" not in str(report.errors)
    mock_worker.assert_called_once()


def test_worker_fails_error_captured_safely(storage, fake_ingestion_report):
    with patch(
        "scheduler.ingest_recent_emails", return_value=fake_ingestion_report
    ), patch(
        "scheduler.process_pending_emails",
        side_effect=ValueError("Worker claim lock failed"),
    ):
        report = run_cycle(storage=storage)

    assert report.ingestion == fake_ingestion_report
    assert report.worker is None
    assert report.errors == ("worker: ValueError",)
    assert "Worker claim lock failed" not in str(report.errors)


def test_both_stages_fail_without_exposing_raw_messages(storage):
    with patch(
        "scheduler.ingest_recent_emails",
        side_effect=ConnectionError("Sensitive token xyz in connection string"),
    ), patch(
        "scheduler.process_pending_emails",
        side_effect=Exception("Sensitive email body leaked"),
    ):
        report = run_cycle(storage=storage)

    assert report.ingestion is None
    assert report.worker is None
    assert report.errors == ("ingestion: ConnectionError", "worker: Exception")
    for err in report.errors:
        assert "Sensitive" not in err


def test_cycle_report_records_non_negative_duration(
    storage, fake_ingestion_report, fake_worker_report
):
    with patch(
        "scheduler.ingest_recent_emails", return_value=fake_ingestion_report
    ), patch(
        "scheduler.process_pending_emails", return_value=fake_worker_report
    ):
        report = run_cycle(storage=storage)

    assert isinstance(report.duration_seconds, float)
    assert report.duration_seconds >= 0.0


def test_configuration_passed_to_correct_stage(
    storage, fake_ingestion_report, fake_worker_report
):
    with patch(
        "scheduler.ingest_recent_emails", return_value=fake_ingestion_report
    ) as mock_ingest, patch(
        "scheduler.process_pending_emails", return_value=fake_worker_report
    ) as mock_worker:
        report = run_cycle(
            storage=storage,
            query="label:unread",
            max_results=25,
            batch_limit=15,
        )

    mock_ingest.assert_called_once_with(
        storage=storage,
        query="label:unread",
        max_results=25,
    )
    mock_worker.assert_called_once_with(
        storage=storage,
        limit=15,
    )
    assert report.errors == ()


def test_run_forever_stops_after_shutdown_request():
    stop = Event()
    calls = []

    def fake_cycle():
        calls.append(1)
        stop.set()

    run_forever(
        poll_interval_seconds=0.01,
        stop_event=stop,
        cycle_runner=fake_cycle,
    )
    assert len(calls) == 1


def test_run_forever_rejects_nonpositive_interval():
    with pytest.raises(ValueError):
        run_forever(poll_interval_seconds=0)


def test_run_forever_continues_after_cycle_exception():
    stop = Event()
    calls = []

    def fake_cycle():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("sensitive details must not be logged")
        stop.set()

    run_forever(
        poll_interval_seconds=0.01,
        stop_event=stop,
        cycle_runner=fake_cycle,
    )
    assert len(calls) == 2


def test_scheduler_startup_cycle_ingests_and_processes(storage):
    ingest_report = IngestionReport(discovered=2, inserted=2, duplicates=0, failed=0)
    worker_report = WorkerReport(discovered=2, completed=1, failed=0, skipped=0)

    with patch(
        "scheduler.ingest_recent_emails", return_value=ingest_report
    ) as mock_ingest, patch(
        "scheduler.process_pending_emails", return_value=worker_report
    ) as mock_worker:
        report = run_cycle(storage=storage, batch_limit=1)

    assert report.ingestion == ingest_report
    assert report.worker == worker_report
    assert report.errors == ()
    mock_ingest.assert_called_once_with(
        storage=storage, query="newer_than:7d", max_results=10
    )
    mock_worker.assert_called_once_with(
        storage=storage, limit=1
    )


def test_run_forever_runs_first_cycle_immediately_on_startup():
    stop = Event()
    executions = []

    def fake_startup_cycle():
        executions.append(monotonic())
        stop.set()

    started = monotonic()
    run_forever(
        poll_interval_seconds=5400,  # 90 minutes
        stop_event=stop,
        cycle_runner=fake_startup_cycle,
    )
    duration = monotonic() - started

    assert len(executions) == 1
    # Must have run immediately (< 1.0 second), NOT after waiting 90 minutes
    assert duration < 1.0


def test_scheduler_cycle_dispatches_notifications_with_custom_notifier(
    storage, fake_ingestion_report, fake_worker_report
):
    fake_notification_report = NotificationReport(discovered=1, delivered=1, failed=0)

    with patch(
        "scheduler.ingest_recent_emails", return_value=fake_ingestion_report
    ), patch(
        "scheduler.process_pending_emails", return_value=fake_worker_report
    ), patch(
        "scheduler.dispatch_notifications", return_value=fake_notification_report
    ) as mock_dispatch:
        custom_notifier = MagicMock(return_value=True)
        report = run_cycle(
            storage=storage,
            notifier=custom_notifier,
            notification_limit=10,
        )

    mock_dispatch.assert_called_once_with(
        storage=storage,
        notifier=custom_notifier,
        limit=10,
    )
    assert report.ingestion == fake_ingestion_report
    assert report.worker == fake_worker_report
    assert report.notification == fake_notification_report
    assert report.errors == ()


def test_notification_failure_does_not_abort_scheduler_or_break_reports(
    storage, fake_ingestion_report, fake_worker_report
):
    with patch(
        "scheduler.ingest_recent_emails", return_value=fake_ingestion_report
    ), patch(
        "scheduler.process_pending_emails", return_value=fake_worker_report
    ), patch(
        "scheduler.dispatch_notifications",
        side_effect=RuntimeError("Sensitive toast crash message"),
    ):
        report = run_cycle(storage=storage)

    assert report.ingestion == fake_ingestion_report
    assert report.worker == fake_worker_report
    assert report.notification is None
    assert report.errors == ("notification: RuntimeError",)
    assert "Sensitive toast crash message" not in str(report.errors)


def test_get_seconds_until_next_hour():
    from datetime import datetime, timezone
    from scheduler import get_seconds_until_next_hour

    # Test at 09:40:00 UTC -> should return 20 minutes = 1200 seconds
    t_940 = datetime(2026, 10, 13, 9, 40, 0, tzinfo=timezone.utc)
    assert get_seconds_until_next_hour(t_940) == 1200.0

    # Test at 09:00:00 UTC -> should return 3600 seconds
    t_900 = datetime(2026, 10, 13, 9, 0, 0, tzinfo=timezone.utc)
    assert get_seconds_until_next_hour(t_900) == 3600.0

    # Test at 09:59:45 UTC -> should return 15 seconds
    t_959 = datetime(2026, 10, 13, 9, 59, 45, tzinfo=timezone.utc)
    assert get_seconds_until_next_hour(t_959) == 15.0


def test_run_forever_align_to_hour_records_next_clock_sync(storage):
    stop = Event()
    calls = []

    def fake_cycle():
        calls.append(1)
        stop.set()

    run_forever(
        poll_interval_seconds=3600,
        stop_event=stop,
        cycle_runner=fake_cycle,
        storage=storage,
        align_to_hour=True,
    )

    assert len(calls) == 1
    status = storage.get_service_status("background_monitor")
    assert status is not None
    assert status.get("next_sync_at") is not None




