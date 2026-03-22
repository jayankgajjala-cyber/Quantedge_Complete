"""
backend/services/signal_engine.py
=====================================
SignalEngine v2 - Multi-Source Handshake Orchestrator

THE HANDSHAKE - A signal is HIGH CONFIDENCE only when ALL THREE agree:
  1. Technical Strategy (Mod 3 engine)  -> BUY / SELL / HOLD
  2. TradingView Consensus (Source D)   -> STRONG_BUY / BUY
  3. Moneycontrol Sentiment (Source C)  -> BULLISH / NEUTRAL (not BEARISH)

OVERRIDE RULES (in order):
  A. Moneycontrol BEARISH + BUY         -> HOLD/WATCH (MC override)
  B. FinBERT sentiment < -0.6 + BUY    -> HOLD/CAUTION
  C. FinBERT sentiment > +0.6 + HOLD   -> WATCH (upgrade)

MACRO RISK ADJUSTMENTS (Investing.com):
  - DXY >= 106     -> SL buffer +25%
  - US10Y >= 5%    -> position size 60%
  - Brent >= 95    -> commodity risk flag

GRACEFUL DEGRADATION:
  TV unavailable   -> skip TV gate, conf -5
  MC unavailable   -> skip MC gate, log warning
  Macro unavailable -> default risk params, flagged in audit
"""

from __future__ import annotations

import json
import logging
import math
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

import pandas as pd
from sqlalchemy import desc
from sqlalchemy.orm import Session

from backend.core.config import get_settings
from backend.core.database import get_db_context
from backend.models.portfolio import Holding
from backend.models.regime    import MarketRegime, MarketRegimeLabel
from backend.models.signals   import (
    FinalSignal, LiveSignal, RegimeMode,
    SignalStatus, SignalType,
)
from backend.services.data_manager import (
    DATA_UNAVAILABLE, MacroContext, NewsContext, TVConsensus,
    fetch_live_price, fetch_tv_consensus, get_data_manager,
)
from backend.services.news_service  import get_news_service
from backend.services.quant_service import (
    STRATEGY_CATEGORY, get_quant_service, fetch_with_cache, _generate_signals,
)
from backend.services.regime_service import get_regime_service

logger = logging.getLogger(__name__)
cfg    = get_settings()

NSE_SUFFIX          = ".NS"
VOLUME_CONFIRM_X    = 1.5
ATR_STOP_MULT       = 1.5
TARGET1_MULT        = 2.0
TARGET2_MULT        = 3.5
MIN_RR              = 1.5
AGREEMENT_THRESHOLD = 3
AGREEMENT_BONUS     = 20.0
BIAS_HOLD_PCT       = 0.80
BIAS_PENALTY        = 10.0
SIGNAL_TTL_MINUTES  = 30
TV_PENALTY_UNAVAIL  = 5.0
TV_CONFIRM_BONUS    = 12.0
MC_CONFIRM_BONUS    = 8.0


# TradingView alignment
TV_BUY_RECS = {"STRONG_BUY", "BUY"}


def _tv_aligns_buy(tv: TVConsensus) -> Optional[bool]:
    if tv.summary == DATA_UNAVAILABLE:
        return None
    return str(tv.summary).upper() in TV_BUY_RECS


def _mc_negative(news: NewsContext) -> bool:
    if news.market_mood == DATA_UNAVAILABLE:
        return False
    return str(news.market_mood).upper() == "BEARISH"


def _compute_macro_adjustments(macro: MacroContext) -> dict:
    sl_mult    = ATR_STOP_MULT
    pos_factor = 1.0
    risk_flags = []
    notes      = []
    avail      = False

    dxy      = macro.dxy_index    if macro.dxy_index    != DATA_UNAVAILABLE else None
    yield10  = macro.us_10y_yield if macro.us_10y_yield != DATA_UNAVAILABLE else None
    crude    = macro.brent_crude  if macro.brent_crude  != DATA_UNAVAILABLE else None

    if any(v is not None for v in [dxy, yield10, crude]):
        avail = True

    if dxy and dxy >= 106:
        sl_mult += 0.375
        risk_flags.append("DXY_STRENGTH")
        notes.append(f"DXY={dxy:.1f} >= 106 - SL buffer expanded 25%")

    if yield10 and yield10 >= 5.0:
        pos_factor = 0.6
        risk_flags.append("HIGH_YIELD_RISK")
        notes.append(f"US10Y={yield10:.2f}% >= 5% - position size 60%")

    if crude and crude >= 95:
        risk_flags.append("COMMODITY_RISK")
        notes.append(f"Brent={crude:.1f} >= 95 - commodity risk")

    return {
        "sl_multiplier":  round(sl_mult, 3),
        "pos_factor":     round(pos_factor, 2),
        "risk_flags":     risk_flags,
        "macro_notes":    notes,
        "macro_available":avail,
        "dxy":            dxy,
        "us_10y_yield":   yield10,
        "brent_crude":    crude,
    }


