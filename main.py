import logging
import signal
import sys
from threading import Event

from scheduler import run_forever


def main() -> None:
    cli_triggers = {"list", "sync", "classify", "reanalyze", "ack", "show", "digest", "app", "serve", "desktop", "auth", "monitor", "status", "--all", "--help", "-h", "--db"}
    if len(sys.argv) > 1 and (sys.argv[1] in cli_triggers or sys.argv[1].startswith("--db")):
        import cli
        sys.exit(cli.main(sys.argv[1:]))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    stop_event = Event()

    def request_shutdown(signum, frame) -> None:
        logging.getLogger(__name__).info(
            "Shutdown requested; finishing the current cycle"
        )
        stop_event.set()

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)

    run_forever(
        poll_interval_seconds=60 * 60,
        stop_event=stop_event,
        align_to_hour=True,
    )


if __name__ == "__main__":
    main()
