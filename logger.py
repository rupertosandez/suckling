"""
Lightweight error logging to a rotating file.

Errors and warnings go to `data/bot.log`. Console output is unchanged
(other modules' print() calls keep working as before).
"""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import config


def setup_logging() -> None:
    """Configure file logging for errors. Call once at startup."""
    Path(config.LOG_PATH).parent.mkdir(parents=True, exist_ok=True)

    # Rotating file: 1 MB max per file, keep 3 backups
    file_handler = RotatingFileHandler(
        config.LOG_PATH,
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.WARNING)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    # Attach to the root logger so all module logs flow through
    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    root.addHandler(file_handler)

    # plexapi logs one ERROR line per candidate URI (LAN/WAN/relay) it tries
    # internally before we ever see the final exception, which floods the log
    # every time the home Plex server is briefly unreachable. Our own
    # plex.py catches that failure and logs a single WARNING summary instead,
    # so silence plexapi's internal per-URI noise here.
    logging.getLogger("plexapi").setLevel(logging.CRITICAL)


def log_exception(source: str, exc: BaseException) -> None:
    """Log an exception with full traceback, tagged with a source name."""
    logging.getLogger(source).exception("Unhandled exception: %s", exc)


def log_warning(source: str, message: str) -> None:
    """Log an expected/transient failure without a traceback, at WARNING."""
    logging.getLogger(source).warning(message)
