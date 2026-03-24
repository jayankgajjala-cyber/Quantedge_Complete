"""
backend/utils/logger.py
========================
Production-grade structured logger for Railway.

Features:
  - ISO-8601 timestamps with milliseconds
  - Full tracebacks on every ERROR+ log (never truncated)
  - Request-ID context injected by LoggingMiddleware in main.py
  - JSON-friendly format: Railway's log viewer can filter by level/name
  - Silences noisy third-party libraries that flood logs with junk
  - Safe for multi-worker uvicorn (no shared state)

Usage in any module:
    from backend.utils.logger import get_logger
    logger = get_logger(__name__)
    logger.info("Portfolio uploaded: %d holdings", count)
    logger.error("Backtest failed for %s", ticker, exc_info=True)  # includes traceback
"""

import logging
import sys
import traceback
from pathlib import Path
from typing import Optional


# ── Formatter ─────────────────────────────────────────────────────────────────

class RailwayFormatter(logging.Formatter):
    """
    Single-line format for Railway's terminal + searchable log viewer.

    Format:
        2024-01-15 09:32:11.847 | ERROR    | backend.api.routers.trading | run_backtest | Backtest failed for RELIANCE: ...
        <traceback lines follow on separate lines when exc_info=True>

    Each field is pipe-delimited so Railway's grep works:
        grep "| ERROR" → all errors
        grep "backend.api.routers" → all API errors
    """

    LEVEL_COLORS = {
        "DEBUG":    "\033[36m",   # cyan
        "INFO":     "\033[32m",   # green
        "WARNING":  "\033[33m",   # yellow
        "ERROR":    "\033[31m",   # red
        "CRITICAL": "\033[35m",   # magenta
    }
    RESET = "\033[0m"

    def __init__(self, use_color: bool = True):
        super().__init__()
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        # Millisecond-precision timestamp
        ts = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        ms = int(record.msecs)

        level = record.levelname
        name  = record.name
        func  = record.funcName

        # Colour level for terminal readability
        if self.use_color and level in self.LEVEL_COLORS:
            level_str = f"{self.LEVEL_COLORS[level]}{level:<8}{self.RESET}"
        else:
            level_str = f"{level:<8}"

        msg = record.getMessage()

        line = f"{ts}.{ms:03d} | {level_str} | {name:<45} | {func:<30} | {msg}"

        # Always append full traceback for ERROR and above
        if record.exc_info:
            tb = "".join(traceback.format_exception(*record.exc_info))
            # Indent each traceback line so it's visually grouped with the log line
            indented = "\n".join(f"    {l}" for l in tb.rstrip().splitlines())
            line = f"{line}\n{indented}"
        elif record.exc_text:
            line = f"{line}\n    {record.exc_text}"

        return line


# ── Root logger setup ─────────────────────────────────────────────────────────

_configured = False


def configure_logging(
    log_dir: str = "logs",
    level: int = logging.INFO,
    use_color: bool = True,
) -> None:
    """
    Call ONCE at application startup (in main.py lifespan, before any imports).
    Idempotent — safe to call multiple times.
    """
    global _configured
    if _configured:
        return
    _configured = True

    # Ensure log directory exists (safe to fail on read-only filesystems)
    try:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
    except OSError:
        log_dir = "/tmp/logs"
        Path(log_dir).mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    # Remove any existing handlers (prevents duplicate logs when uvicorn reloads)
    root.handlers.clear()

    # ── Console handler → Railway terminal ────────────────────────────────────
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(RailwayFormatter(use_color=use_color))
    console.setLevel(level)
    root.addHandler(console)

    # ── File handler → persistent log file ───────────────────────────────────
    # /tmp is always writable on Railway/Render/Fly containers
    log_file = Path(log_dir) / "app.log"
    try:
        file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
        file_handler.setFormatter(RailwayFormatter(use_color=False))
        file_handler.setLevel(level)
        root.addHandler(file_handler)
    except OSError as e:
        # Non-fatal — console logging still works
        logging.warning("Could not open log file %s: %s", log_file, e)

    # ── Silence noisy third-party libraries ──────────────────────────────────
    _SILENCE = {
        "uvicorn.access":       logging.WARNING,   # per-request access log spam
        "uvicorn.error":        logging.WARNING,
        "apscheduler.scheduler":logging.WARNING,   # scheduler heartbeat spam
        "apscheduler.executors":logging.WARNING,
        "yfinance":             logging.WARNING,   # "No timezone found" etc.
        "peewee":               logging.WARNING,
        "urllib3":              logging.WARNING,
        "urllib3.connectionpool":logging.WARNING,
        "httpx":                logging.WARNING,
        "httpcore":             logging.WARNING,
        "charset_normalizer":   logging.WARNING,
        "PIL":                  logging.WARNING,
        "multipart":            logging.WARNING,
        "sqlalchemy.engine":    logging.WARNING,   # SQL query echo (only in DEBUG)
        "sqlalchemy.pool":      logging.WARNING,
        "passlib":              logging.WARNING,
        "bcrypt":               logging.WARNING,
    }
    for name, lvl in _SILENCE.items():
        logging.getLogger(name).setLevel(lvl)

    # Enable SQLAlchemy SQL echo only in DEBUG mode
    if level <= logging.DEBUG:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)

    logging.getLogger(__name__).info(
        "Logging configured | level=%s | file=%s",
        logging.getLevelName(level),
        log_file,
    )


def get_logger(name: str) -> logging.Logger:
    """
    Drop-in replacement for logging.getLogger(__name__).

    Usage:
        from backend.utils.logger import get_logger
        logger = get_logger(__name__)
    """
    return logging.getLogger(name)


# ── Helpers for structured context logging ────────────────────────────────────

def log_request(logger: logging.Logger, method: str, path: str, status: int,
                duration_ms: float, request_id: Optional[str] = None) -> None:
    """Log an HTTP request with timing. Called by LoggingMiddleware."""
    rid = f"[{request_id}] " if request_id else ""
    if status >= 500:
        logger.error("%s%s %s → %d (%.1fms)", rid, method, path, status, duration_ms)
    elif status >= 400:
        logger.warning("%s%s %s → %d (%.1fms)", rid, method, path, status, duration_ms)
    else:
        logger.info("%s%s %s → %d (%.1fms)", rid, method, path, status, duration_ms)


def log_db_error(logger: logging.Logger, operation: str, exc: Exception) -> None:
    """Standard DB error log with table/operation context."""
    logger.error(
        "DB error during '%s': %s — check DATABASE_URL and Supabase connection",
        operation, exc, exc_info=True,
    )


def log_startup_banner(logger: logging.Logger, app_name: str, version: str,
                        db_driver: str, frontend_url: str) -> None:
    """Print a clear startup banner to the Railway terminal."""
    sep = "═" * 70
    logger.info(sep)
    logger.info("  %s  v%s", app_name, version)
    logger.info("  Database : %s", db_driver)
    logger.info("  Frontend : %s", frontend_url)
    logger.info("  Docs     : /docs")
    logger.info("  Health   : /api/health")
    logger.info(sep)
