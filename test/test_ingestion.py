from datetime import datetime, timedelta, timezone

import pytest

from email_parser import ParsedEmail
from ingestion import build_sync_query, ingest_recent_emails
from storage import EmailStorage


class FakeGmailClient:
    def __init__(self, messages):
        self.messages = messages
        self.fetch_calls = []
        self.last_query = None

    def list_recent_messages(self, *, query, max_results):
        self.last_query = query
        return [
            {"id": message_id, "threadId": f"thread-{message_id}"}
            for message_id in list(self.messages)[:max_results]
        ]

    def list_messages_paginated(self, *, query, page_size=100, max_total_results=1000):
        from gmail_client import GmailListResult
        self.last_query = query
        items = [
            {"id": message_id, "threadId": f"thread-{message_id}"}
            for message_id in list(self.messages)[:max_total_results]
        ]
        return GmailListResult(
            messages=items,
            complete=len(self.messages) <= max_total_results,
        )

    def get_message(self, message_id):
        self.fetch_calls.append(message_id)
        return self.messages[message_id]


def make_raw_message(message_id, subject="Internship opportunity"):
    import base64

    def encode(text):
        return base64.urlsafe_b64encode(
            text.encode("utf-8")
        ).decode().rstrip("=")

    return {
        "id": message_id,
        "threadId": f"thread-{message_id}",
        "internalDate": "1791806400000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "jobs@example.com"},
                {"name": "Subject", "value": subject},
                {
                    "name": "Date",
                    "value": "Mon, 12 Oct 2026 10:00:00 +0530",
                },
            ],
            "body": {"data": encode("Apply by Friday.")},
        },
    }


@pytest.fixture
def storage(tmp_path):
    return EmailStorage(tmp_path / "ingestion_test.db")


def test_ingests_new_messages(storage):
    client = FakeGmailClient({
        "msg-1": make_raw_message("msg-1"),
        "msg-2": make_raw_message("msg-2"),
    })

    report = ingest_recent_emails(client=client, storage=storage)

    assert report.discovered == 2
    assert report.inserted == 2
    assert report.duplicates == 0
    assert report.failed == 0
    assert storage.get_email("msg-1")["status"] == "pending"
    assert storage.get_email("msg-2")["status"] == "pending"


def test_repeated_ingestion_does_not_create_duplicates(storage):
    client = FakeGmailClient({
        "msg-1": make_raw_message("msg-1"),
    })

    first = ingest_recent_emails(client=client, storage=storage)
    second = ingest_recent_emails(client=client, storage=storage)

    assert first.inserted == 1
    assert second.inserted == 0
    assert second.duplicates == 1


def test_duplicate_ingestion_does_not_overwrite_existing_classification(
    storage,
):
    client = FakeGmailClient({
        "msg-1": make_raw_message("msg-1"),
    })

    ingest_recent_emails(client=client, storage=storage)

    storage.save_analysis("msg-1", {
        "category": "job",
        "urgency": "high",
        "summary": "Apply for the internship.",
        "deadline": None,
        "requires_action": True,
    })

    # A later ingestion sees the same Gmail ID and must not reset its status.
    report = ingest_recent_emails(client=client, storage=storage)

    assert report.duplicates == 1
    saved = storage.get_email("msg-1")
    assert saved["status"] == "completed"
    assert saved["category"] == "job"


def test_invalid_message_is_counted_and_processing_continues(storage):
    client = FakeGmailClient({
        "bad": {"id": "bad"},
        "good": make_raw_message("good"),
    })

    report = ingest_recent_emails(client=client, storage=storage)

    assert report.discovered == 2
    assert report.inserted == 1
    assert report.failed == 1
    assert storage.get_email("good") is not None


def test_rejects_invalid_max_results(storage):
    client = FakeGmailClient({})

    with pytest.raises(ValueError, match="max_results"):
        ingest_recent_emails(
            client=client,
            storage=storage,
            max_results=0,
        )


def test_empty_inbox_returns_zero_counts(storage):
    client = FakeGmailClient({})

    report = ingest_recent_emails(client=client, storage=storage)

    assert report.discovered == 0
    assert report.inserted == 0
    assert report.duplicates == 0
    assert report.failed == 0


def test_ingestion_respects_max_results(storage):
    client = FakeGmailClient({
        f"msg-{i}": make_raw_message(f"msg-{i}")
        for i in range(3)
    })

    report = ingest_recent_emails(
        client=client,
        storage=storage,
        max_results=2,
    )

    assert report.discovered == 2
    assert report.inserted == 2


def test_build_sync_query_none_returns_newer_than_7d():
    assert build_sync_query(None) == "newer_than:7d"


def test_build_sync_query_with_checkpoint():
    dt = datetime(2026, 10, 12, 12, 0, 0, tzinfo=timezone.utc)
    expected_epoch = int(
        (dt - timedelta(hours=2)).timestamp()
    )
    assert build_sync_query(dt.isoformat()) == f"after:{expected_epoch}"


