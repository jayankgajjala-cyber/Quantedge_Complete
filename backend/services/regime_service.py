"""
backend/services/regime_service.py
=====================================
RegimeService: Nifty 50 regime detection with parquet-cached price data.

Classification rules (priority order):
  1. VOLATILE_HIGH_RISK – ATR(14) in top 80th percentile of 252-day range
  2. STRONG_TREND       – ADX > 25 AND Close > EMA(200)
  3. BEAR_CRASHING      – Close < EMA(200) AND 20-day slope < 0
  4. SIDEWAYS           – ADX < 20 AND price inside BB(20,2)

Parquet caching:
  Daily OHLCV is saved to <PARQUET_CACHE_DIR>/{symbol}_daily.parquet.
  Cache is considered stale if older than 1 trading day, preventing
  repeated yfinance calls and respecting rate limits.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf
from scipy import stats as scipy_stats
from sqlalchemy.orm import Session

from backend.core.config import get_settings
from backend.core.database import get_db_context
from backend.models.regime import MarketRegime, MarketRegimeLabel

logger = logging.getLogger(__name__)
cfg    = get_settings()

NIFTY_SYMBOL      = "^NSEI"
LOOKBACK_DAYS     = 200
ATR_PCT_THRESHOLD = 80.0
ADX_STRONG        = 25.0
ADX_WEAK          = 20.0


# ─── Parquet cache helpers ────────────────────────────────────────────────────

def _cache_path(symbol: str) -> Path:
    p = Path(cfg.PARQUET_CACHE_DIR)
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{symbol.replace('^', 'IDX_')}_daily.parquet"


def _is_cache_fresh(path: Path) -> bool:
    """Cache is fresh if the file was written in the last 18 hours."""
    if not path.exists():
        return False
    age = datetime.now().timestamp() - path.stat().st_mtime
    return age < 18 * 3600


def _load_from_cache(symbol: str) -> Optional[pd.DataFrame]:
    path = _cache_path(symbol)
    if _is_cache_fresh(path):
        try:
            df = pd.read_parquet(path)
            logger.info("Parquet cache HIT for %s (%d bars)", symbol, len(df))
            return df
        except Exception as exc:
            logger.warning("Parquet read failed for %s: %s", symbol, exc)
    return None


def _save_to_cache(symbol: str, df: pd.DataFrame) -> None:
    try:
        df.to_parquet(_cache_path(symbol), index=True)
        logger.info("Parquet cache WRITE for %s (%d bars)", symbol, len(df))
    except Exception as exc:
        logger.warning("Parquet write failed for %s: %s", symbol, exc)


# ─── Indicator calculations (vectorised NumPy/Pandas) ─────────────────────────

def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()

def _true_range(df: pd.DataFrame) -> pd.Series:
    hl  = df["High"] - df["Low"]
    hpc = (df["High"] - df["Close"].shift(1)).abs()
    lpc = (df["Low"]  - df["Close"].shift(1)).abs()
    return pd.concat([hl, hpc, lpc], axis=1).max(axis=1)

def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return _true_range(df).ewm(alpha=1/n, adjust=False, min_periods=n).mean()

def _adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    tr   = _true_range(df)
    up   = df["High"].diff()
    down = -df["Low"].diff()
    pdm  = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    mdm  = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    smooth_tr = tr.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    pdi  = 100 * pdm.ewm(alpha=1/n, adjust=False, min_periods=n).mean() / smooth_tr
    mdi  = 100 * mdm.ewm(alpha=1/n, adjust=False, min_periods=n).mean() / smooth_tr
    dx   = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1/n, adjust=False, min_periods=n).mean()

def _bollinger(s: pd.Series, n=20, k=2.0):
    mid = s.rolling(n, min_periods=n).mean()
    std = s.rolling(n, min_periods=n).std(ddof=1)
    return mid + k*std, mid, mid - k*std

def _rolling_slope(s: pd.Series, n: int = 20) -> pd.Series:
    def _slope(arr):
        x = np.arange(len(arr), dtype=float)
        return float(scipy_stats.linregress(x, arr)[0])
    return s.rolling(n, min_periods=n).apply(_slope, raw=True)

def _atr_percentile(atr_s: pd.Series, n: int = 252) -> pd.Series:
    return atr_s.rolling(n, min_periods=n//2).apply(
        lambda a: float(scipy_stats.percentileofscore(a, a[-1])), raw=True
    )


# ─── RegimeService class ──────────────────────────────────────────────────────

class RegimeService:
    """Detects the current Nifty 50 market regime and persists to DB."""

    def __init__(self, symbol: str = NIFTY_SYMBOL):
        self.symbol = symbol
        # Ensure the parquet cache directory exists before any read/write attempt.
        # Uses cfg.PARQUET_CACHE_DIR which defaults to /tmp/parquet (writable on
        # Render/Railway/Fly/Docker without any additional configuration).
        os.makedirs(cfg.PARQUET_CACHE_DIR, exist_ok=True)

    def _fetch_data(self) -> Optional[pd.DataFrame]:
        # Try parquet cache first
        df = _load_from_cache(self.symbol)
        if df is not None:
            return df.tail(LOOKBACK_DAYS + 252)

        try:
            raw = yf.Ticker(self.symbol).history(
                period="2y", interval="1d", auto_adjust=True
            )
            if raw.empty or len(raw) < 50:
                logger.error("Insufficient data for %s", self.symbol)
                return None
            df = raw[["Open","High","Low","Close","Volume"]].rename(columns=str.title)
            df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
            df = df.dropna()
            _save_to_cache(self.symbol, df)
            return df.tail(LOOKBACK_DAYS + 252)
        except Exception as exc:
            logger.error("yfinance fetch failed for %s: %s", self.symbol, exc)
            return None

    def _compute_indicators(self, df: pd.DataFrame) -> dict:
        close     = df["Close"]
        adx_s     = _adx(df, 14)
        atr_s     = _atr(df, 14)
        ema200    = _ema(close, 200)
        slope_s   = _rolling_slope(close, 20)
        atr_pct_s = _atr_percentile(atr_s, 252)
        bb_up, bb_mid, bb_lo = _bollinger(close, 20, 2.0)

        def last(s):
            v = s.dropna()
            return float(v.iloc[-1]) if not v.empty else None

        return {
            "adx_14":      last(adx_s),
            "atr_14":      last(atr_s),
            "atr_pct":     last(atr_pct_s),
            "ema_200":     last(ema200),
            "close":       float(close.iloc[-1]),
            "bb_upper":    last(bb_up),
            "bb_lower":    last(bb_lo),
            "slope_20d":   last(slope_s),
        }

    def _classify(self, ind: dict) -> tuple[MarketRegimeLabel, str, float]:
        adx      = ind.get("adx_14")
        atr_pct  = ind.get("atr_pct")
        ema200   = ind.get("ema_200")
        close    = ind.get("close")
        slope    = ind.get("slope_20d")
        bb_upper = ind.get("bb_upper")
        bb_lower = ind.get("bb_lower")

        if any(v is None for v in [adx, close, ema200]):
            return MarketRegimeLabel.UNKNOWN, "Insufficient indicator data", 0.0

        # Rule 1 — VOLATILE (highest priority)
        if atr_pct and atr_pct >= ATR_PCT_THRESHOLD:
            conf = min(1.0, (atr_pct - ATR_PCT_THRESHOLD) / 20)
            return (MarketRegimeLabel.VOLATILE_HIGH_RISK,
                    f"VOLATILE: ATR pct={atr_pct:.1f}% ≥ {ATR_PCT_THRESHOLD}%. Reduce position sizing.",
                    round(conf, 2))

        # Rule 2 — STRONG TREND
        if adx > ADX_STRONG and close > ema200:
            conf = min(1.0, (adx - ADX_STRONG) / 25)
            return (MarketRegimeLabel.STRONG_TREND,
                    f"STRONG TREND: ADX={adx:.1f} > 25 and Close={close:.0f} > EMA200={ema200:.0f}",
                    round(conf, 2))

        # Rule 3 — BEAR
        if close < ema200 and slope is not None and slope < 0:
            conf = min(1.0, abs(slope) / close * 500)
            return (MarketRegimeLabel.BEAR_CRASHING,
                    f"BEAR: Close={close:.0f} < EMA200={ema200:.0f} and slope={slope:.4f} < 0",
                    round(conf, 2))

        # Rule 4 — SIDEWAYS
        if adx < ADX_WEAK and bb_upper and bb_lower and bb_lower <= close <= bb_upper:
            conf = min(1.0, (ADX_WEAK - adx) / ADX_WEAK)
            return (MarketRegimeLabel.SIDEWAYS,
                    f"SIDEWAYS: ADX={adx:.1f} < 20 and price inside BB [{bb_lower:.0f}, {bb_upper:.0f}]",
                    round(conf, 2))

        return (MarketRegimeLabel.UNKNOWN, f"Mixed signals (ADX={adx:.1f})", 0.3)

    def detect(self) -> Optional[dict]:
        """Run full detection pipeline. Returns result dict or None on data failure."""
        df = self._fetch_data()
        if df is None:
            return None
        ind    = self._compute_indicators(df)
        label, summary, confidence = self._classify(ind)
        pve    = "ABOVE" if (ind.get("close", 0) > (ind.get("ema_200") or 0)) else "BELOW"
        logger.info("Regime: %s (conf=%.2f) | ADX=%.1f Close=%.0f EMA200=%.0f",
                    label.value, confidence, ind.get("adx_14") or 0,
                    ind.get("close") or 0, ind.get("ema_200") or 0)
        return {
            "timestamp":        datetime.utcnow(),
            "index_symbol":     self.symbol,
            "regime_label":     label,
            "adx_14":           ind.get("adx_14"),
            "atr_14":           ind.get("atr_14"),
            "atr_percentile":   ind.get("atr_pct"),
            "ema_200":          ind.get("ema_200"),
            "close_price":      ind.get("close"),
            "bb_upper":         ind.get("bb_upper"),
            "bb_lower":         ind.get("bb_lower"),
            "slope_20d":        ind.get("slope_20d"),
            "price_vs_ema":     pve,
            "regime_summary":   summary,
            "confidence_score": confidence,
        }

    def detect_and_persist(self) -> Optional[MarketRegime]:
        """Run detection and save to DB. Used by APScheduler."""
        result = self.detect()
        if not result:
            return None
        with get_db_context() as db:
            row = MarketRegime(
                timestamp        = result["timestamp"],
                index_symbol     = result["index_symbol"],
                regime_label     = result["regime_label"],
                adx_14           = result.get("adx_14"),
                atr_14           = result.get("atr_14"),
                atr_percentile   = result.get("atr_percentile"),
                ema_200          = result.get("ema_200"),
                close_price      = result.get("close_price"),
                bb_upper         = result.get("bb_upper"),
                bb_lower         = result.get("bb_lower"),
                slope_20d        = result.get("slope_20d"),
                price_vs_ema     = result.get("price_vs_ema"),
                regime_summary   = result.get("regime_summary"),
                confidence_score = result.get("confidence_score"),
            )
            db.add(row)
            db.flush()
            db.refresh(row)
        logger.info("Regime persisted (id=%d label=%s)", row.id, row.regime_label.value)
        return row


# Module-level singleton
_regime_service: Optional[RegimeService] = None

def get_regime_service() -> RegimeService:
    global _regime_service
    if _regime_service is None:
        _regime_service = RegimeService()
    return _regime_service
