"""
backend/core/logging_config.py
================================
Configures structured logging for the entire application.
All modules must use logging.getLogger(__name__) — never print().
"""

import logging
import sys
from pathlib import Path


def configure_logging(log_dir: str = "logs", level: int = logging.INFO) -> None:
    """
    Set up console + rotating file handlers.
    Call once at application startup before any modules are imported.
    """
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)-40s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    ch.setLevel(level)
    root.addHandler(ch)

    # File handler
    fh = logging.FileHandler(f"{log_dir}/app.log", encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(level)
    root.addHandler(fh)

    # Silence noisy third-party loggers
    for noisy in ("uvicorn.access", "apscheduler.scheduler",
                  "yfinance", "urllib3", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger(__name__).info("Logging configured (level=%s)", logging.getLevelName(level))
