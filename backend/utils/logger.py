"""
backend/utils/logger.py
========================
Centralized logging configuration for the Quantedge backend.

Usage (in every module):
    from backend.utils.logger import get_logger
    logger = get_logger(__name__)

Design:
  - Single call to setup_logging() at startup wires the root logger.
  - get_logger() is a thin wrapper around logging.getLogger() so every
    module automatically inherits the root config — format, level, handlers.
  - Format includes timestamp, level, module name, and message so that
    Railway log lines are self-contained and greppable without opening a
    separate file.
  - exc_info=True on every logger.error / logger.critical call guarantees
    the full traceback (file, line number, exception type) appears in the
    Railway console — no more "Something went wrong" mysteries.
  - Noisy third-party loggers are silenced to WARNING so real errors
    aren't buried in yfinance/urllib3/apscheduler noise.
"""

import logging
import sys
from pathlib import Path


# ── Format ────────────────────────────────────────────────────────────────────
# [2024-03-15 14:32:01] [ERROR   ] [backend.api.routers.dashboard]: <message>
#  ^─ timestamp          ^─ level   ^─ module path (dotted)           ^─ body
LOG_FORMAT  = "[%(asctime)s] [%(levelname)-8s] [%(name)s]: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(log_dir: str = "logs", level: int = logging.INFO) -> None:
    """
    Wire the root logger with console + rotating file handlers.

    Call exactly once at application startup (main.py) before any other
    module is imported so every subsequent getLogger(__name__) call
    inherits this config automatically.

    Args:
        log_dir: Directory for the app.log file (created if absent).
        level:   Root log level. Pass logging.DEBUG for verbose output,
                 logging.INFO for production.
    """
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    root = logging.getLogger()

    # Guard: don't add duplicate handlers on reload (e.g. uvicorn --reload)
    if root.handlers:
        root.handlers.clear()

    root.setLevel(level)

    # ── Console handler (stdout → Railway log stream) ─────────────────────────
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)
    root.addHandler(console_handler)

    # ── File handler (persistent across restarts in local dev) ────────────────
    try:
        file_handler = logging.FileHandler(
            Path(log_dir) / "app.log", encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        root.addHandler(file_handler)
    except OSError as exc:
        # Non-fatal: file logging unavailable in read-only FS environments
        logging.getLogger(__name__).warning(
            "File logging unavailable (%s) — console only", exc
        )

    # ── Silence noisy third-party loggers ─────────────────────────────────────
    # These emit INFO/DEBUG at very high frequency and bury real errors.
    _noisy = (
        "uvicorn.access",          # HTTP access log (every request)
        "apscheduler.scheduler",   # job scheduling heartbeat
        "apscheduler.executors",   # thread pool executor noise
        "yfinance",                # data fetch internals
        "urllib3",                 # HTTP connection pool
        "httpx",                   # async HTTP client
        "httpcore",                # httpx transport layer
        "multipart",               # file upload parsing
        "passlib",                 # bcrypt hashing noise
    )
    for name in _noisy:
        logging.getLogger(name).setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "Logging configured — level=%s format='%s'",
        logging.getLevelName(level),
        LOG_FORMAT,
    )


def get_logger(name: str) -> logging.Logger:
    """
    Return a logger for the given module name.

    This is a thin wrapper around logging.getLogger(name). Its value is
    that it acts as a single import point — modules never need to import
    both `logging` and `utils.logger`; just `from backend.utils.logger
    import get_logger`.

    Always call with `get_logger(__name__)` so the logger name reflects
    the module path (e.g. 'backend.api.routers.dashboard') and log lines
    are easily greppable in Railway.

    Args:
        name: Typically __name__ of the calling module.

    Returns:
        A standard logging.Logger instance.
    """
    return logging.getLogger(name)
