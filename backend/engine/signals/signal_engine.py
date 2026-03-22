"""
RegimeAwareSignalEngine
========================
The central orchestrator of Module 4.

Every 5 minutes during market hours (APScheduler):
  1. Fetch current market regime from market_regime table
  2. For each ticker in portfolio holdings:
       a. Run all 8 strategies on the latest enriched data
       b. Collect raw signals → write to live_signals table
       c. Compute agreement factor (bonus if ≥3 strategies agree)
  3. Detect scan-level HOLD bias across all signals
  4. For each ticker:
       a. Use RegimeSwitchboard to pick the best strategy
       b. Fetch the latest 5-min candle
       c. Validate the signal (volume + R:R gates)
       d. Apply confidence adjustments (agreement bonus, bias penalty)
       e. Build the FinalSignal and persist to final_signals table
  5. Return list of FinalSignal JSON objects for the API

Output JSON per ticker (frontend spec):
{
  "ticker":            "SBIN",
  "regime":            "STRONG_TREND",
  "selected_strategy": "Trend_EMA_Cross",
  "signal":            "BUY",
  "confidence":        88.0,
  "entry_price":       820.50,
  "stop_loss":         803.20,
  "target_1":          854.60,
  "target_2":          876.15,
  "risk_reward":       2.0,
  "reason":            "ADX confirms trend + Strategy agreement",
  "agreeing_strategies": 4,
  "bias_warning":      false,
  "bias_message":      "",
  "generated_at":      "2024-01-15T09:30:00"
}
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd
import yfinance as yf
from sqlalchemy import desc
from sqlalchemy.orm import Session

from engine.indicators.technical import enrich_dataframe, atr as calc_atr
from engine.signals.agreement_factor import (
    apply_confidence_adjustments,
    compute_agreement,
    detect_scan_bias,
)
from engine.signals.price_feed import CandleData, fetch_latest_candles, is_market_open
from engine.signals.regime_switchboard import map_best_strategy
from engine.signals.signal_validator import validate_signal
from engine.strategies.library import all_strategy_instances
from models.database import Holding, MarketRegime, MarketRegimeLabel, StrategyPerformance
from models.session import SessionLocal
from models.signals_db import (
    FinalSignal,
    LiveSignal,
    RegimeMode,
    SignalAgreementLog,
    SignalStatus,
    SignalType,
)

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

NSE_SUFFIX         = ".NS"
BASE_CONFIDENCE    = 50.0   # starting confidence before adjustments
MIN_BARS_FOR_SIGNAL= 220    # need at least this many daily bars for indicators
SIGNAL_TTL_MINUTES = 30     # FinalSignals expire after 30 min


# ─── Helper: fetch daily data for indicator enrichment ───────────────────────

def _fetch_daily_enriched(symbol: str, exchange: str = "NSE") -> Optional[pd.DataFrame]:
    """Download 2 years of daily OHLCV and enrich with all indicators."""
    yf_sym = f"{symbol}{NSE_SUFFIX}" if exchange == "NSE" else symbol
    try:
        df = yf.Ticker(yf_sym).history(
            period="2y", interval="1d", auto_adjust=True
        )
        if df.empty or len(df) < MIN_BARS_FOR_SIGNAL:
            logger.warning("Insufficient daily data for %s (%d bars)", symbol, len(df))
            return None
        df = df[["Open","High","Low","Close","Volume"]].rename(columns=str.title)
        df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
        df = df.dropna()
        return enrich_dataframe(df)
    except Exception as exc:
        logger.error("Daily data fetch failed for %s: %s", symbol, exc)
        return None


def _extract_atr(df: pd.DataFrame) -> Optional[float]:
    """Return the latest ATR(14) value from an enriched DataFrame."""
    if df is None or "ATR_14" not in df.columns:
        return None
    series = df["ATR_14"].dropna()
    return float(series.iloc[-1]) if not series.empty else None


# ─── Base confidence from strategy performance ───────────────────────────────

def _base_confidence_from_performance(
    ticker:        str,
    strategy_name: str,
    db:            Session,
) -> float:
    """
    Derive a base confidence from historical Sharpe + Win Rate.
    Score formula: clamp((Sharpe * 15) + (WinRate * 0.4), 20, 85)
    """
    row = (
        db.query(StrategyPerformance)
          .filter(
              StrategyPerformance.stock_ticker  == ticker,
              StrategyPerformance.strategy_name == strategy_name,
          )
          .first()
    )
    if row is None:
        return BASE_CONFIDENCE

    sharpe_contrib   = (row.sharpe_ratio or 0.0) * 15.0
    win_rate_contrib = (row.win_rate     or 0.0) *  0.4
    score            = sharpe_contrib + win_rate_contrib
    return round(max(20.0, min(85.0, score)), 1)


# ─── Regime confidence bonus ─────────────────────────────────────────────────

REGIME_STRATEGY_FIT: dict[tuple, float] = {
    # (regime, strategy_category) → bonus points
    (RegimeMode.STRONG_TREND,       "trend"):      10.0,
    (RegimeMode.STRONG_TREND,       "momentum"):   10.0,
    (RegimeMode.SIDEWAYS,           "reversion"):  10.0,
    (RegimeMode.SIDEWAYS,           "swing"):       8.0,
    (RegimeMode.BEAR_CRASHING,      "fundamental"): 8.0,
    (RegimeMode.VOLATILE_HIGH_RISK, "reversion"):   5.0,
}

STRATEGY_CATEGORY: dict[str, str] = {
    "Trend_EMA_Cross":          "trend",
    "Momentum_Breakout":        "momentum",
    "Factor_Momentum":          "momentum",
    "Volume_Surge":             "momentum",
    "Mean_Reversion_ZScore":    "reversion",
    "Bollinger_Reversion":      "reversion",
    "Swing_HighLow":            "swing",
    "Fundamental_Filter":       "fundamental",
}


def _regime_fit_bonus(regime: RegimeMode, strategy_name: str) -> float:
    cat = STRATEGY_CATEGORY.get(strategy_name, "")
    return REGIME_STRATEGY_FIT.get((regime, cat), 0.0)


# ─── Reason builder ──────────────────────────────────────────────────────────

def _build_reason(
    ticker:           str,
    regime:           RegimeMode,
    strategy_name:    str,
    candle:           Optional[CandleData],
    agreeing_count:   int,
    df:               Optional[pd.DataFrame],
) -> str:
    parts = []

    # Regime context
    parts.append(f"{regime.value.replace('_', ' ')} regime active")

    # ADX
    if df is not None and "ADX_14" in df.columns:
        adx_val = df["ADX_14"].dropna().iloc[-1] if not df["ADX_14"].dropna().empty else None
        if adx_val:
            adx_str = f"ADX={adx_val:.1f}"
            parts.append(f"{'ADX confirms trend' if adx_val > 25 else adx_str}")

    # Volume
    if candle and candle.volume_confirmed:
        parts.append(f"Volume confirmed ({candle.volume_ratio:.1f}×avg)")

    # Strategy
    parts.append(f"Strategy '{strategy_name}' selected")

    # Agreement
    if agreeing_count >= 3:
        parts.append(f"{agreeing_count} strategies agree (+20 confidence)")

    return " + ".join(parts) + "."


# ─── FinalSignal persistence ─────────────────────────────────────────────────

def _persist_final_signal(db: Session, fs: FinalSignal) -> FinalSignal:
    try:
        # Upsert: if same scan_id + ticker exists, update it
        existing = (
            db.query(FinalSignal)
              .filter(FinalSignal.scan_id == fs.scan_id,
                      FinalSignal.ticker  == fs.ticker)
              .first()
        )
        if existing:
            for col in FinalSignal.__table__.columns.keys():
                if col not in ("id",):
                    setattr(existing, col, getattr(fs, col))
            db.commit()
            db.refresh(existing)
            return existing
        db.add(fs)
        db.commit()
        db.refresh(fs)
        return fs
    except Exception as exc:
        db.rollback()
        logger.error("Failed to persist FinalSignal for %s: %s", fs.ticker, exc)
        raise


# ─── Core engine class ────────────────────────────────────────────────────────

class RegimeAwareSignalEngine:
    """
    The main signal engine.

    Usage
    -----
        engine  = RegimeAwareSignalEngine()
        results = engine.run_scan()     # returns list[dict] for frontend
    """

    def __init__(self, exchange: str = "NSE"):
        self.exchange   = exchange
        self.strategies = all_strategy_instances()
        logger.info(
            "RegimeAwareSignalEngine initialised with %d strategies",
            len(self.strategies),
        )

    # ── Step 1: load tickers from holdings ───────────────────────────────────

    def _load_tickers(self, db: Session) -> list[str]:
        holdings = db.query(Holding).all()
        tickers  = [h.symbol for h in holdings]
        if not tickers:
            logger.warning("No holdings found in DB — signal scan has nothing to process")
        return tickers

    # ── Step 2: get current regime ───────────────────────────────────────────

    def _current_regime(self, db: Session) -> tuple[MarketRegimeLabel, float]:
        """Returns (regime_label, regime_confidence)."""
        row = (
            db.query(MarketRegime)
              .order_by(desc(MarketRegime.timestamp))
              .first()
        )
        if row is None:
            return MarketRegimeLabel.UNKNOWN, 0.0
        return row.regime_label, row.confidence_score or 0.0

    # ── Step 3: run all strategies → collect raw signals ─────────────────────

    def _run_all_strategies(
        self,
        ticker:  str,
        df:      pd.DataFrame,
        scan_id: str,
        db:      Session,
    ) -> list[dict]:
        """
        Run every strategy on *df*, persist LiveSignal rows,
        and return list of signal dicts for agreement counting.
        """
        raw_signals = []
        live_signal_rows = []

        for strategy in self.strategies:
            try:
                sig_df = strategy.generate_signals(df.copy())
                last   = sig_df.iloc[-1]
                raw_val = int(last.get("signal", 0))

                if   raw_val ==  1: stype = SignalType.BUY
                elif raw_val == -1: stype = SignalType.SELL
                else:               stype = SignalType.HOLD

                live_signal_rows.append(LiveSignal(
                    scan_id          = scan_id,
                    symbol           = ticker,
                    strategy_name    = strategy.name,
                    signal_type      = stype,
                    price_at_signal  = float(df["Close"].iloc[-1]),
                    volume_ratio     = float(df.get("VOL_RATIO", pd.Series([None])).iloc[-1] or 0),
                    adx_at_signal    = float(df["ADX_14"].dropna().iloc[-1]) if "ADX_14" in df.columns and not df["ADX_14"].dropna().empty else None,
                    rsi_at_signal    = float(df["RSI_14"].dropna().iloc[-1]) if "RSI_14" in df.columns and not df["RSI_14"].dropna().empty else None,
                    zscore_at_signal = float(df["ZSCORE_20"].dropna().iloc[-1]) if "ZSCORE_20" in df.columns and not df["ZSCORE_20"].dropna().empty else None,
                    raw_confidence   = 0.5,
                    status           = SignalStatus.ACTIVE,
                ))

                raw_signals.append({
                    "ticker":        ticker,
                    "strategy_name": strategy.name,
                    "signal_type":   stype,
                })

            except Exception as exc:
                logger.warning("Strategy %s failed on %s: %s", strategy.name, ticker, exc)

        # Bulk insert LiveSignal rows
        try:
            db.bulk_save_objects(live_signal_rows)
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.error("LiveSignal bulk insert failed for %s: %s", ticker, exc)

        return raw_signals

    # ── Step 4: build FinalSignal for one ticker ──────────────────────────────

    def _build_final_signal(
        self,
        ticker:          str,
        regime_label:    MarketRegimeLabel,
        regime_conf:     float,
        all_raw_signals: list[dict],
        candle:          Optional[CandleData],
        df:              Optional[pd.DataFrame],
        scan_id:         str,
        bias_result,
        db:              Session,
    ) -> FinalSignal:

        # Map regime enum
        regime_map = {
            MarketRegimeLabel.STRONG_TREND:       RegimeMode.STRONG_TREND,
            MarketRegimeLabel.SIDEWAYS:           RegimeMode.SIDEWAYS,
            MarketRegimeLabel.VOLATILE_HIGH_RISK: RegimeMode.VOLATILE_HIGH_RISK,
            MarketRegimeLabel.BEAR_CRASHING:      RegimeMode.BEAR_CRASHING,
            MarketRegimeLabel.UNKNOWN:            RegimeMode.UNKNOWN,
        }
        regime = regime_map.get(regime_label, RegimeMode.UNKNOWN)

        # Agreement factor
        agreement = compute_agreement(all_raw_signals, ticker)

        # Strategy selection via switchboard
        selection = map_best_strategy(ticker, db, regime_label)

        # Force CASH if switchboard says so
        if selection.force_cash:
            fs = FinalSignal(
                scan_id               = scan_id,
                ticker                = ticker,
                regime                = regime,
                selected_strategy     = "CASH_MODE",
                signal                = SignalType.CASH,
                confidence            = 0.0,
                reason                = selection.reason,
                agreeing_strategies   = agreement.tally.dominant_count,
                total_strategies_run  = len(self.strategies),
                agreement_bonus       = 0.0,
                bias_warning          = bias_result.bias_detected,
                bias_message          = bias_result.message if bias_result.bias_detected else None,
                status                = SignalStatus.ACTIVE,
                generated_at          = datetime.utcnow(),
                expires_at            = datetime.utcnow() + timedelta(minutes=SIGNAL_TTL_MINUTES),
            )
            return fs

        strategy_name = selection.selected_strategy

        # Get the proposed signal from the chosen strategy's raw vote
        ticker_signals  = [s for s in all_raw_signals if s["ticker"] == ticker
                           and s["strategy_name"] == strategy_name]
        proposed_type   = (
            ticker_signals[0]["signal_type"] if ticker_signals else SignalType.HOLD
        )

        # Validate against live candle
        atr_val = _extract_atr(df)
        if candle:
            validation = validate_signal(proposed_type, candle, atr_val, regime, strategy_name)
        else:
            # No candle (outside market hours) — demote to HOLD
            proposed_type = SignalType.HOLD
            validation = validate_signal(SignalType.HOLD, CandleData.__new__(CandleData), atr_val, regime, strategy_name)
            validation.passed = True
            validation.signal_type = SignalType.HOLD

        final_signal_type = validation.signal_type if validation.passed else SignalType.HOLD

        # Confidence scoring
        base_conf     = _base_confidence_from_performance(ticker, strategy_name, db)
        regime_bonus  = _regime_fit_bonus(regime, strategy_name)
        conf          = apply_confidence_adjustments(
            base_confidence = base_conf + regime_bonus,
            agreement_bonus = agreement.agreement_bonus,
            bias_penalty    = bias_result.confidence_penalty,
        )

        # Reason string
        reason = _build_reason(
            ticker, regime, strategy_name,
            candle if validation.passed else None,
            agreement.tally.dominant_count, df,
        )
        if not validation.passed and validation.rejection_reason:
            reason += f" [Rejected: {validation.rejection_reason}]"

        # ADX / RSI from df
        adx_val = rsi_val = vol_ratio_val = None
        if df is not None:
            adx_val      = float(df["ADX_14"].dropna().iloc[-1])     if "ADX_14"    in df.columns and not df["ADX_14"].dropna().empty     else None
            rsi_val      = float(df["RSI_14"].dropna().iloc[-1])     if "RSI_14"    in df.columns and not df["RSI_14"].dropna().empty     else None
            vol_ratio_val= float(df["VOL_RATIO"].dropna().iloc[-1])  if "VOL_RATIO" in df.columns and not df["VOL_RATIO"].dropna().empty  else None

        fs = FinalSignal(
            scan_id               = scan_id,
            ticker                = ticker,
            regime                = regime,
            selected_strategy     = strategy_name,
            signal                = final_signal_type,
            confidence            = conf,
            entry_price           = validation.entry_price,
            stop_loss             = validation.stop_loss,
            target_1              = validation.target_1,
            target_2              = validation.target_2,
            risk_reward_ratio     = validation.risk_reward_ratio,
            adx                   = adx_val,
            rsi                   = rsi_val,
            volume_ratio          = candle.volume_ratio if candle else vol_ratio_val,
            regime_confidence     = regime_conf,
            agreeing_strategies   = agreement.tally.dominant_count,
            total_strategies_run  = len(self.strategies),
            agreement_bonus       = agreement.agreement_bonus,
            bias_warning          = bias_result.bias_detected,
            bias_message          = bias_result.message if bias_result.bias_detected else None,
            reason                = reason,
            status                = SignalStatus.ACTIVE,
            generated_at          = datetime.utcnow(),
            expires_at            = datetime.utcnow() + timedelta(minutes=SIGNAL_TTL_MINUTES),
        )

        # Persist agreement log
        try:
            log = SignalAgreementLog(
                scan_id         = scan_id,
                ticker          = ticker,
                buy_votes       = agreement.tally.buy_votes,
                sell_votes      = agreement.tally.sell_votes,
                hold_votes      = agreement.tally.hold_votes,
                total_votes     = agreement.tally.total,
                agreement_pct   = agreement.agreement_pct,
                dominant_signal = agreement.dominant_signal,
                agreement_bonus = agreement.agreement_bonus,
                bias_detected   = bias_result.bias_detected,
            )
            db.add(log)
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning("Agreement log persist failed for %s: %s", ticker, exc)

        return fs

    # ── Master scan ───────────────────────────────────────────────────────────

    def run_scan(self) -> list[dict]:
        """
        Full pipeline scan. Called every 5 minutes by APScheduler.
        Returns list of FinalSignal JSON dicts for the REST API.
        """
        scan_id = f"scan_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        logger.info("═══ Signal scan START  scan_id=%s ═══", scan_id)
        start_ts = datetime.utcnow()

        db = SessionLocal()
        try:
            tickers      = self._load_tickers(db)
            if not tickers:
                return []

            regime_label, regime_conf = self._current_regime(db)
            logger.info("Current regime: %s (conf=%.2f)", regime_label, regime_conf)

            # Fetch live candles for all tickers in one batch
            candles: dict = {}
            if is_market_open():
                candles = fetch_latest_candles(tickers, self.exchange)
                logger.info("Market OPEN — live candles fetched for %d tickers", len(candles))
            else:
                logger.info("Market CLOSED — skipping live candle fetch")

            # Run all strategies per ticker, collect raw signals
            all_raw_signals: list[dict] = []
            ticker_dfs:      dict[str, Optional[pd.DataFrame]] = {}

            for ticker in tickers:
                df = _fetch_daily_enriched(ticker, self.exchange)
                ticker_dfs[ticker] = df
                if df is None:
                    logger.warning("No data for %s — skipping strategy run", ticker)
                    continue
                raw = self._run_all_strategies(ticker, df, scan_id, db)
                all_raw_signals.extend(raw)

            # Scan-level bias detection (uses all raw signals)
            bias_result = detect_scan_bias(all_raw_signals)
            if bias_result.bias_detected:
                logger.warning("BIAS DETECTED: %s", bias_result.message)

            # Build and persist FinalSignal per ticker
            output: list[dict] = []
            for ticker in tickers:
                df     = ticker_dfs.get(ticker)
                candle = candles.get(ticker)

                try:
                    fs = self._build_final_signal(
                        ticker, regime_label, regime_conf,
                        all_raw_signals, candle, df,
                        scan_id, bias_result, db,
                    )
                    fs = _persist_final_signal(db, fs)
                    output.append(fs.to_frontend_json())

                    logger.info(
                        "SIGNAL %-12s %-8s conf=%5.1f strategy=%-35s regime=%s",
                        ticker,
                        fs.signal.value,
                        fs.confidence,
                        fs.selected_strategy,
                        fs.regime.value,
                    )
                except Exception as exc:
                    logger.error("FinalSignal build failed for %s: %s", ticker, exc, exc_info=True)

            elapsed = (datetime.utcnow() - start_ts).total_seconds()
            logger.info(
                "═══ Signal scan END  scan_id=%s | %d signals | %.1fs ═══",
                scan_id, len(output), elapsed,
            )
            return output

        except Exception as exc:
            logger.critical("Scan run failed: %s", exc, exc_info=True)
            return []
        finally:
            db.close()


# ─── APScheduler integration ─────────────────────────────────────────────────

_engine_instance: Optional[RegimeAwareSignalEngine] = None


def get_signal_engine() -> RegimeAwareSignalEngine:
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = RegimeAwareSignalEngine()
    return _engine_instance


async def _signal_scan_job() -> None:
    """Async wrapper executed by APScheduler every 5 minutes."""
    import asyncio
    try:
        engine  = get_signal_engine()
        results = await asyncio.get_event_loop().run_in_executor(None, engine.run_scan)
        logger.info("Scheduled scan produced %d signals", len(results))
    except Exception as exc:
        logger.error("Scheduled signal scan failed: %s", exc, exc_info=True)


def start_signal_scheduler(scheduler) -> None:
    """
    Attach the signal-scan job to an existing APScheduler instance.
    Call from FastAPI lifespan after the regime scheduler is started.
    """
    scheduler.add_job(
        _signal_scan_job,
        trigger      = "interval",
        seconds      = 300,          # every 5 minutes
        id           = "signal_engine",
        name         = "Regime-Aware Signal Engine",
        replace_existing = True,
        max_instances    = 1,        # never overlap
    )
    logger.info("Signal engine job attached to scheduler (every 300 s)")
