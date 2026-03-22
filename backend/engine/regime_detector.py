"""
Market Regime Detector
========================
Analyses Nifty 50 (^NSEI) over the last 200 trading days and classifies
the market into one of four regimes:

    STRONG_TREND       ADX > 25  AND  Close > EMA(200)
    VOLATILE_HIGH_RISK ATR(14) in top 80th percentile of 1-yr range
    SIDEWAYS           ADX < 20  AND  price oscillating inside BB(20, 2)
    BEAR_CRASHING      Close < EMA(200)  AND  20-day slope < 0

The scheduler calls `detect_and_persist()` every 5 minutes.
Results are written to the `market_regime` SQLite table.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.orm import Session

from engine.indicators.technical import (
    adx as calc_adx,
    atr as calc_atr,
    atr_percentile,
    bollinger_bands,
    ema as calc_ema,
    rolling_slope,
)
from models.database import MarketRegime, MarketRegimeLabel
from models.session import SessionLocal

logger = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────

NIFTY_SYMBOL      = "^NSEI"
LOOKBACK_DAYS     = 200          # bars used for indicator calculations
ATR_PCT_THRESHOLD = 80.0         # top 80th percentile → VOLATILE
ADX_STRONG        = 25.0         # ADX > 25 → potential STRONG_TREND
ADX_WEAK          = 20.0         # ADX < 20 → potential SIDEWAYS
POLL_INTERVAL_SEC = 300          # 5 minutes


# ─── Regime classification logic ──────────────────────────────────────────────

class RegimeDetector:
    """
    Downloads Nifty 50 daily data and classifies the current market regime.

    Usage
    -----
    detector = RegimeDetector()
    result   = detector.detect()
    # result is a dict with regime_label, indicators, summary, confidence
    """

    def __init__(
        self,
        symbol:        str   = NIFTY_SYMBOL,
        lookback_days: int   = LOOKBACK_DAYS,
    ):
        self.symbol        = symbol
        self.lookback_days = lookback_days

    # ── Data fetch ────────────────────────────────────────────────────────────

    def _fetch_data(self) -> Optional[pd.DataFrame]:
        """Download enough bars to compute all indicators reliably."""
        # Fetch extra days to account for weekends/holidays
        fetch_days = max(self.lookback_days * 2, 600)
        try:
            ticker = yf.Ticker(self.symbol)
            df     = ticker.history(
                period=f"{fetch_days}d",
                interval="1d",
                auto_adjust=True,
            )
            if df.empty or len(df) < 50:
                logger.error("Insufficient data for %s: %d bars", self.symbol, len(df))
                return None

            df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
            df = df.rename(columns=str.title)
            df.index = pd.to_datetime(df.index, utc=True)
            logger.info("Fetched %d bars for regime detection (%s)", len(df), self.symbol)
            return df.tail(self.lookback_days + 252)   # include full ATR pct lookback

        except Exception as exc:
            logger.error("Failed to fetch %s for regime detection: %s", self.symbol, exc)
            return None

    # ── Indicator computation ─────────────────────────────────────────────────

    def _compute_indicators(self, df: pd.DataFrame) -> dict:
        """Return a dict of scalar indicator values (last row)."""
        close = df["Close"]

        adx_series   = calc_adx(df, 14)
        atr_series   = calc_atr(df, 14)
        ema200_series= calc_ema(close, 200)
        slope_series = rolling_slope(close, 20)
        atr_pct_s    = atr_percentile(atr_series, 252)
        bb_up, bb_mid, bb_lo = bollinger_bands(close, 20, 2.0)

        def last(s: pd.Series) -> Optional[float]:
            v = s.dropna()
            return float(v.iloc[-1]) if not v.empty else None

        return {
            "adx_14":       last(adx_series),
            "atr_14":       last(atr_series),
            "atr_pct":      last(atr_pct_s),
            "ema_200":      last(ema200_series),
            "close":        float(close.iloc[-1]),
            "bb_upper":     last(bb_up),
            "bb_lower":     last(bb_lo),
            "slope_20d":    last(slope_series),
        }

    # ── Regime classification ─────────────────────────────────────────────────

    def _classify(self, ind: dict) -> tuple[MarketRegimeLabel, str, float]:
        """
        Apply the four regime rules in priority order.
        Returns (label, human_summary, confidence_0_to_1).
        """
        adx      = ind.get("adx_14")
        atr_pct  = ind.get("atr_pct")
        ema200   = ind.get("ema_200")
        close    = ind.get("close")
        bb_upper = ind.get("bb_upper")
        bb_lower = ind.get("bb_lower")
        slope    = ind.get("slope_20d")

        if any(v is None for v in [adx, close, ema200]):
            return MarketRegimeLabel.UNKNOWN, "Insufficient indicator data", 0.0

        # ── Rule 1: VOLATILE / HIGH RISK (checked first – overrides everything) ─
        if atr_pct is not None and atr_pct >= ATR_PCT_THRESHOLD:
            confidence = min(1.0, (atr_pct - ATR_PCT_THRESHOLD) / 20)
            summary = (
                f"VOLATILE/HIGH RISK: ATR percentile={atr_pct:.1f}% "
                f"(≥ {ATR_PCT_THRESHOLD}%). Market is in elevated-volatility regime. "
                f"Position sizing should be reduced."
            )
            return MarketRegimeLabel.VOLATILE_HIGH_RISK, summary, round(confidence, 2)

        # ── Rule 2: STRONG TREND ─────────────────────────────────────────────
        if adx > ADX_STRONG and close > ema200:
            confidence = min(1.0, (adx - ADX_STRONG) / 25)
            summary = (
                f"STRONG TREND: ADX={adx:.1f} (>{ADX_STRONG}) "
                f"and Price={close:.0f} > EMA(200)={ema200:.0f}. "
                f"Trend-following strategies are favoured."
            )
            return MarketRegimeLabel.STRONG_TREND, summary, round(confidence, 2)

        # ── Rule 3: BEAR / CRASHING ───────────────────────────────────────────
        if close < ema200 and slope is not None and slope < 0:
            confidence = min(1.0, abs(slope) / close * 100 * 5)
            summary = (
                f"BEAR/CRASHING: Price={close:.0f} < EMA(200)={ema200:.0f} "
                f"and 20-day slope={slope:.4f} (negative). "
                f"Defensive positioning recommended."
            )
            return MarketRegimeLabel.BEAR_CRASHING, summary, round(confidence, 2)

        # ── Rule 4: SIDEWAYS / MEAN REVERTING ─────────────────────────────────
        if adx < ADX_WEAK:
            inside_bb = (
                bb_upper is not None and bb_lower is not None
                and bb_lower <= close <= bb_upper
            )
            if inside_bb:
                confidence = min(1.0, (ADX_WEAK - adx) / ADX_WEAK)
                summary = (
                    f"SIDEWAYS/MEAN REVERTING: ADX={adx:.1f} (<{ADX_WEAK}) "
                    f"and price oscillating between BB [{bb_lower:.0f}, {bb_upper:.0f}]. "
                    f"Mean-reversion strategies are favoured."
                )
                return MarketRegimeLabel.SIDEWAYS, summary, round(confidence, 2)

        # Fallback – mixed signals
        summary = (
            f"MIXED signals: ADX={adx:.1f}, Close={close:.0f}, EMA200={ema200:.0f}, "
            f"Slope={slope:.4f if slope else 'N/A'}. No dominant regime."
        )
        return MarketRegimeLabel.UNKNOWN, summary, 0.3

    # ── Public detect() ───────────────────────────────────────────────────────

    def detect(self) -> Optional[dict]:
        """
        Full pipeline: fetch → compute → classify.
        Returns a result dict or None on data failure.
        """
        df = self._fetch_data()
        if df is None:
            return None

        ind = self._compute_indicators(df)
        label, summary, confidence = self._classify(ind)

        price_vs_ema = (
            "ABOVE" if ind.get("close", 0) > (ind.get("ema_200") or 0) else "BELOW"
        )

        result = {
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
            "price_vs_ema":     price_vs_ema,
            "regime_summary":   summary,
            "confidence_score": confidence,
        }

        logger.info(
            "Regime detected: %s (confidence=%.2f) | ADX=%.1f | Close=%.0f | EMA200=%.0f",
            label.value, confidence,
            ind.get("adx_14") or 0,
            ind.get("close") or 0,
            ind.get("ema_200") or 0,
        )
        return result


# ─── Persistence ──────────────────────────────────────────────────────────────

def persist_regime(result: dict) -> MarketRegime:
    """Write a regime detection result to the market_regime SQLite table."""
    db: Session = SessionLocal()
    try:
        record = MarketRegime(
            timestamp         = result["timestamp"],
            index_symbol      = result["index_symbol"],
            regime_label      = result["regime_label"],
            adx_14            = result.get("adx_14"),
            atr_14            = result.get("atr_14"),
            atr_percentile    = result.get("atr_percentile"),
            ema_200           = result.get("ema_200"),
            close_price       = result.get("close_price"),
            bb_upper          = result.get("bb_upper"),
            bb_lower          = result.get("bb_lower"),
            slope_20d         = result.get("slope_20d"),
            price_vs_ema      = result.get("price_vs_ema"),
            regime_summary    = result.get("regime_summary"),
            confidence_score  = result.get("confidence_score"),
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        logger.info("Regime persisted to DB: id=%d label=%s", record.id, record.regime_label)
        return record
    except Exception as exc:
        db.rollback()
        logger.error("Failed to persist regime: %s", exc, exc_info=True)
        raise
    finally:
        db.close()


# ─── Scheduled job ────────────────────────────────────────────────────────────

_detector = RegimeDetector()


async def _regime_job() -> None:
    """Async wrapper run every 5 minutes by APScheduler."""
    try:
        result = await asyncio.get_event_loop().run_in_executor(None, _detector.detect)
        if result:
            await asyncio.get_event_loop().run_in_executor(None, persist_regime, result)
        else:
            logger.warning("Regime detection returned no result – skipping persistence")
    except Exception as exc:
        logger.error("Regime scheduler job failed: %s", exc, exc_info=True)


def start_regime_scheduler() -> AsyncIOScheduler:
    """
    Create and start the APScheduler instance.
    Call from FastAPI lifespan startup.
    """
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        _regime_job,
        trigger="interval",
        seconds=POLL_INTERVAL_SEC,
        id="regime_detector",
        name="Market Regime Detector",
        replace_existing=True,
        max_instances=1,        # prevent overlap if job runs long
    )
    scheduler.start()
    logger.info(
        "Regime scheduler started – running every %d s", POLL_INTERVAL_SEC
    )
    return scheduler
