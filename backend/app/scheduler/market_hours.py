"""
Market Hours Utility
=====================
Determines whether the NSE market is currently open.

NSE trading hours (IST = UTC + 5:30):
  Monday–Friday: 09:15 – 15:30 IST
  Closed: Saturdays, Sundays, NSE holidays

This module provides:
  is_market_open()         → bool
  is_market_day()          → bool (weekday check only)
  next_market_open_utc()   → datetime
  time_until_open_secs()   → float
  MarketSession context

The heartbeat scheduler calls is_market_open() to decide whether to
run the 5-minute job. Outside hours, the job is skipped gracefully.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc

# NSE trading hours in IST
_MARKET_OPEN  = time(9, 15)
_MARKET_CLOSE = time(15, 30)

# NSE holidays 2024–2025 (add more as needed)
# Format: (month, day)
NSE_HOLIDAYS: set[tuple[int, int]] = {
    (1, 22),   # Ram Mandir Prana Pratishtha 2024
    (1, 26),   # Republic Day
    (3, 25),   # Holi
    (3, 29),   # Good Friday
    (4, 14),   # Dr. Ambedkar Jayanti / Ram Navami
    (4, 17),   # Ram Navami
    (4, 21),   # Ram Navami (alt)
    (5, 23),   # Buddha Purnima
    (6, 17),   # Eid-ul-Adha
    (7, 17),   # Muharram
    (8, 15),   # Independence Day
    (10, 2),   # Gandhi Jayanti / Mahatma Gandhi Jayanti
    (11, 1),   # Diwali Laxmi Puja
    (11, 15),  # Gurunanak Jayanti
    (12, 25),  # Christmas
}


def _ist_now() -> datetime:
    return datetime.now(IST)


def is_market_day(dt: datetime | None = None) -> bool:
    """
    Returns True if *dt* (default: now IST) is a weekday
    that is not a listed NSE holiday.
    """
    dt_ist = (dt or _ist_now()).astimezone(IST)
    # Monday=0 … Friday=4
    if dt_ist.weekday() > 4:
        return False
    if (dt_ist.month, dt_ist.day) in NSE_HOLIDAYS:
        logger.debug("NSE holiday: %s-%02d", dt_ist.month, dt_ist.day)
        return False
    return True


def is_market_open(dt: datetime | None = None) -> bool:
    """
    Returns True if the market is currently open
    (weekday, not holiday, between 09:15 and 15:30 IST).
    """
    dt_ist = (dt or _ist_now()).astimezone(IST)
    if not is_market_day(dt_ist):
        return False
    current_time = dt_ist.time()
    return _MARKET_OPEN <= current_time <= _MARKET_CLOSE


def minutes_until_close() -> float:
    """Returns minutes remaining until market close. Negative if already closed."""
    now = _ist_now()
    close_today = now.replace(
        hour=_MARKET_CLOSE.hour, minute=_MARKET_CLOSE.minute, second=0, microsecond=0
    )
    return (close_today - now).total_seconds() / 60


def minutes_since_open() -> float:
    """Returns minutes since market opened today. Negative if not opened yet."""
    now = _ist_now()
    open_today = now.replace(
        hour=_MARKET_OPEN.hour, minute=_MARKET_OPEN.minute, second=0, microsecond=0
    )
    return (now - open_today).total_seconds() / 60


def next_market_open_utc() -> datetime:
    """
    Returns the UTC datetime of the next NSE market open.
    Skips weekends and known holidays.
    """
    candidate = _ist_now()
    candidate = candidate.replace(
        hour=_MARKET_OPEN.hour, minute=_MARKET_OPEN.minute, second=0, microsecond=0
    )
    # If we're past today's open, start from tomorrow
    if _ist_now() >= candidate:
        candidate += timedelta(days=1)

    # Walk forward until we find a valid market day
    for _ in range(14):   # safety: never loop > 2 weeks
        if is_market_day(candidate):
            return candidate.astimezone(UTC)
        candidate += timedelta(days=1)

    # Fallback: 9:15 IST next Monday
    return candidate.astimezone(UTC)


def market_status_summary() -> dict:
    """Return a dict describing current market status."""
    open_flag = is_market_open()
    ist_now   = _ist_now()
    return {
        "is_open":          open_flag,
        "is_market_day":    is_market_day(),
        "ist_time":         ist_now.strftime("%H:%M IST"),
        "weekday":          ist_now.strftime("%A"),
        "minutes_to_close": round(minutes_until_close(), 1) if open_flag else None,
        "next_open_utc":    next_market_open_utc().isoformat() if not open_flag else None,
    }
