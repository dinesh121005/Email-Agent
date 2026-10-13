"""Notification subsystem and Windows desktop toast delivery with selective policy."""

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any

from storage import EmailStorage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NotificationPayload:
    message_id: str
    subject: str
    category: str
    urgency: str
    summary: str
    deadline: str | None = None
    sender: str = ""
    priority_reason: str | None = None
    importance: str = "normal"
    intent: str = "other"
    notification_reason: str | None = None


@dataclass(frozen=True)
class NotificationReport:
    discovered: int
    delivered: int
    failed: int


@dataclass(frozen=True)
class NotificationPolicy:
    """Configurable notification policy thresholds and rules."""
    deadline_window_hours: float = 72.0
    urgent_window_hours: float = 24.0
    notify_interviews: bool = True
    notify_urgent: bool = True
    notify_high: bool = True
    notify_deadlines: bool = True
    notify_actions: bool = True
    notify_uncertain_review: bool = False
    escalation_enabled: bool = True
    escalation_interval_hours: float = 24.0
    max_escalation_count: int = 2
    app_url_base: str = "http://localhost:8765"


Notifier = Callable[[NotificationPayload], bool]


def evaluate_notification_eligibility(
    item: dict[str, Any],
    policy: NotificationPolicy | None = None,
) -> tuple[bool, str]:
    """Evaluate whether an email qualifies for a desktop notification according to policy.
    
    Returns (is_eligible, decision_reason).
    """
    pol = policy or NotificationPolicy()

    spam_status = item.get("spam_status") or "not_spam"
    intent = str(item.get("intent") or "other")
    category = str(item.get("category") or "other")
    urgency = str(item.get("urgency") or "normal").lower()
    importance = str(item.get("importance") or "normal").lower()
    subject = str(item.get("subject") or "").lower()
    requires_action = bool(item.get("requires_action"))
    deadline = item.get("deadline")

    # 1. Suppressed spam and promotional noise are silent
    if spam_status in ("suspected_spam", "confirmed_spam") or intent in ("promotional", "newsletter"):
        return False, "Suppressed promotional content or spam noise"

    # 2. Uncertain messages requiring review
    if intent == "uncertain" or category == "uncertain":
        if pol.notify_uncertain_review:
            return True, "Ambiguous email flagged for human review"
        return False, "Stored in review queue; notification suppressed by policy"

    # 3. Immediate priority: Genuine interview communications
    is_interview = (
        intent in ("interview_invitation", "interview_scheduling", "interview_confirmation")
        or "interview" in subject
        or "screening" in subject
    )
    if is_interview and pol.notify_interviews:
        return True, "Verified interview communication requiring candidate preparation or scheduling"

    # 4. Immediate priority: Urgent items (e.g. deadline <= 24 hours or imminent event)
    if urgency == "urgent" and pol.notify_urgent:
        return True, "Urgent actionable item requiring prompt attention"

    # 5. Deadline-sensitive: Approaching deadline with action required
    if deadline and pol.notify_deadlines and requires_action:
        return True, f"Approaching deadline: {deadline}"

    # 6. Important: High importance / high urgency actionable items
    if urgency == "high" and pol.notify_high:
        return True, "High urgency email requiring timely response"

    if importance in ("critical", "high") and requires_action and pol.notify_actions:
        return True, f"Actionable {importance} importance communication"

    # 7. Hackathon and competition opportunities with deadlines
    if category in ("hackathon", "competition") and deadline and requires_action:
        return True, f"Actionable {category} opportunity with active deadline"

    return False, "Routine email; does not meet notification threshold"


