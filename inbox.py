"""Persistent important-message inbox queries and management."""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from storage import EmailStorage

URGENCY_WEIGHTS = {
    "urgent": 0,
    "high": 1,
    "normal": 2,
    "low": 3,
}


@dataclass(frozen=True)
class InboxItem:
    message_id: str
    subject: str
    sender: str
    received_at: str
    category: str
    urgency: str
    summary: str
    deadline: str | None
    requires_action: bool
    notified_at: str | None
    acknowledged_at: str | None
    status: str
    intent: str = "other"
    importance: str = "normal"
    spam_status: str = "not_spam"
    priority_reason: str | None = None
    recommended_action: str | None = None
    evidence: list[str] = field(default_factory=list)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "InboxItem":
        ev = row.get("evidence")
        parsed_evidence: list[str] = []
        if isinstance(ev, list):
            parsed_evidence = [str(x) for x in ev]
        elif isinstance(ev, str) and ev.strip():
            try:
                loaded = json.loads(ev)
                if isinstance(loaded, list):
                    parsed_evidence = [str(x) for x in loaded]
                elif isinstance(loaded, str):
                    parsed_evidence = [loaded]
            except Exception:
                parsed_evidence = [ev]

        return cls(
            message_id=str(row.get("message_id") or ""),
            subject=str(row.get("subject") or ""),
            sender=str(row.get("sender") or ""),
            received_at=str(row.get("received_at") or ""),
            category=str(row.get("category") or "other"),
            urgency=str(row.get("urgency") or "normal"),
            summary=str(row.get("summary") or ""),
            deadline=(
                row.get("deadline")
                if isinstance(row.get("deadline"), str)
                else None
            ),
            requires_action=bool(row.get("requires_action")),
            notified_at=(
                row.get("notified_at")
                if isinstance(row.get("notified_at"), str)
                else None
            ),
            acknowledged_at=(
                row.get("acknowledged_at")
                if isinstance(row.get("acknowledged_at"), str)
                else None
            ),
            status=str(row.get("status") or ""),
            intent=str(row.get("intent") or "other"),
            importance=str(row.get("importance") or "normal"),
            spam_status=str(row.get("spam_status") or "not_spam"),
            priority_reason=(
                row.get("priority_reason")
                if isinstance(row.get("priority_reason"), str)
                else None
            ),
            recommended_action=(
                row.get("recommended_action")
                if isinstance(row.get("recommended_action"), str)
                else None
            ),
            evidence=parsed_evidence,
        )


def _sort_key(item: InboxItem) -> tuple[int, Any, int, str]:
    """Sort key prioritizing actionable deadlines, then urgency, then received_at."""
    urgency_rank = URGENCY_WEIGHTS.get(item.urgency.lower(), 9)

    if item.deadline:
        # Items with deadlines first, ordered by deadline ascending
        return (0, item.deadline, urgency_rank, item.received_at)

    # Items without deadlines, ordered by urgency, then newest received
    return (1, "", urgency_rank, item.received_at)


def list_important_messages(
    storage: EmailStorage | None = None,
    *,
    unacknowledged_only: bool = True,
    sort_by_deadline: bool = True,
    limit: int = 50,
) -> list[InboxItem]:
    """Retrieve important inbox messages, optionally filtered and sorted."""
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")

    database = storage or EmailStorage()
    rows = database.get_important_emails(
        unacknowledged_only=unacknowledged_only,
        limit=limit,
    )

    items = [InboxItem.from_row(row) for row in rows]

    if sort_by_deadline:
        items.sort(key=_sort_key)

    return items


def acknowledge_message(
    message_id: str,
    storage: EmailStorage | None = None,
    sync_gmail_read: bool = True,
) -> bool:
    """Mark an important message as handled by the user.

    Preserves the message record, category, urgency, and classification intact.
    """
    clean_id = message_id.strip()
    if not clean_id:
        return False

    database = storage or EmailStorage()
    success = database.mark_acknowledged(clean_id)
    if success and sync_gmail_read and database.get_setting("mark_as_read_enabled") == "true":
        try:
            from gmail_client import GmailClient
            client = GmailClient()
            if client.has_modify_permission():
                if client.mark_as_read(clean_id):
                    database.mark_read_sync_completed(clean_id)
        except Exception:
            pass
    return success


def get_inbox_item(
    message_id: str,
    storage: EmailStorage | None = None,
) -> InboxItem | None:
    """Retrieve a single inbox item by message ID."""
    clean_id = message_id.strip()
    if not clean_id:
        return None

    database = storage or EmailStorage()
    row = database.get_email(clean_id)
    if not row:
        return None

    return InboxItem.from_row(row)


def format_inbox_table(items: list[InboxItem]) -> str:
    """Render items into a safe, human-readable CLI table without email bodies."""
    if not items:
        return "No important messages found in inbox."

    header = (
        f"{'ID':<18} {'URGENCY':<9} {'CATEGORY':<13} "
        f"{'DEADLINE':<25} {'ACK':<5} {'SUBJECT'}"
    )
    separator = "-" * len(header)
    lines = [header, separator]

    for item in items:
        ack_str = "YES" if item.acknowledged_at else "NO"
        deadline_str = item.deadline or "-"
        subj = item.subject[:40] if len(item.subject) > 40 else item.subject
        lines.append(
            f"{item.message_id:<18} "
            f"{item.urgency.upper():<9} "
            f"{item.category:<13} "
            f"{deadline_str:<25} "
            f"{ack_str:<5} "
            f"{subj}"
        )

    return "\n".join(lines)


def format_item_details(item: InboxItem) -> str:
    """Render detailed view of an inbox item without printing email body."""
    ack_status = (
        f"Acknowledged at {item.acknowledged_at}"
        if item.acknowledged_at
        else "Pending action (Unacknowledged)"
    )
    notified_status = (
        f"Delivered at {item.notified_at}"
        if item.notified_at
        else "Not yet delivered"
    )

    lines = [
        f"Message ID:     {item.message_id}",
        f"Subject:        {item.subject}",
        f"Sender:         {item.sender}",
        f"Received At:    {item.received_at}",
        f"Intent:         {item.intent}",
        f"Importance:     {item.importance}",
        f"Urgency:        {item.urgency}",
        f"Category:       {item.category}",
        f"Spam Status:    {item.spam_status}",
        f"Action Needed:  {'Yes' if item.requires_action else 'No'}",
        f"Deadline:       {item.deadline or 'None'}",
        f"Notification:   {notified_status}",
        f"Status:         {ack_status}",
    ]
    if item.recommended_action:
        lines.append(f"Recommended:    {item.recommended_action}")
    if item.priority_reason:
        lines.append(f"Priority Reason:{item.priority_reason}")
    if item.evidence:
        lines.append(f"Evidence:       {', '.join(item.evidence)}")

    lines.append(f"\nSummary:\n{item.summary}")
    return "\n".join(lines)