def _compute_atr(df, price: float = 100.0) -> float:
    if df is not None and len(df) >= 15:
        try:
            tr = pd.concat([
                df["High"] - df["Low"],
                (df["High"] - df["Close"].shift()).abs(),
                (df["Low"]  - df["Close"].shift()).abs(),
            ], axis=1).max(axis=1)
            return float(tr.ewm(alpha=1/14, adjust=False).mean().iloc[-1])
        except Exception:
            pass
    return price * 0.01


def _build_levels(price: float, signal: SignalType, atr: float, sl_mult: float) -> dict:
    if signal == SignalType.BUY:
        sl = price - sl_mult * atr
        t1 = price + TARGET1_MULT * atr
        t2 = price + TARGET2_MULT * atr
        rr = (t1 - price) / max(price - sl, 0.001)
    elif signal == SignalType.SELL:
        sl = price + sl_mult * atr
        t1 = price - TARGET1_MULT * atr
        t2 = price - TARGET2_MULT * atr
        rr = (price - t1) / max(sl - price, 0.001)
    else:
        return {"entry": None, "stop_loss": None, "target_1": None, "target_2": None, "rr": None}
    return {
        "entry":    round(price, 2),
        "stop_loss":round(sl, 2),
        "target_1": round(t1, 2),
        "target_2": round(t2, 2),
        "rr":       round(rr, 2),
    }


REGIME_FIT_BONUS = {
    (RegimeMode.STRONG_TREND,  "trend"):      10.0,
    (RegimeMode.STRONG_TREND,  "momentum"):   10.0,
    (RegimeMode.SIDEWAYS,      "reversion"):  10.0,
    (RegimeMode.SIDEWAYS,      "swing"):       8.0,
    (RegimeMode.BEAR_CRASHING, "fundamental"):8.0,
}


def _base_conf(ticker: str, strategy: str, db: Session) -> float:
    from backend.models.backtest import StrategyPerformance
    row = db.query(StrategyPerformance).filter(
        StrategyPerformance.stock_ticker  == ticker,
        StrategyPerformance.strategy_name == strategy,
    ).first()
    if not row:
        return 50.0
    return round(max(20.0, min(80.0,
        (row.sharpe_ratio or 0.0) * 15.0 + (row.win_rate or 0.0) * 0.35
    )), 1)


def _build_audit(technical, tv, news, macro, sentiment_lbl, override, bias_warning) -> dict:
    return {
        "technical_signal":     technical,
        "tradingview_summary":  tv.summary if tv.summary != DATA_UNAVAILABLE else "DATA_UNAVAILABLE",
        "moneycontrol_mood":    news.market_mood if news.market_mood != DATA_UNAVAILABLE else "DATA_UNAVAILABLE",
        "news_sentiment":       sentiment_lbl or "N/A",
        "sentiment_override":   override,
        "macro_available":      macro["macro_available"],
        "macro_risk_flags":     macro["risk_flags"],
        "macro_notes":          macro["macro_notes"],
        "dxy":                  macro["dxy"],
        "us_10y_yield":         macro["us_10y_yield"],
        "brent_crude":          macro["brent_crude"],
        "sl_multiplier":        macro["sl_multiplier"],
        "pos_size_factor":      macro["pos_factor"],
        "bias_warning":         bias_warning,
    }