def windows_toast_notifier(
    payload: NotificationPayload,
    app_url_base: str = "http://localhost:8765",
) -> bool:
    """Best-effort Windows toast delivery using windows-toasts with click-through navigation.

    Returns True on confirmed display, False on any failure or unavailability.
    """
    try:
        from windows_toasts import Toast, WindowsToaster

        toaster = WindowsToaster("Local Mail Agent")

        # Build concise title and summary without exposing sensitive email body.
        intent_lower = str(payload.intent or "").lower()
        subject_lower = str(payload.subject or "").lower()

        if "interview" in intent_lower or "interview" in subject_lower or "screening" in subject_lower:
            tag = "INTERVIEW"
        elif payload.urgency == "urgent":
            tag = "URGENT"
        elif payload.importance in ("critical", "high"):
            tag = "IMPORTANT"
        elif payload.deadline:
            tag = "DEADLINE"
        elif payload.urgency == "high":
            tag = "HIGH"
        else:
            tag = payload.urgency.upper()

        title = f"[{tag}] {payload.subject}"[:64]

        sender_prefix = f"From: {payload.sender} — " if payload.sender else ""
        reason_info = payload.priority_reason or payload.notification_reason or payload.summary
        summary_line = f"{sender_prefix}[{payload.category}] {reason_info}"

        if payload.deadline:
            summary_line = f"{summary_line} (Deadline: {payload.deadline})"

        toast = Toast(text_fields=[title, summary_line[:200]])

        # Configure click-through activation to open the desktop application
        if payload.message_id and app_url_base:
            clean_url = f"{app_url_base.rstrip('/')}/api/navigate?email={payload.message_id}"
            try:
                toast.launch_action = clean_url
            except Exception:
                pass

        toaster.show_toast(toast)
        return True
    except Exception as exc:
        # Keep logs safe: never log email bodies, tokens, or private details.
        logger.error(
            "Windows toast delivery failed (%s)",
            type(exc).__name__,
        )
        return False


def dispatch_notifications(
    *,
    storage: EmailStorage | None = None,
    notifier: Notifier | None = None,
    limit: int = 50,
    policy: NotificationPolicy | None = None,
    include_escalation: bool = True,
) -> NotificationReport:
    """Retrieve eligible unnotified important emails and dispatch alerts.

    Only marks emails as notified if the notifier confirms successful delivery.
    Deduplicates across restarts and supports bounded escalation for urgent items.
    """
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")

    database = storage or EmailStorage()
    send = notifier or windows_toast_notifier
    pol = policy or NotificationPolicy()

    candidates = database.get_unnotified_important(
        limit=limit,
        include_escalation=include_escalation and pol.escalation_enabled,
        escalation_hours=pol.escalation_interval_hours,
        max_escalations=pol.max_escalation_count,
    )
    discovered = 0
    delivered = 0
    failed = 0

    for item in candidates:
        message_id = item.get("message_id")
        if not message_id or not isinstance(message_id, str):
            failed += 1
            logger.warning("Skipping invalid item with missing message_id")
            continue

        is_eligible, reason = evaluate_notification_eligibility(item, pol)
        if not is_eligible:
            # Not eligible under current policy
            continue

        discovered += 1
        payload = NotificationPayload(
            message_id=message_id,
            subject=str(item.get("subject") or ""),
            category=str(item.get("category") or "other"),
            urgency=str(item.get("urgency") or "normal"),
            summary=str(item.get("summary") or ""),
            deadline=(
                item.get("deadline")
                if isinstance(item.get("deadline"), str)
                else None
            ),
            sender=str(item.get("sender") or ""),
            priority_reason=(
                item.get("priority_reason")
                if isinstance(item.get("priority_reason"), str)
                else None
            ),
            importance=str(item.get("importance") or "normal"),
            intent=str(item.get("intent") or "other"),
            notification_reason=reason,
        )

        try:
            success = send(payload)
            if success is True:
                database.mark_notified(message_id, reason=reason)
                delivered += 1
            else:
                failed += 1
                logger.warning(
                    "Notifier reported delivery failure for message %s",
                    message_id,
                )
        except Exception as exc:
            failed += 1
            logger.error(
                "Notifier raised error for message %s (%s)",
                message_id,
                type(exc).__name__,
            )

    return NotificationReport(
        discovered=discovered,
        delivered=delivered,
        failed=failed,
    )
