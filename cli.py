"""Command-line interface for the local mail agent inbox and scheduler."""

import argparse
import sys
from typing import Sequence

from digest import (
    build_daily_digest,
    format_digest_text,
    save_digest_markdown,
)
from inbox import (
    acknowledge_message,
    format_inbox_table,
    format_item_details,
    get_inbox_item,
    list_important_messages,
)
from storage import EmailStorage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mail-agent",
        description="Local Mail Agent CLI: manage important emails, synchronization, and inbox status.",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Path to SQLite database (default: mail_agent.db or MAIL_AGENT_DB env var)",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Command: list
    list_parser = subparsers.add_parser(
        "list",
        help="List stored important inbox emails (does not contact Gmail API)",
    )
    list_parser.add_argument(
        "--all",
        action="store_true",
        help="Include already acknowledged items",
    )
    list_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum items to display (default: 50)",
    )
    list_parser.add_argument(
        "--no-sort",
        action="store_true",
        help="Disable sorting by deadline and urgency",
    )

    # Command: sync
    sync_parser = subparsers.add_parser(
        "sync",
        help="Synchronize emails from Gmail API into local database",
    )
    sync_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum emails to fetch (default: 10)",
    )
    sync_parser.add_argument(
        "--query",
        type=str,
        default=None,
        help="Custom Gmail search query (default: checkpoint query or newer_than:7d)",
    )

    # Command: classify
    classify_parser = subparsers.add_parser(
        "classify",
        help="Run local AI model to classify pending emails",
    )
    classify_parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum emails to classify in this batch (default: 5)",
    )

    # Command: reanalyze
    reanalyze_parser = subparsers.add_parser(
        "reanalyze",
        help="Re-analyze existing emails safely without deleting or re-ingesting",
    )
    reanalyze_parser.add_argument(
        "--message-id",
        type=str,
        default=None,
        help="Specific message ID to re-analyze",
    )
    reanalyze_parser.add_argument(
        "--all",
        action="store_true",
        help="Reset and re-analyze all completed emails",
    )
    reanalyze_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum emails to re-analyze in this batch (default: 10)",
    )

    # Command: ack
    ack_parser = subparsers.add_parser(
        "ack",
        help="Acknowledge an important email by message ID",
    )
    ack_parser.add_argument(
        "message_id",
        type=str,
        help="Gmail message ID to acknowledge",
    )

    # Command: show
    show_parser = subparsers.add_parser(
        "show",
        help="Show details and summary of an important email",
    )
    show_parser.add_argument(
        "message_id",
        type=str,
        help="Gmail message ID to inspect",
    )

    # Command: digest
    digest_parser = subparsers.add_parser(
        "digest",
        help="Generate and display the local deterministic daily digest",
    )
    digest_parser.add_argument(
        "--save",
        action="store_true",
        help="Save digest as a dated Markdown report in digests/ directory",
    )
    digest_parser.add_argument(
        "--output-dir",
        type=str,
        default="digests",
        help="Directory to save Markdown report (default: digests)",
    )
    digest_parser.add_argument(
        "--hours",
        type=float,
        default=24.0,
        help="Window for new opportunities in hours (default: 24.0)",
    )

    # Command: app / serve / desktop
    for cmd_name, cmd_help in [
        ("app", "Launch the desktop email application interface"),
        ("serve", "Alias for app: launch the desktop email application server"),
        ("desktop", "Launch the mail agent in a standalone native desktop window"),
    ]:
        p = subparsers.add_parser(cmd_name, help=cmd_help)
        p.add_argument(
            "--port",
            type=int,
            default=8765,
            help="Port for the desktop app server (default: 8765)",
        )
        p.add_argument(
            "--no-browser",
            action="store_true",
            help="Do not automatically open default browser",
        )
        p.add_argument(
            "--window",
            "--desktop",
            dest="window",
            action="store_true",
            default=(cmd_name == "desktop"),
            help="Open in a standalone desktop window without browser tabs or address bar",
        )

    # Command: auth
    auth_parser = subparsers.add_parser(
        "auth",
        help="Connect, switch, or inspect the authorized Gmail account",
    )
    auth_parser.add_argument(
        "--switch",
        action="store_true",
        help="Revoke local token and re-authenticate (forces browser account selector)",
    )
    auth_parser.add_argument(
        "--check",
        action="store_true",
        help="Check and display currently authenticated Gmail account and database",
    )
    auth_parser.add_argument(
        "--personal",
        action="store_true",
        help="Configure and use mail_agent_personal.db for the personal account",
    )
    auth_parser.add_argument(
        "--fresh-db",
        action="store_true",
        help="Initialize dedicated personal database (mail_agent_personal.db) preserving test database",
    )

    # Command: status
    subparsers.add_parser(
        "status",
        help="Check observable background worker and synchronization status",
    )

    # Command: monitor
    monitor_parser = subparsers.add_parser(
        "monitor",
        help="Run the independent background monitor service loop",
    )
    monitor_parser.add_argument(
        "--interval",
        type=int,
        default=3600,
        help="Polling interval in seconds (default: 3600 = 60 minutes)",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    import os
    if args.db:
        os.environ["MAIL_AGENT_DB"] = args.db

    storage = EmailStorage(args.db) if args.db else EmailStorage()

    if args.command == "list" or args.command is None:
        show_all = getattr(args, "all", False)
        limit = getattr(args, "limit", 50)
        sort_by_deadline = not getattr(args, "no_sort", False)

        items = list_important_messages(
            storage=storage,
            unacknowledged_only=not show_all,
            sort_by_deadline=sort_by_deadline,
            limit=limit,
        )
        print(format_inbox_table(items))
        return 0

    if args.command == "sync":
        from ingestion import ingest_recent_emails
        from gmail_client import GmailClient
        client = GmailClient()
        try:
            user_email = client.get_user_email()
            print(f"Connected Gmail Account: {user_email}")
        except Exception:
            pass
        print(f"Target Database: {storage.db_path}")
        print(f"Synchronizing up to {args.limit} messages from Gmail...")
        report = ingest_recent_emails(
            client=client,
            storage=storage,
            query=args.query,
            max_results=args.limit,
        )
        print(
            f"Sync complete: discovered={report.discovered}, "
            f"inserted={report.inserted}, duplicates={report.duplicates}, "
            f"failed={report.failed}"
        )
        checkpoint = storage.get_checkpoint()
        if checkpoint:
            print(f"Sync checkpoint advanced to: {checkpoint}")
        return 0

    if args.command == "classify":
        from worker import process_pending_emails
        limit = getattr(args, "limit", 5)
        print(f"Target Database: {storage.db_path}")
        print(f"Running local AI classification on up to {limit} pending emails...")
        report = process_pending_emails(storage=storage, limit=limit)
        print(
            f"Classification complete: discovered={report.discovered}, "
            f"completed={report.completed}, failed={report.failed}, "
            f"skipped={report.skipped}"
        )
        return 0

    if args.command == "reanalyze":
        from worker import process_pending_emails
        target_id = getattr(args, "message_id", None)
        reanalyze_all = getattr(args, "all", False)
        limit = getattr(args, "limit", 10)

        if target_id:
            storage.reset_for_reanalysis(target_id)
            print(f"Reset email {target_id} to pending for re-analysis.")
        elif reanalyze_all:
            with storage._connect() as conn:
                conn.execute(
                    "UPDATE emails SET status = 'pending', attempt_count = 0, next_retry_at = NULL WHERE status = 'completed'"
                )
            print("Reset all completed emails to pending for re-analysis.")

        report = process_pending_emails(storage=storage, limit=limit)
        print(
            f"Re-analysis batch complete: discovered={report.discovered}, "
            f"completed={report.completed}, failed={report.failed}, "
            f"skipped={report.skipped}"
        )
        return 0

    if args.command == "ack":
        success = acknowledge_message(args.message_id, storage=storage)
        if success:
            print(f"Message {args.message_id!r} marked as acknowledged.")
            return 0
        else:
            print(
                f"Error: message {args.message_id!r} not found or could not be acknowledged.",
                file=sys.stderr,
            )
            return 1

    if args.command == "show":
        item = get_inbox_item(args.message_id, storage=storage)
        if item:
            print(format_item_details(item))
            return 0
        else:
            print(f"Error: message {args.message_id!r} not found.", file=sys.stderr)
            return 1

    if args.command == "digest":
        daily_digest = build_daily_digest(
            storage=storage,
            opportunity_window_hours=args.hours,
        )
        print(format_digest_text(daily_digest))

        if args.save:
            saved_path = save_digest_markdown(
                daily_digest,
                output_dir=args.output_dir,
            )
            print(f"\nDigest saved to: {saved_path}")
        return 0

    if args.command in {"app", "serve", "desktop"}:
        from app import start_app
        port = getattr(args, "port", 8765)
        no_browser = getattr(args, "no_browser", False)
        as_window = getattr(args, "window", False) or (args.command == "desktop")
        start_app(port=port, open_browser=not no_browser, as_window=as_window, storage=storage)
        return 0

    if args.command == "auth":
        from pathlib import Path
        token_path = Path("token.json")
        backup_db_path = Path("mail_agent_test.db")
        default_db_path = Path("mail_agent.db")

        # Preserve test database safely using SQLite backup API if not already backed up
        if default_db_path.exists() and not backup_db_path.exists():
            print(f"Preserving existing test database: creating SQLite backup to {backup_db_path}...")
            storage.backup_to(backup_db_path)

        if getattr(args, "fresh_db", False) or getattr(args, "personal", False):
            personal_db = Path("mail_agent_personal.db")
            os.environ["MAIL_AGENT_DB"] = str(personal_db)
            storage = EmailStorage(personal_db)
            print(f"Configured dedicated personal database: {personal_db}")
            print(f"Preserved test databases: {default_db_path} and {backup_db_path}")

        from gmail_client import GmailClient
        client = GmailClient()

        if getattr(args, "check", False):
            try:
                user_email = client.get_user_email()
                print(f"Currently authenticated Gmail account: {user_email}")
                print(f"Active database: {storage.db_path}")
                checkpoint = storage.get_checkpoint()
                account_cp = storage.get_checkpoint_account()
                print(f"Sync checkpoint: {checkpoint} (account: {account_cp or 'unrecorded'})")
                return 0
            except Exception as exc:
                print(f"Unable to retrieve account profile ({exc}). Please authenticate.")
                return 1

        if getattr(args, "switch", False) and token_path.exists():
            token_path.unlink()
            print("Removed previous token.json.")

        print("Launching Google OAuth authorization flow (account chooser enabled)...")
        client.connect()
        user_email = client.get_user_email()
        print(f"Successfully authenticated as: {user_email}")
        print(f"OAuth token saved to: {token_path}")
        print(f"Active database: {storage.db_path}")
        return 0

    if args.command == "status":
        svc = storage.get_service_status("background_monitor")
        checkpoint = storage.get_checkpoint()
        account_cp = storage.get_checkpoint_account()

        print("\n=======================================================")
        print("  Local Mail Agent: Background Service & Sync Status")
        print("=======================================================")
        print(f"Database:         {storage.db_path}")
        print(f"Service Status:   {(svc.get('status') if svc else 'unknown').upper()}")
        print(f"Last Sync:        {(svc.get('last_sync_at') if svc else None) or checkpoint or 'None'}")
        print(f"Next Sync:        {(svc.get('next_sync_at') if svc else None) or 'Pending next cycle'}")
        print(f"Last Heartbeat:   {(svc.get('heartbeat_at') if svc else None) or 'None'}")
        if svc and svc.get("last_error"):
            print(f"Recent Error:     {svc.get('last_error')} (at {svc.get('last_error_at')})")
        else:
            print("Recent Error:     None")
        print(f"Sync Account:     {account_cp or 'unrecorded'}")
        print("=======================================================\n")
        return 0

    if args.command == "monitor":
        from monitor_service import run_monitor
        return run_monitor(storage=storage, poll_interval_seconds=float(args.interval))

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