def _persist_final(db, scan_id, ticker, d, regime_mode):
    existing = db.query(FinalSignal).filter(
        FinalSignal.scan_id == scan_id,
        FinalSignal.ticker  == ticker,
    ).first()
    sig_str  = d.get("signal", "HOLD").split("/")[0].strip().upper()
    sig_enum = SignalType(sig_str) if sig_str in SignalType._value2member_map_ else SignalType.HOLD
    fs = existing or FinalSignal()
    fs.scan_id              = scan_id
    fs.ticker               = ticker
    fs.regime               = regime_mode
    fs.selected_strategy    = d.get("selected_strategy", "UNKNOWN")
    fs.signal               = sig_enum
    fs.confidence           = d.get("confidence", 0.0)
    fs.entry_price          = d.get("entry_price")
    fs.stop_loss            = d.get("stop_loss")
    fs.target_1             = d.get("target_1")
    fs.target_2             = d.get("target_2")
    fs.risk_reward_ratio    = d.get("risk_reward")
    fs.volume_ratio         = d.get("volume_ratio")
    fs.agreeing_strategies  = d.get("agreeing_strategies")
    fs.total_strategies_run = d.get("total_strategies")
    fs.agreement_bonus      = d.get("agreement_bonus")
    fs.bias_warning         = d.get("bias_warning", False)
    fs.bias_message         = d.get("bias_message")
    fs.sentiment_score      = d.get("sentiment_score")
    fs.sentiment_label      = d.get("sentiment_label")
    fs.sentiment_override   = d.get("sentiment_override", False)
    fs.reason               = d.get("reason", "")
    fs.status               = SignalStatus.ACTIVE
    fs.generated_at         = datetime.utcnow()
    fs.expires_at           = datetime.utcnow() + timedelta(minutes=SIGNAL_TTL_MINUTES)
    if not existing:
        db.add(fs)
    db.flush()


