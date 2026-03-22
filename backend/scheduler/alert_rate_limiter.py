"""
Alert Rate Limiter
===================
Enforces the "max 3 alerts per day" rule to prevent inbox spam.

Storage: SQLite table `alert_dispatch_log`
Strategy: Count alerts sent today (UTC date) — if ≥ MAX_ALERTS_PER_DAY, suppress.

Additional de-duplication:
  • Same (ticker, signal_type) within the last 60 minutes → suppress
  • This prevents the same signal from firing 12 times in a row

Public API
----------
  can_send_alert(ticker, signal_type, db) → (bool, reason)
  record_alert_sent(ticker, signal_type, confidence, db) → None
  get_today_count(db) → int
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from core.config import MAX_ALERTS_PER_DAY
from scheduler.alert_log_db import AlertDispatchLog

logger = logging.getLogger(__name__)

# Minimum gap between same ticker+signal (minutes)
DEDUP_WINDOW_MINUTES: int = 60


def get_today_count(db: Session) -> int:
    """Count alerts sent today (UTC calendar day)."""
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return (
        db.query(AlertDispatchLog)
          .filter(AlertDispatchLog.sent_at >= today_start)
          .count()
    )


def can_send_alert(
    ticker:      str,
    signal_type: str,
    db:          Session,
) -> tuple[bool, str]:
    """
    Check rate limit + deduplication.

    Returns
    -------
    (allowed: bool, reason: str)
    """
    # ── Daily cap ─────────────────────────────────────────────────────────────
    daily_count = get_today_count(db)
    if daily_count >= MAX_ALERTS_PER_DAY:
        reason = (
            f"Daily alert limit reached ({daily_count}/{MAX_ALERTS_PER_DAY}). "
            f"Next window opens at 00:00 UTC."
        )
        logger.info("Alert suppressed (daily limit): %s %s", ticker, signal_type)
        return False, reason

    # ── Deduplication window ──────────────────────────────────────────────────
    dedup_cutoff = datetime.now(timezone.utc) - timedelta(minutes=DEDUP_WINDOW_MINUTES)
    recent = (
        db.query(AlertDispatchLog)
          .filter(
              AlertDispatchLog.ticker      == ticker,
              AlertDispatchLog.signal_type == signal_type,
              AlertDispatchLog.sent_at     >= dedup_cutoff,
          )
          .first()
    )
    if recent:
        age_mins = (datetime.now(timezone.utc) - recent.sent_at).seconds // 60
        reason = (
            f"Duplicate suppressed: {ticker} {signal_type} was sent "
            f"{age_mins} min ago (dedup window: {DEDUP_WINDOW_MINUTES} min)"
        )
        logger.info("Alert deduplicated: %s", reason)
        return False, reason

    return True, f"OK — {daily_count + 1}/{MAX_ALERTS_PER_DAY} alerts today"


def record_alert_sent(
    db:          Session,
    ticker:      str,
    signal_type: str,
    confidence:  float,
    regime:      str       = "",
    channel:     str       = "EMAIL",
    subject:     str       = "",
) -> AlertDispatchLog:
    """Persist an alert dispatch record."""
    record = AlertDispatchLog(
        ticker       = ticker,
        signal_type  = signal_type,
        confidence   = confidence,
        regime       = regime,
        channel      = channel,
        subject      = subject,
        sent_at      = datetime.now(timezone.utc),
    )
    try:
        db.add(record)
        db.commit()
        db.refresh(record)
        logger.info(
            "Alert logged: %s %s conf=%.0f%% [%s] (today: %d/%d)",
            ticker, signal_type, confidence, channel,
            get_today_count(db), MAX_ALERTS_PER_DAY,
        )
    except Exception as exc:
        db.rollback()
        logger.error("Failed to log alert: %s", exc)
        raise
    return record
