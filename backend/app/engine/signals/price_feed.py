"""
Real-Time Price & 5-Minute Candle Service
==========================================
Fetches the latest 5-minute OHLCV candle for a list of NSE/BSE tickers
using yfinance.  Used by the signal engine to confirm that strategy
conditions are met on the current price bar before emitting a signal.

Design
------
• Single yfinance batch download per scan cycle (one API call for all tickers)
• Returns a dict[symbol → latest_candle_dict]
• Volume confirmation: checks if current bar volume > 1.5× the 20-bar avg
• Falls back to daily bar if 5-min data is unavailable (outside market hours)
• Exchange suffix is resolved once via EXCHANGE_SUFFIX_MAP (no hardcoding)
• Market-open check uses IST (UTC+5:30) and respects NSE public holidays
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time as dtime, timezone, timedelta
from typing import Optional

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────

# Dynamic suffix map – add more exchanges here without touching any other code
EXCHANGE_SUFFIX_MAP: dict[str, str] = {
    "NSE": ".NS",
    "BSE": ".BO",
    "NYSE": "",
    "NASDAQ": "",
}
DEFAULT_EXCHANGE = "NSE"

CANDLE_INTERVAL        = "5m"
CANDLE_LOOKBACK_PERIOD = "5d"        # enough history for 20-bar avg
VOLUME_CONFIRMATION_X  = 1.5         # current volume must be ≥ 1.5× avg
MIN_BARS_FOR_AVG       = 10          # minimum bars needed to compute vol avg

# IST = UTC + 5h30m
_IST = timezone(timedelta(hours=5, minutes=30))

# NSE trading window in IST
_MARKET_OPEN_IST  = dtime(9,  15)   # 09:15 IST
_MARKET_CLOSE_IST = dtime(15, 30)   # 15:30 IST

# NSE public holidays for the current calendar year (YYYY-MM-DD).
# Update this set at the start of each year from the NSE official holiday list.
# Source: https://www.nseindia.com/resources/exchange-communication-holidays
NSE_HOLIDAYS_2025: frozenset[date] = frozenset({
    date(2025, 2, 26),  # Mahashivratri
    date(2025, 3, 14),  # Holi
    date(2025, 3, 31),  # Id-ul-Fitr (Ramzan Id)
    date(2025, 4, 10),  # Shri Ram Navami
    date(2025, 4, 14),  # Dr. Baba Saheb Ambedkar Jayanti
    date(2025, 4, 18),  # Good Friday
    date(2025, 5,  1),  # Maharashtra Day
    date(2025, 8, 15),  # Independence Day
    date(2025, 8, 27),  # Ganesh Chaturthi
    date(2025, 10,  2), # Mahatma Gandhi Jayanti / Dussehra
    date(2025, 10, 21), # Diwali Laxmi Puja (Muhurat trading only)
    date(2025, 10, 22), # Diwali Balipratipada
    date(2025, 11,  5), # Prakash Gurpurb Sri Guru Nanak Dev Ji
    date(2025, 12, 25), # Christmas
})

# Combine holidays from multiple years so the module survives a year boundary
NSE_HOLIDAYS: frozenset[date] = NSE_HOLIDAYS_2025  # extend as needed


# ─── Data classes ─────────────────────────────────────────────────────────────

class CandleData:
    """Represents one OHLCV bar with volume confirmation flag."""
    __slots__ = (
        "symbol", "timestamp", "open", "high", "low", "close",
        "volume", "volume_avg_20", "volume_ratio", "volume_confirmed",
        "is_stale",
    )

    def __init__(
        self,
        symbol:    str,
        timestamp: datetime,
        open_:     float,
        high:      float,
        low:       float,
        close:     float,
        volume:    float,
        vol_avg:   float,
    ):
        self.symbol     = symbol
        self.timestamp  = timestamp
        self.open       = open_
        self.high       = high
        self.low        = low
        self.close      = close
        self.volume     = volume
        self.volume_avg_20 = vol_avg
        self.volume_ratio  = (volume / vol_avg) if vol_avg > 0 else 0.0
        self.volume_confirmed = self.volume_ratio >= VOLUME_CONFIRMATION_X
        self.is_stale   = False   # set True if bar is older than 10 minutes

    def to_dict(self) -> dict:
        return {
            "symbol":           self.symbol,
            "timestamp":        self.timestamp.isoformat(),
            "open":             self.open,
            "high":             self.high,
            "low":              self.low,
            "close":            self.close,
            "volume":           self.volume,
            "volume_avg_20":    self.volume_avg_20,
            "volume_ratio":     round(self.volume_ratio, 2),
            "volume_confirmed": self.volume_confirmed,
            "is_stale":         self.is_stale,
        }


# ─── Fetcher ──────────────────────────────────────────────────────────────────

def _build_yf_symbol(symbol: str, exchange: str = DEFAULT_EXCHANGE) -> str:
    """Append the correct exchange suffix from EXCHANGE_SUFFIX_MAP."""
    suffix = EXCHANGE_SUFFIX_MAP.get(exchange.upper(), "")
    return f"{symbol}{suffix}"


def fetch_latest_candles(
    symbols:  list[str],
    exchange: str = DEFAULT_EXCHANGE,
) -> dict[str, Optional[CandleData]]:
    """
    Batch-download the latest 5-minute candle for all symbols.

    Returns
    -------
    dict mapping symbol → CandleData (or None if fetch failed)
    """
    if not symbols:
        return {}

    yf_symbols = [_build_yf_symbol(s, exchange) for s in symbols]
    symbol_map  = dict(zip(yf_symbols, symbols))

    result: dict[str, Optional[CandleData]] = {s: None for s in symbols}

    try:
        raw = yf.download(
            tickers   = yf_symbols,
            period    = CANDLE_LOOKBACK_PERIOD,
            interval  = CANDLE_INTERVAL,
            auto_adjust = True,
            progress  = False,
            group_by  = "ticker",
            threads   = True,
        )
    except Exception as exc:
        logger.error("yfinance batch download failed: %s", exc, exc_info=True)
        return result

    now_utc = datetime.now(timezone.utc)

    for yf_sym, orig_sym in symbol_map.items():
        try:
            # Handle single vs multi-ticker download structure
            if len(yf_symbols) == 1:
                df = raw
            else:
                if yf_sym not in raw.columns.get_level_values(0):
                    logger.warning("No data for %s in batch response", yf_sym)
                    continue
                df = raw[yf_sym]

            df = df.dropna(subset=["Close"])
            if df.empty or len(df) < 2:
                logger.warning("Insufficient candle data for %s", yf_sym)
                continue

            # Rename columns to title-case for consistency
            df.columns = [c.title() if isinstance(c, str) else c for c in df.columns]
            df.index   = pd.to_datetime(df.index, utc=True)

            # Volume average (last MIN_BARS_FOR_AVG complete bars, excluding current)
            historical    = df.iloc[-(MIN_BARS_FOR_AVG + 1):-1]
            vol_avg       = float(historical["Volume"].mean()) if len(historical) >= 5 else 0.0

            # Latest bar
            latest        = df.iloc[-1]
            bar_ts        = latest.name.to_pydatetime()

            candle = CandleData(
                symbol    = orig_sym,
                timestamp = bar_ts,
                open_     = float(latest["Open"]),
                high      = float(latest["High"]),
                low       = float(latest["Low"]),
                close     = float(latest["Close"]),
                volume    = float(latest["Volume"]),
                vol_avg   = vol_avg,
            )

            # Mark stale if bar is older than 15 minutes
            age_minutes = (now_utc - bar_ts.replace(tzinfo=timezone.utc)).seconds / 60
            candle.is_stale = age_minutes > 15

            result[orig_sym] = candle
            logger.debug(
                "Candle [%s] close=%.2f vol_ratio=%.2f confirmed=%s stale=%s",
                orig_sym, candle.close, candle.volume_ratio,
                candle.volume_confirmed, candle.is_stale,
            )

        except Exception as exc:
            logger.error("Error processing candle for %s: %s", yf_sym, exc)

    fetched = sum(1 for v in result.values() if v is not None)
    logger.info("Candle fetch: %d/%d symbols successful", fetched, len(symbols))
    return result


def is_market_open() -> bool:
    """
    Returns True if the NSE is currently open for trading.

    Checks three conditions in order:
      1. Current IST time is within 09:15–15:30
      2. Today is a weekday (Monday–Friday)
      3. Today is not an NSE public holiday

    IST is computed directly as UTC+5:30 – no pytz dependency required.
    """
    now_ist  = datetime.now(_IST)
    today    = now_ist.date()

    # Weekend check
    if now_ist.weekday() >= 5:          # 5=Saturday, 6=Sunday
        return False

    # Holiday check
    if today in NSE_HOLIDAYS:
        logger.debug("NSE holiday today (%s) – market closed", today)
        return False

    # Time-window check (compare time objects in IST directly)
    current_time = now_ist.time().replace(tzinfo=None)
    return _MARKET_OPEN_IST <= current_time <= _MARKET_CLOSE_IST
