"""Ingest recent Gmail messages into local SQLite storage."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from email_parser import EmailParseError, parse_gmail_message
from gmail_client import GmailClient, GmailClientError, GmailListResult
from storage import EmailStorage


@dataclass(frozen=True)
class IngestionReport:
    discovered: int
    inserted: int
    duplicates: int
    failed: int


def build_sync_query(checkpoint: str | None, include_unread: bool = False) -> str:
    """Build the Gmail query from checkpoint timestamp minus a 2-hour overlap."""
    if checkpoint is None:
        return "newer_than:7d"

    timestamp = datetime.fromisoformat(
        checkpoint.replace("Z", "+00:00")
    )
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Checkpoint must be timezone-aware")

    overlap_start = timestamp.astimezone(timezone.utc) - timedelta(hours=2)
    epoch_seconds = int(overlap_start.timestamp())
    if include_unread:
        return f"after:{epoch_seconds} OR is:unread"
    return f"after:{epoch_seconds}"


def build_startup_catchup_query(checkpoint: str | None) -> str:
    """Build a catch-up Gmail query covering checkpoint overlap and unread messages accumulated during downtime."""
    return build_sync_query(checkpoint, include_unread=True)


def ingest_recent_emails(
    *,
    client: GmailClient | None = None,
    storage: EmailStorage | None = None,
    query: str | None = None,
    max_results: int | None = None,
    max_total_results: int = 1000,
    page_size: int = 100,
) -> IngestionReport:
    """Fetch, parse, and persist recent emails without analyzing them."""
    if not 1 <= page_size <= 100:
        raise ValueError("page_size must be between 1 and 100")
    if max_total_results < 1:
        raise ValueError("max_total_results must be positive")
    if max_results is not None:
        if not 1 <= max_results <= 1000:
            raise ValueError("max_results must be between 1 and 1000")
        max_total_results = min(max_results, max_total_results)

    gmail = client or GmailClient()
    database = storage or EmailStorage()

    if query is None or query == "newer_than:7d":
        checkpoint = database.get_checkpoint()
        effective_query = build_sync_query(checkpoint)
    else:
        effective_query = query

    if hasattr(gmail, "list_messages_paginated"):
        list_result = gmail.list_messages_paginated(
            query=effective_query,
            page_size=min(page_size, max_total_results),
            max_total_results=max_total_results,
        )
        references = list_result.messages
        is_complete = list_result.complete
    else:
        references = gmail.list_recent_messages(
            query=effective_query,
            max_results=min(max_results or page_size, max_total_results),
        )
        is_complete = True

    inserted = 0
    duplicates = 0
    failed = 0

    for reference in references:
        message_id = reference.get("id")

        if not isinstance(message_id, str) or not message_id.strip():
            failed += 1
            continue

        try:
            raw_message = gmail.get_message(message_id)
            parsed_email = parse_gmail_message(raw_message)

            # Use the ID returned by Gmail's list operation as a consistency
            # check against an unexpected or malformed message response.
            if parsed_email.message_id != message_id:
                failed += 1
                continue

        except (GmailClientError, EmailParseError):
            failed += 1
            continue

        # Let storage failures propagate. A database outage must not be
        # reported as a successful ingestion run.
        was_inserted = database.save_email(parsed_email)

        if was_inserted:
            inserted += 1
        else:
            duplicates += 1

    # Checkpoint safety rule: advance checkpoint only when pagination was complete
    # and every discovered message was successfully persisted or recognized as duplicate.
    if is_complete and failed == 0:
        account_email = None
        if hasattr(gmail, "get_user_email"):
            try:
                account_email = gmail.get_user_email()
            except Exception:
                account_email = None
        database.set_checkpoint(
            synced_at=datetime.now(timezone.utc),
            account_email=account_email,
        )

    return IngestionReport(
        discovered=len(references),
        inserted=inserted,
        duplicates=duplicates,
        failed=failed,
    )