def test_build_sync_query_rejects_naive():
    with pytest.raises(ValueError, match="timezone-aware"):
        build_sync_query("2026-10-12T12:00:00")


def test_successful_complete_ingestion_advances_checkpoint(storage):
    client = FakeGmailClient({
        "msg-1": make_raw_message("msg-1"),
    })
    assert storage.get_checkpoint() is None

    report = ingest_recent_emails(client=client, storage=storage)

    assert report.inserted == 1
    assert report.failed == 0
    assert storage.get_checkpoint() is not None


def test_parse_failure_leaves_checkpoint_unchanged(storage):
    initial_cp = "2026-10-12T08:00:00+00:00"
    storage.set_checkpoint(synced_at=initial_cp)

    client = FakeGmailClient({
        "bad-msg": {"id": "bad-msg"},
        "good-msg": make_raw_message("good-msg"),
    })

    report = ingest_recent_emails(client=client, storage=storage)

    assert report.failed == 1
    assert storage.get_checkpoint() == initial_cp


def test_incomplete_pagination_leaves_checkpoint_unchanged(storage):
    initial_cp = "2026-10-12T08:00:00+00:00"
    storage.set_checkpoint(synced_at=initial_cp)

    class IncompleteGmailClient:
        def list_messages_paginated(
            self, *, query, page_size=100, max_total_results=1000
        ):
            from gmail_client import GmailListResult
            return GmailListResult(
                messages=[{"id": "msg-1", "threadId": "t-1"}],
                complete=False,
            )

        def get_message(self, message_id):
            return make_raw_message(message_id)

    report = ingest_recent_emails(
        client=IncompleteGmailClient(), storage=storage
    )

    assert report.inserted == 1
    assert report.failed == 0
    assert storage.get_checkpoint() == initial_cp


def test_reingesting_overlapping_messages_increments_duplicates(storage):
    client = FakeGmailClient({
        "msg-1": make_raw_message("msg-1"),
    })

    first = ingest_recent_emails(client=client, storage=storage)
    assert first.inserted == 1
    assert first.duplicates == 0

    second = ingest_recent_emails(client=client, storage=storage)
    assert second.inserted == 0
    assert second.duplicates == 1
    assert storage.get_checkpoint() is not None


def test_ingestion_uses_checkpoint_query_when_checkpoint_exists(storage):
    initial_cp = datetime(2026, 10, 12, 12, 0, 0, tzinfo=timezone.utc).isoformat()
    storage.set_checkpoint(synced_at=initial_cp)

    client = FakeGmailClient({})
    ingest_recent_emails(client=client, storage=storage)

    expected_epoch = int(
        (datetime(2026, 10, 12, 12, 0, 0, tzinfo=timezone.utc) - timedelta(hours=2)).timestamp()
    )
    assert client.last_query == f"after:{expected_epoch}"


def test_catchup_ingests_missed_emails_after_downtime(storage):
    # Checkpoint saved before laptop shut down 6 hours ago
    shutdown_time = datetime(2026, 10, 12, 6, 0, 0, tzinfo=timezone.utc)
    storage.set_checkpoint(synced_at=shutdown_time)

    # 3 emails arrived while the machine was off
    client = FakeGmailClient({
        "offline-1": make_raw_message("offline-1", subject="Email while sleeping"),
        "offline-2": make_raw_message("offline-2", subject="Urgent reminder"),
        "offline-3": make_raw_message("offline-3", subject="Meeting invite"),
    })

    # Laptop turns on -> catch-up cycle runs
    report = ingest_recent_emails(client=client, storage=storage)

    assert report.discovered == 3
    assert report.inserted == 3
    assert report.duplicates == 0
    assert report.failed == 0

    assert storage.get_email("offline-1")["status"] == "pending"
    assert storage.get_email("offline-2")["status"] == "pending"
    assert storage.get_email("offline-3")["status"] == "pending"

    # Checkpoint has now advanced beyond shutdown_time
    new_checkpoint = storage.get_checkpoint()
    assert new_checkpoint is not None
    assert new_checkpoint > shutdown_time.isoformat()


def test_catchup_overlapping_runs_produce_zero_duplicates(storage):
    # Initial sync inserts msg-1
    client = FakeGmailClient({
        "msg-1": make_raw_message("msg-1"),
    })
    first_report = ingest_recent_emails(client=client, storage=storage)
    assert first_report.inserted == 1
    assert first_report.duplicates == 0

    # Second catch-up cycle with overlapping query sees msg-1 plus msg-2
    client_with_overlap = FakeGmailClient({
        "msg-1": make_raw_message("msg-1"),
        "msg-2": make_raw_message("msg-2"),
    })
    second_report = ingest_recent_emails(client=client_with_overlap, storage=storage)

    assert second_report.discovered == 2
    assert second_report.inserted == 1
    assert second_report.duplicates == 1
    assert second_report.failed == 0
    assert storage.get_email("msg-1") is not None
    assert storage.get_email("msg-2") is not None


