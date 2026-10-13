"""Scheduled job script to generate, save, and notify the daily email digest."""

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from pathlib import Path
import sys
from typing import Any

from digest import build_daily_digest, save_digest_markdown, send_digest_toast
from storage import EmailStorage


@dataclass(frozen=True)
class DigestJobReport:
    success: bool
    generated_at: str
    saved_path: Path | None
    toast_delivered: bool
    error: str | None = None


def configure_job_logging(log_dir: str | Path = "logs") -> logging.Logger:
    """Set up safe logging to file and console without leaking sensitive data."""
    logs_path = Path(log_dir)
    logs_path.mkdir(parents=True, exist_ok=True)
    log_file = logs_path / "digest_job.log"

    job_logger = logging.getLogger("digest_job")
    job_logger.setLevel(logging.INFO)

    # Avoid adding duplicate handlers if reconfigured
    if not job_logger.handlers:
        file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
        file_handler.setLevel(logging.INFO)
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )
        file_handler.setFormatter(formatter)
        job_logger.addHandler(file_handler)

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        job_logger.addHandler(console_handler)

    return job_logger


def run_digest_job(
    *,
    storage: EmailStorage | None = None,
    output_dir: str | Path = "digests",
    log_dir: str | Path = "logs",
    enable_toast: bool = True,
    notifier: Any | None = None,
) -> DigestJobReport:
    """Execute the scheduled digest generation, markdown archiving, and optional toast."""
    job_logger = configure_job_logging(log_dir)
    now_iso = datetime.now(timezone.utc).isoformat()
    job_logger.info("Daily Digest scheduled job started.")

    try:
        database = storage or EmailStorage()
        digest = build_daily_digest(storage=database)

        # 1. Save Markdown report (primary durable output)
        saved_path = save_digest_markdown(digest, output_dir=output_dir)
        job_logger.info(
            "Daily Digest successfully generated and saved to %s",
            saved_path,
        )

        # 2. Desktop notification reminder (best-effort)
        toast_delivered = False
        if enable_toast:
            try:
                toast_delivered = send_digest_toast(digest, notifier=notifier)
                job_logger.info(
                    "Desktop reminder toast delivery: %s",
                    "Success" if toast_delivered else "Unavailable / Failed",
                )
            except Exception as exc:
                job_logger.warning(
                    "Toast notification exception encountered (%s); report was safely preserved",
                    type(exc).__name__,
                )

        h = digest.health_stats
        job_logger.info(
            "Digest summary: %d deadlines, %d opportunities, %d awaiting action; "
            "Total messages=%d, Checkpoint=%s",
            len(digest.upcoming_deadlines),
            len(digest.new_opportunities),
            len(digest.awaiting_action),
            h.total_emails,
            h.last_sync_at or "None",
        )

        return DigestJobReport(
            success=True,
            generated_at=digest.generated_at,
            saved_path=saved_path,
            toast_delivered=toast_delivered,
            error=None,
        )

    except Exception as exc:
        job_logger.error(
            "Daily Digest job failed with error (%s)",
            type(exc).__name__,
        )
        return DigestJobReport(
            success=False,
            generated_at=now_iso,
            saved_path=None,
            toast_delivered=False,
            error=type(exc).__name__,
        )


def main() -> int:
    report = run_digest_job()
    return 0 if report.success else 1


if __name__ == "__main__":
    sys.exit(main())
