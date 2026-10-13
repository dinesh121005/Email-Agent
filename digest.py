"""Local deterministic Daily Digest generated from SQLite persistence."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from inbox import InboxItem
from storage import EmailStorage


@dataclass(frozen=True)
class DigestHealthStats:
    total_emails: int
    completed: int
    pending: int
    processing: int
    failed: int
    unnotified_important: int
    unacknowledged_important: int
    last_sync_at: str | None


@dataclass(frozen=True)
class DailyDigest:
    generated_at: str
    upcoming_deadlines: list[InboxItem]
    new_opportunities: list[InboxItem]
    awaiting_action: list[InboxItem]
    health_stats: DigestHealthStats


def build_daily_digest(
    storage: EmailStorage | None = None,
    *,
    now: datetime | None = None,
    opportunity_window_hours: float = 24.0,
) -> DailyDigest:
    """Build a deterministic daily digest from SQLite without invoking the LLM."""
    if opportunity_window_hours <= 0:
        raise ValueError("opportunity_window_hours must be positive")

    database = storage or EmailStorage()
    ref_time = now or datetime.now(timezone.utc)
    if ref_time.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    ref_iso = ref_time.astimezone(timezone.utc).isoformat()
    cutoff_iso = (
        ref_time - timedelta(hours=opportunity_window_hours)
    ).astimezone(timezone.utc).isoformat()

    with database._connect() as conn:
        # 1. Upcoming deadlines (unacknowledged with a non-null deadline)
        deadline_rows = conn.execute("""
            SELECT *
            FROM emails
            WHERE status = 'completed'
              AND deadline IS NOT NULL
              AND acknowledged_at IS NULL
            ORDER BY deadline ASC, urgency ASC
        """).fetchall()
        upcoming_deadlines = [InboxItem.from_row(dict(r)) for r in deadline_rows]

        # 2. New opportunities (classified in opportunity window)
        opp_rows = conn.execute("""
            SELECT *
            FROM emails
            WHERE status = 'completed'
              AND category IN ('job', 'hackathon', 'competition')
              AND (received_at >= ? OR created_at >= ?)
            ORDER BY received_at DESC, message_id ASC
        """, (cutoff_iso, cutoff_iso)).fetchall()
        new_opportunities = [InboxItem.from_row(dict(r)) for r in opp_rows]

        # 3. Still awaiting action (all unacknowledged important messages)
        unack_rows = conn.execute("""
            SELECT *
            FROM emails
            WHERE status = 'completed'
              AND (
                  requires_action = 1
                  OR urgency IN ('urgent', 'high')
                  OR category IN ('job', 'hackathon', 'competition')
              )
              AND acknowledged_at IS NULL
            ORDER BY
                CASE WHEN deadline IS NOT NULL THEN 0 ELSE 1 END,
                deadline ASC,
                CASE urgency
                    WHEN 'urgent' THEN 0
                    WHEN 'high' THEN 1
                    WHEN 'normal' THEN 2
                    ELSE 3
                END,
                received_at DESC
        """).fetchall()
        awaiting_action = [InboxItem.from_row(dict(r)) for r in unack_rows]

        # 4. Agent health statistics
        stats_row = conn.execute("""
            SELECT
                COUNT(*) as total,
                COALESCE(SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END), 0) as completed,
                COALESCE(SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END), 0) as pending,
                COALESCE(SUM(CASE WHEN status = 'processing' THEN 1 ELSE 0 END), 0) as processing,
                COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) as failed
            FROM emails
        """).fetchone()

        unnotified_count = conn.execute("""
            SELECT COUNT(*) as cnt
            FROM emails
            WHERE status = 'completed'
              AND (
                  requires_action = 1
                  OR urgency IN ('urgent', 'high')
                  OR category IN ('job', 'hackathon', 'competition')
              )
              AND notified_at IS NULL
              AND acknowledged_at IS NULL
        """).fetchone()["cnt"]

    health_stats = DigestHealthStats(
        total_emails=stats_row["total"],
        completed=stats_row["completed"],
        pending=stats_row["pending"],
        processing=stats_row["processing"],
        failed=stats_row["failed"],
        unnotified_important=unnotified_count,
        unacknowledged_important=len(awaiting_action),
        last_sync_at=database.get_checkpoint("gmail_sync"),
    )

    return DailyDigest(
        generated_at=ref_iso,
        upcoming_deadlines=upcoming_deadlines,
        new_opportunities=new_opportunities,
        awaiting_action=awaiting_action,
        health_stats=health_stats,
    )


def format_digest_text(digest: DailyDigest) -> str:
    """Format the digest into human-readable plain text without email bodies."""
    lines: list[str] = []
    lines.append("=" * 80)
    lines.append(f"             MAIL AGENT DAILY DIGEST ({digest.generated_at[:10]})")
    lines.append("=" * 80)

    # Section 1: Upcoming Deadlines
    lines.append("\n--- 1. UPCOMING DEADLINES ---")
    if digest.upcoming_deadlines:
        for item in digest.upcoming_deadlines:
            lines.append(
                f"  * [{item.urgency.upper()}] {item.subject} "
                f"(Due: {item.deadline or 'None'}, ID: {item.message_id})"
            )
            if item.summary:
                lines.append(f"    Summary: {item.summary}")
    else:
        lines.append("  No pending deadlines.")

    # Section 2: New Opportunities
    lines.append("\n--- 2. NEW OPPORTUNITIES (LAST 24H) ---")
    if digest.new_opportunities:
        for item in digest.new_opportunities:
            lines.append(
                f"  * [{item.category.upper()}] {item.subject} "
                f"(Received: {item.received_at[:10]}, ID: {item.message_id})"
            )
            if item.summary:
                lines.append(f"    Summary: {item.summary}")
    else:
        lines.append("  No new opportunities today.")

    # Section 3: Still Awaiting Action
    lines.append("\n--- 3. STILL AWAITING ACTION ---")
    if digest.awaiting_action:
        for item in digest.awaiting_action:
            deadline_str = f" | Due: {item.deadline}" if item.deadline else ""
            lines.append(
                f"  * [{item.urgency.upper()}] {item.subject} "
                f"(Category: {item.category}{deadline_str}, ID: {item.message_id})"
            )
    else:
        lines.append("  All important items handled!")

    # Section 4: Agent Health
    h = digest.health_stats
    lines.append("\n--- 4. AGENT HEALTH & OPERATIONS ---")
    lines.append(f"  Total Emails:        {h.total_emails}")
    lines.append(f"  Classified:          {h.completed}")
    lines.append(f"  Pending:             {h.pending}")
    lines.append(f"  Failed:              {h.failed}")
    lines.append(f"  Unnotified Alerts:   {h.unnotified_important}")
    lines.append(f"  Awaiting Action:     {h.unacknowledged_important}")
    lines.append(f"  Last Sync Checkpoint: {h.last_sync_at or 'None'}")
    lines.append("=" * 80)

    return "\n".join(lines)


def format_digest_markdown(digest: DailyDigest) -> str:
    """Format the digest as structured Markdown for persistent archiving."""
    md: list[str] = []
    date_str = digest.generated_at[:10]
    md.append(f"# Mail Agent Daily Digest — {date_str}\n")
    md.append(f"_Generated at: {digest.generated_at}_\n")

    # Deadlines
    md.append("## 1. Upcoming Deadlines & Urgent Actions\n")
    if digest.upcoming_deadlines:
        for item in digest.upcoming_deadlines:
            md.append(
                f"- **[{item.urgency.upper()}] {item.subject}**  \n"
                f"  **Deadline:** `{item.deadline or 'None'}` | **ID:** `{item.message_id}`  \n"
                f"  {item.summary}\n"
            )
    else:
        md.append("_No pending deadlines._\n")

    # Opportunities
    md.append("## 2. New Opportunities (Last 24 Hours)\n")
    if digest.new_opportunities:
        for item in digest.new_opportunities:
            md.append(
                f"- **[{item.category.upper()}] {item.subject}**  \n"
                f"  **Received:** {item.received_at[:10]} | **ID:** `{item.message_id}`  \n"
                f"  {item.summary}\n"
            )
    else:
        md.append("_No new opportunities discovered._\n")

    # Still Awaiting Action
    md.append("## 3. Still Awaiting Action\n")
    if digest.awaiting_action:
        md.append("| Urgency | Category | Deadline | Subject | Message ID |")
        md.append("| :--- | :--- | :--- | :--- | :--- |")
        for item in digest.awaiting_action:
            dl = item.deadline or "-"
            subj = item.subject.replace("|", "/")
            md.append(f"| {item.urgency.upper()} | {item.category} | {dl} | {subj} | `{item.message_id}` |")
        md.append("")
    else:
        md.append("_All important items have been acknowledged._\n")

    # Health
    h = digest.health_stats
    md.append("## 4. Agent Health & Operations\n")
    md.append("| Metric | Count / Value |")
    md.append("| :--- | :--- |")
    md.append(f"| Total Messages Ingested | {h.total_emails} |")
    md.append(f"| Completed Classifications | {h.completed} |")
    md.append(f"| Pending Analysis | {h.pending} |")
    md.append(f"| Failed Claims / Retries Exhausted | {h.failed} |")
    md.append(f"| Unnotified Desktop Alerts | {h.unnotified_important} |")
    md.append(f"| Important Items Awaiting Action | {h.unacknowledged_important} |")
    md.append(f"| Last Gmail Sync Checkpoint | {h.last_sync_at or 'None'} |")

    return "\n".join(md)


def save_digest_markdown(
    digest: DailyDigest,
    output_dir: str | Path = "digests",
) -> Path:
    """Save the digest as a dated Markdown file without overwriting previous runs."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    date_str = digest.generated_at[:10]
    target_path = out_dir / f"digest_{date_str}.md"

    counter = 1
    while target_path.exists():
        target_path = out_dir / f"digest_{date_str}_{counter}.md"
        counter += 1

    target_path.write_text(format_digest_markdown(digest), encoding="utf-8")
    return target_path


def send_digest_toast(
    digest: DailyDigest,
    notifier: Any | None = None,
) -> bool:
    """Best-effort desktop toast notification for the daily digest.

    Never raises exceptions; failure does not prevent saving or archiving reports.
    """
    from notifier import NotificationPayload, windows_toast_notifier

    send = notifier or windows_toast_notifier

    deadlines_count = len(digest.upcoming_deadlines)
    opps_count = len(digest.new_opportunities)
    unack_count = len(digest.awaiting_action)

    title = f"Daily Digest ({digest.generated_at[:10]})"
    summary = (
        f"{deadlines_count} deadline(s), "
        f"{opps_count} opportunity(ies), "
        f"{unack_count} awaiting action."
    )

    earliest_dl = digest.upcoming_deadlines[0].deadline if deadlines_count > 0 else None

    payload = NotificationPayload(
        message_id=f"digest-{digest.generated_at[:10]}",
        subject=title,
        category="digest",
        urgency="high" if deadlines_count > 0 else "normal",
        summary=summary,
        deadline=earliest_dl,
    )

    try:
        return send(payload) is True
    except Exception:
        return False