class SignalEngine:
    """Multi-source handshake signal engine."""

    def __init__(self):
        self.regime_svc = get_regime_service()
        self.quant_svc  = get_quant_service()
        self.news_svc   = get_news_service()
        self.data_mgr   = get_data_manager()

    def _regime_mode(self, lbl: MarketRegimeLabel) -> RegimeMode:
        return {
            MarketRegimeLabel.STRONG_TREND:       RegimeMode.STRONG_TREND,
            MarketRegimeLabel.SIDEWAYS:           RegimeMode.SIDEWAYS,
            MarketRegimeLabel.VOLATILE_HIGH_RISK: RegimeMode.VOLATILE_HIGH_RISK,
            MarketRegimeLabel.BEAR_CRASHING:      RegimeMode.BEAR_CRASHING,
        }.get(lbl, RegimeMode.UNKNOWN)

    def run_scan(self) -> list[dict]:
        scan_id = f"scan_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        logger.info("SIGNAL SCAN START id=%s", scan_id)

        with get_db_context() as db:
            holdings = db.query(Holding).all()
            tickers  = [h.symbol for h in holdings]
            if not tickers:
                return []

            regime_row  = db.query(MarketRegime).order_by(desc(MarketRegime.timestamp)).first()
            regime_lbl  = regime_row.regime_label if regime_row else MarketRegimeLabel.UNKNOWN
            regime_mode = self._regime_mode(regime_lbl)

            macro_ctx = self.data_mgr.fetch_macro_only()
            macro_adj = _compute_macro_adjustments(macro_ctx)
            news_ctx  = self.data_mgr.fetch_news_only()
            mc_bearish = _mc_negative(news_ctx)

            all_raw: list[dict] = []
            ticker_dfs: dict    = {}

            for ticker in tickers:
                df = fetch_with_cache(ticker)
                ticker_dfs[ticker] = df
                if df is None or len(df) < 60:
                    continue
                for strat in [
                    "Trend_EMA_Cross", "Mean_Reversion_ZScore", "Momentum_Breakout",
                    "Bollinger_Reversion", "Swing_HighLow", "Volume_Surge",
                    "Factor_Momentum", "Fundamental_Filter",
                ]:
                    sigs   = _generate_signals(df, strat)
                    last_v = int(sigs.iloc[-1]) if not sigs.empty else 0
                    stype  = (SignalType.BUY  if last_v == 1  else
                              SignalType.SELL if last_v == -1 else SignalType.HOLD)
                    all_raw.append({"ticker": ticker, "strategy": strat, "signal": stype})
                    close = float(df["Close"].iloc[-1])
                    db.add(LiveSignal(
                        scan_id=scan_id, symbol=ticker, strategy_name=strat,
                        signal_type=stype, price_at_signal=close, status=SignalStatus.ACTIVE,
                    ))
                db.flush()

            hold_count = sum(1 for s in all_raw if s["signal"] == SignalType.HOLD)
            bias_flag  = len(all_raw) > 0 and (hold_count / len(all_raw)) >= BIAS_HOLD_PCT
            bias_msg   = (f"Bias: {hold_count}/{len(all_raw)} HOLD signals" if bias_flag else None)

            output: list[dict] = []

            for ticker in tickers:
                try:
                    df        = ticker_dfs.get(ticker)
                    ltp_any   = fetch_live_price(ticker)
                    ltp       = ltp_any if ltp_any != DATA_UNAVAILABLE else None
                    tv_result = fetch_tv_consensus(ticker)

                    t_sigs    = [s for s in all_raw if s["ticker"] == ticker]
                    buy_v     = sum(1 for s in t_sigs if s["signal"] == SignalType.BUY)
                    sell_v    = sum(1 for s in t_sigs if s["signal"] == SignalType.SELL)
                    hold_v    = sum(1 for s in t_sigs if s["signal"] == SignalType.HOLD)
                    dom_count = max(buy_v, sell_v, hold_v)
                    agree_b   = AGREEMENT_BONUS if dom_count >= AGREEMENT_THRESHOLD else 0.0

                    best_row = self.quant_svc.get_best_strategy(ticker, regime_lbl, db)
                    if best_row is None:
                        d = {
                            "ticker": ticker, "regime": regime_mode.value,
                            "selected_strategy": "CASH_MODE", "signal": SignalType.CASH.value,
                            "confidence": 0.0, "reason": "VOLATILE - CASH enforced.",
                            "source_confirmations": _build_audit(
                                "CASH", tv_result, news_ctx, macro_adj, None, False, bias_flag),
                            "generated_at": datetime.utcnow().isoformat(),
                        }
                        output.append(d)
                        _persist_final(db, scan_id, ticker, d, regime_mode)
                        continue

                    strategy_name = best_row.strategy_name
                    strat_vote    = next(
                        (s["signal"] for s in t_sigs if s["strategy"] == strategy_name),
                        SignalType.HOLD,
                    )

                    price = ltp or (float(df["Close"].iloc[-1]) if df is not None and len(df) else 0)
                    if price <= 0:
                        strat_vote = SignalType.HOLD

                    final_sig = strat_vote
                    if regime_mode == RegimeMode.VOLATILE_HIGH_RISK and strat_vote == SignalType.BUY:
                        final_sig = SignalType.CASH

                    vol_ok    = True
                    vol_ratio = None
                    if df is not None and len(df) >= 21:
                        try:
                            vol      = float(df["Volume"].iloc[-1])
                            vol_avg  = float(df["Volume"].iloc[-21:-1].mean())
                            vol_ratio = vol / max(vol_avg, 1)
                            vol_ok   = vol_ratio >= VOLUME_CONFIRM_X
                            if not vol_ok and final_sig == SignalType.BUY:
                                final_sig = SignalType.HOLD
                        except Exception:
                            pass

                    atr    = _compute_atr(df, price=max(price, 1.0))
                    levels = _build_levels(price, final_sig, atr, macro_adj["sl_multiplier"])

                    if final_sig == SignalType.BUY and levels["rr"] and levels["rr"] < MIN_RR:
                        final_sig = SignalType.HOLD
                        levels    = _build_levels(price, SignalType.HOLD, atr, macro_adj["sl_multiplier"])

                    # THE HANDSHAKE
                    tv_aligns  = _tv_aligns_buy(tv_result)
                    tv_penalty = TV_PENALTY_UNAVAIL if tv_result.summary == DATA_UNAVAILABLE else 0.0

                    mc_override = False
                    if final_sig == SignalType.BUY and mc_bearish:
                        final_sig   = SignalType.HOLD
                        mc_override = True
                        logger.warning("[MC OVERRIDE] %s BUY->HOLD (Moneycontrol BEARISH)", ticker)

                    base  = _base_conf(ticker, strategy_name, db)
                    rfb   = REGIME_FIT_BONUS.get((regime_mode, STRATEGY_CATEGORY.get(strategy_name,"")), 0.0)
                    tv_b  = TV_CONFIRM_BONUS if (tv_aligns is True and final_sig == SignalType.BUY) else 0.0
                    mc_b  = MC_CONFIRM_BONUS if (not mc_bearish and news_ctx.market_mood not in (DATA_UNAVAILABLE, None)) else 0.0
                    conf  = round(min(100.0, max(5.0,
                        base + rfb + agree_b + tv_b + mc_b - tv_penalty
                        - (BIAS_PENALTY if bias_flag else 0.0)
                    )), 1)

                    sentiment_score = None
                    sentiment_lbl   = None
                    sent_override   = False
                    orig_signal_str = final_sig.value
                    try:
                        analysis = self.news_svc.analyse(ticker, db)
                        sc       = analysis.avg_sentiment_score or 0.0
                        sl_      = analysis.sentiment_label.value if analysis.sentiment_label else "NEUTRAL"
                        sentiment_score = round(sc, 4)
                        sentiment_lbl   = sl_
                        if sc < cfg.SENTIMENT_NEGATIVE_THRESHOLD and final_sig == SignalType.BUY:
                            final_sig   = SignalType.HOLD
                            conf        = max(5.0, conf - 30.0)
                            sent_override = True
                        elif sc > cfg.SENTIMENT_POSITIVE_THRESHOLD and final_sig == SignalType.HOLD:
                            conf        = min(100.0, conf + 15.0)
                            sent_override = True
                    except Exception as exc:
                        logger.warning("Sentiment failed for %s: %s", ticker, exc)

                    parts = [f"{regime_mode.value.replace('_',' ')} regime"]
                    if vol_ok and vol_ratio:
                        parts.append(f"Volume confirmed ({vol_ratio:.1f}x avg)")
                    if dom_count >= AGREEMENT_THRESHOLD:
                        parts.append(f"{dom_count} strategies agree (+{agree_b:.0f} conf)")
                    if tv_aligns is True:
                        parts.append(f"Confirmed by TradingView ({tv_result.summary})")
                    elif tv_result.summary == DATA_UNAVAILABLE:
                        parts.append("TradingView: DATA_UNAVAILABLE (conf -5)")
                    if not mc_bearish and news_ctx.market_mood not in (DATA_UNAVAILABLE, None):
                        parts.append(f"Confirmed by Moneycontrol ({news_ctx.market_mood})")
                    if mc_override:
                        parts.append("OVERRIDE: Moneycontrol BEARISH - BUY->HOLD")
                    if macro_adj["risk_flags"]:
                        parts.append(f"Macro: {', '.join(macro_adj['risk_flags'])}")

                    audit = _build_audit(
                        technical=orig_signal_str, tv=tv_result, news=news_ctx,
                        macro=macro_adj, sentiment_lbl=sentiment_lbl,
                        override=sent_override or mc_override, bias_warning=bias_flag,
                    )

                    d = {
                        "ticker":              ticker,
                        "regime":              regime_mode.value,
                        "selected_strategy":   strategy_name,
                        "signal":              final_sig.value,
                        "confidence":          conf,
                        "entry_price":         levels.get("entry"),
                        "stop_loss":           levels.get("stop_loss"),
                        "target_1":            levels.get("target_1"),
                        "target_2":            levels.get("target_2"),
                        "risk_reward":         levels.get("rr"),
                        "volume_ratio":        round(vol_ratio, 2) if vol_ratio else None,
                        "agreeing_strategies": dom_count,
                        "total_strategies":    len(t_sigs),
                        "agreement_bonus":     agree_b,
                        "bias_warning":        bias_flag,
                        "bias_message":        bias_msg,
                        "sentiment_score":     sentiment_score,
                        "sentiment_label":     sentiment_lbl,
                        "sentiment_override":  sent_override or mc_override,
                        "original_signal":     orig_signal_str,
                        "reason":              " + ".join(parts) + ".",
                        "source_confirmations":audit,
                        "generated_at":        datetime.utcnow().isoformat(),
                    }
                    output.append(d)
                    _persist_final(db, scan_id, ticker, d, regime_mode)

                    logger.info(
                        "SIGNAL %-12s %-8s conf=%5.1f TV=%s MC=%s macro=%s",
                        ticker, final_sig.value, conf,
                        tv_result.summary if tv_result.summary != DATA_UNAVAILABLE else "N/A",
                        news_ctx.market_mood if news_ctx.market_mood != DATA_UNAVAILABLE else "N/A",
                        macro_adj["risk_flags"] or "none",
                    )
                except Exception as exc:
                    logger.error("FinalSignal failed for %s: %s", ticker, exc, exc_info=True)

            db.commit()

        logger.info("SCAN DONE %d signals", len(output))
        return output


_signal_engine: Optional[SignalEngine] = None

def get_signal_engine() -> SignalEngine:
    global _signal_engine
    if _signal_engine is None:
        _signal_engine = SignalEngine()
    return _signal_engine
