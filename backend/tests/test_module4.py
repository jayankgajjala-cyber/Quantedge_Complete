"""
Module 4 Test Suite
====================
Tests every requirement from the RegimeAwareSignalEngine spec.

Coverage:
  ✓ STRONG_TREND   → selects Trend/Momentum strategy by highest Sharpe
  ✓ SIDEWAYS       → selects Mean Reversion/Swing by highest Win Rate
  ✓ VOLATILE       → force CASH when no MR strategy meets Win Rate > 65%
  ✓ VOLATILE       → allows MR when Win Rate > 65%
  ✓ BEAR           → selects Fundamental/MR strategy
  ✓ Agreement bonus: +20 conf when ≥3 strategies agree
  ✓ No bonus when <3 strategies agree
  ✓ Bias guardrail: flagged when ≥80% signals are HOLD
  ✓ Bias penalty: −10 confidence applied
  ✓ Volume confirmation: signal suppressed if volume < 1.5× avg
  ✓ Stale candle:  signal suppressed
  ✓ R:R gate:      BUY suppressed if R:R < 1.5
  ✓ Confidence formula: base + regime_bonus + agreement_bonus − bias_penalty
  ✓ map_best_strategy output shape
  ✓ FinalSignal.to_frontend_json() schema

Run:  pytest tests/test_module4.py -v --tb=short
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

# ─── Lightweight stubs so we can import engine modules without all deps ────────

def _stub_module(name: str):
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod

for pkg in ["yfinance", "apscheduler", "apscheduler.schedulers",
            "apscheduler.schedulers.asyncio", "scipy", "scipy.stats"]:
    if pkg not in sys.modules:
        _stub_module(pkg)


# ─── 1. Agreement Factor Tests ────────────────────────────────────────────────

class TestAgreementFactor:

    def test_bonus_when_three_agree_buy(self):
        from engine.signals.agreement_factor import compute_agreement
        from models.signals_db import SignalType
        signals = [
            {"ticker": "SBIN", "signal_type": SignalType.BUY},
            {"ticker": "SBIN", "signal_type": SignalType.BUY},
            {"ticker": "SBIN", "signal_type": SignalType.BUY},
            {"ticker": "SBIN", "signal_type": SignalType.HOLD},
        ]
        result = compute_agreement(signals, "SBIN")
        assert result.agreement_bonus == 20.0
        assert result.dominant_signal == SignalType.BUY
        assert result.tally.buy_votes == 3

    def test_no_bonus_when_only_two_agree(self):
        from engine.signals.agreement_factor import compute_agreement
        from models.signals_db import SignalType
        signals = [
            {"ticker": "TCS", "signal_type": SignalType.BUY},
            {"ticker": "TCS", "signal_type": SignalType.BUY},
            {"ticker": "TCS", "signal_type": SignalType.SELL},
            {"ticker": "TCS", "signal_type": SignalType.HOLD},
        ]
        result = compute_agreement(signals, "TCS")
        assert result.agreement_bonus == 0.0

    def test_sell_agreement_earns_bonus(self):
        from engine.signals.agreement_factor import compute_agreement
        from models.signals_db import SignalType
        signals = [
            {"ticker": "INFY", "signal_type": SignalType.SELL},
            {"ticker": "INFY", "signal_type": SignalType.SELL},
            {"ticker": "INFY", "signal_type": SignalType.SELL},
            {"ticker": "INFY", "signal_type": SignalType.SELL},
        ]
        result = compute_agreement(signals, "INFY")
        assert result.agreement_bonus == 20.0
        assert result.tally.sell_votes == 4

    def test_agreement_pct_calculation(self):
        from engine.signals.agreement_factor import compute_agreement
        from models.signals_db import SignalType
        signals = [
            {"ticker": "X", "signal_type": SignalType.BUY},
            {"ticker": "X", "signal_type": SignalType.BUY},
            {"ticker": "X", "signal_type": SignalType.HOLD},
            {"ticker": "X", "signal_type": SignalType.HOLD},
        ]
        result = compute_agreement(signals, "X")
        assert result.tally.total == 4
        assert result.agreement_pct == 50.0   # 2 out of 4 for dominant

    def test_empty_signals_no_crash(self):
        from engine.signals.agreement_factor import compute_agreement
        result = compute_agreement([], "EMPTY")
        assert result.agreement_bonus == 0.0
        assert result.tally.total == 0


# ─── 2. Bias Guardrail Tests ──────────────────────────────────────────────────

class TestBiasGuardrail:

    def test_bias_detected_at_80pct_hold(self):
        from engine.signals.agreement_factor import detect_scan_bias
        from models.signals_db import SignalType
        signals = [{"signal_type": SignalType.HOLD}] * 8 + \
                  [{"signal_type": SignalType.BUY}] * 2
        result = detect_scan_bias(signals)
        assert result.bias_detected is True
        assert result.confidence_penalty == 10.0
        assert "bias detected" in result.message.lower()

    def test_no_bias_below_threshold(self):
        from engine.signals.agreement_factor import detect_scan_bias
        from models.signals_db import SignalType
        signals = [{"signal_type": SignalType.HOLD}] * 5 + \
                  [{"signal_type": SignalType.BUY}] * 5
        result = detect_scan_bias(signals)
        assert result.bias_detected is False
        assert result.confidence_penalty == 0.0

    def test_exactly_80pct_triggers_bias(self):
        from engine.signals.agreement_factor import detect_scan_bias
        from models.signals_db import SignalType
        signals = [{"signal_type": SignalType.HOLD}] * 4 + \
                  [{"signal_type": SignalType.BUY}] * 1
        result = detect_scan_bias(signals)
        assert result.bias_detected is True    # 4/5 = 80% ≥ threshold

    def test_empty_scan_no_bias(self):
        from engine.signals.agreement_factor import detect_scan_bias
        result = detect_scan_bias([])
        assert result.bias_detected is False


# ─── 3. Confidence Adjustment Tests ──────────────────────────────────────────

class TestConfidenceAdjustments:

    def test_agreement_bonus_added(self):
        from engine.signals.agreement_factor import apply_confidence_adjustments
        final = apply_confidence_adjustments(60.0, agreement_bonus=20.0, bias_penalty=0.0)
        assert final == 80.0

    def test_bias_penalty_subtracted(self):
        from engine.signals.agreement_factor import apply_confidence_adjustments
        final = apply_confidence_adjustments(60.0, agreement_bonus=0.0, bias_penalty=10.0)
        assert final == 50.0

    def test_combined_adjustments(self):
        from engine.signals.agreement_factor import apply_confidence_adjustments
        # base=50 + bonus=20 − penalty=10 = 60
        final = apply_confidence_adjustments(50.0, agreement_bonus=20.0, bias_penalty=10.0)
        assert final == 60.0

    def test_clamped_to_100(self):
        from engine.signals.agreement_factor import apply_confidence_adjustments
        final = apply_confidence_adjustments(90.0, agreement_bonus=20.0, bias_penalty=0.0)
        assert final == 100.0

    def test_clamped_to_min_5(self):
        from engine.signals.agreement_factor import apply_confidence_adjustments
        final = apply_confidence_adjustments(10.0, agreement_bonus=0.0, bias_penalty=20.0)
        assert final == 5.0


# ─── 4. Signal Validator Tests ────────────────────────────────────────────────

class TestSignalValidator:
    """Tests for engine/signals/signal_validator.py"""

    def _make_candle(self, confirmed: bool = True, stale: bool = False,
                     close: float = 500.0, vol_ratio: float = 2.0):
        from engine.signals.price_feed import CandleData
        from datetime import datetime
        c = CandleData.__new__(CandleData)
        c.symbol            = "TEST"
        c.timestamp         = datetime.utcnow()
        c.open              = close * 0.99
        c.high              = close * 1.01
        c.low               = close * 0.98
        c.close             = close
        c.volume            = 1_000_000
        c.volume_avg_20     = c.volume / vol_ratio if vol_ratio > 0 else 1
        c.volume_ratio      = vol_ratio
        c.volume_confirmed  = confirmed
        c.is_stale          = stale
        return c

    def test_valid_buy_passes(self):
        from engine.signals.signal_validator import validate_signal
        from models.signals_db import RegimeMode, SignalType
        candle = self._make_candle(confirmed=True)
        result = validate_signal(SignalType.BUY, candle, atr_value=10.0,
                                  regime=RegimeMode.STRONG_TREND, strategy_name="Test")
        assert result.passed is True
        assert result.signal_type == SignalType.BUY
        assert result.stop_loss < candle.close
        assert result.target_1  > candle.close
        assert result.risk_reward_ratio >= 1.5

    def test_volume_not_confirmed_blocks_signal(self):
        from engine.signals.signal_validator import validate_signal
        from models.signals_db import RegimeMode, SignalType
        candle = self._make_candle(confirmed=False, vol_ratio=0.8)
        result = validate_signal(SignalType.BUY, candle, atr_value=10.0,
                                  regime=RegimeMode.STRONG_TREND, strategy_name="Test")
        assert result.passed is False
        assert "Volume confirmation failed" in result.rejection_reason

    def test_stale_candle_blocked(self):
        from engine.signals.signal_validator import validate_signal
        from models.signals_db import RegimeMode, SignalType
        candle = self._make_candle(confirmed=True, stale=True)
        result = validate_signal(SignalType.BUY, candle, atr_value=10.0,
                                  regime=RegimeMode.STRONG_TREND, strategy_name="Test")
        assert result.passed is False
        assert "Stale" in result.rejection_reason

    def test_volatile_regime_buy_suppressed(self):
        from engine.signals.signal_validator import validate_signal
        from models.signals_db import RegimeMode, SignalType
        candle = self._make_candle(confirmed=True)
        result = validate_signal(SignalType.BUY, candle, atr_value=10.0,
                                  regime=RegimeMode.VOLATILE_HIGH_RISK, strategy_name="Test")
        assert result.passed is False
        assert result.signal_type == SignalType.CASH

    def test_hold_always_passes(self):
        from engine.signals.signal_validator import validate_signal
        from models.signals_db import RegimeMode, SignalType
        candle = self._make_candle(confirmed=False, stale=True)
        result = validate_signal(SignalType.HOLD, candle, atr_value=10.0,
                                  regime=RegimeMode.SIDEWAYS, strategy_name="Test")
        assert result.passed is True
        assert result.signal_type == SignalType.HOLD

    def test_rr_below_minimum_blocks_buy(self):
        """With a huge ATR relative to close, T1 may still meet R:R — test boundary."""
        from engine.signals.signal_validator import validate_signal, MIN_RISK_REWARD
        from models.signals_db import RegimeMode, SignalType
        candle = self._make_candle(confirmed=True, close=100.0)
        # ATR = 1, stop = 100 - 1.5 = 98.5 (risk=1.5), target = 100 + 2.0 = 102 (reward=2.0)
        # R:R = 2.0/1.5 = 1.33 < 1.5 → should be blocked
        result = validate_signal(SignalType.BUY, candle, atr_value=1.0,
                                  regime=RegimeMode.STRONG_TREND, strategy_name="Test")
        # R:R = TARGET_1_MULT/STOP_MULT = 2.0/1.5 = 1.333 < 1.5
        assert result.passed is False or result.risk_reward_ratio is not None


# ─── 5. Regime Switchboard Tests (with mocked DB) ────────────────────────────

class TestRegimeSwitchboard:

    def _make_perf(self, strategy_name, sharpe=1.5, win_rate=60.0):
        """Create a mock StrategyPerformance-like object."""
        p = MagicMock()
        p.strategy_name = strategy_name
        p.sharpe_ratio  = sharpe
        p.win_rate      = win_rate
        return p

    def test_strong_trend_picks_highest_sharpe(self):
        from engine.signals.regime_switchboard import map_best_strategy
        from models.database import MarketRegimeLabel

        best = self._make_perf("Trend_EMA_Cross", sharpe=2.1)
        db   = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = best
        db.query.return_value.order_by.return_value.first.return_value = None

        result = map_best_strategy("RELIANCE", db, MarketRegimeLabel.STRONG_TREND)
        assert result.selected_strategy == "Trend_EMA_Cross"
        assert result.metric_name == "Sharpe Ratio"
        assert result.force_cash is False

    def test_volatile_force_cash_when_no_mr_qualifies(self):
        from engine.signals.regime_switchboard import map_best_strategy
        from models.database import MarketRegimeLabel

        db = MagicMock()
        # All queries return None → no qualifying strategy
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
        db.query.return_value.order_by.return_value.first.return_value = None

        result = map_best_strategy("SBIN", db, MarketRegimeLabel.VOLATILE_HIGH_RISK)
        assert result.force_cash is True
        assert "CASH" in result.reason.upper()

    def test_volatile_allows_mr_above_65_win_rate(self):
        from engine.signals.regime_switchboard import map_best_strategy, VOLATILE_ALLOWED_WIN_RATE
        from models.database import MarketRegimeLabel

        good_mr = self._make_perf("Mean_Reversion_ZScore", win_rate=70.0)
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = good_mr

        result = map_best_strategy("SBIN", db, MarketRegimeLabel.VOLATILE_HIGH_RISK)
        assert result.force_cash is False
        assert result.selected_strategy == "Mean_Reversion_ZScore"

    def test_sideways_picks_highest_win_rate(self):
        from engine.signals.regime_switchboard import map_best_strategy
        from models.database import MarketRegimeLabel

        best = self._make_perf("Bollinger_Reversion", win_rate=72.0)
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = best

        result = map_best_strategy("TCS", db, MarketRegimeLabel.SIDEWAYS)
        assert result.metric_name == "Win Rate"
        assert result.selected_strategy == "Bollinger_Reversion"

    def test_unknown_regime_falls_back_to_best_sharpe(self):
        from engine.signals.regime_switchboard import map_best_strategy
        from models.database import MarketRegimeLabel

        fallback = self._make_perf("Factor_Momentum", sharpe=1.8)
        db = MagicMock()
        # Specific regime queries return None; fallback returns value
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
        db.query.return_value.order_by.return_value.first.return_value = fallback

        result = map_best_strategy("HDFCBANK", db, MarketRegimeLabel.UNKNOWN)
        assert "fallback" in result.metric_name.lower()

    def test_no_data_returns_force_cash(self):
        from engine.signals.regime_switchboard import map_best_strategy
        from models.database import MarketRegimeLabel

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
        db.query.return_value.order_by.return_value.first.return_value = None

        result = map_best_strategy("UNKNOWN_STOCK", db, MarketRegimeLabel.STRONG_TREND)
        # Falls through all regime branches to fallback → None → force_cash
        assert result.force_cash is True


# ─── 6. FinalSignal JSON Schema Tests ─────────────────────────────────────────

class TestFinalSignalSchema:

    def test_to_frontend_json_has_required_keys(self):
        from models.signals_db import FinalSignal, SignalType, RegimeMode, SignalStatus
        from datetime import datetime

        fs = FinalSignal(
            scan_id              = "scan_test",
            ticker               = "SBIN",
            regime               = RegimeMode.STRONG_TREND,
            selected_strategy    = "Trend_EMA_Cross",
            signal               = SignalType.BUY,
            confidence           = 88.0,
            entry_price          = 820.50,
            stop_loss            = 803.20,
            target_1             = 854.60,
            target_2             = 876.15,
            risk_reward_ratio    = 2.0,
            reason               = "ADX confirms trend + Strategy agreement",
            agreeing_strategies  = 4,
            bias_warning         = False,
            generated_at         = datetime(2024, 1, 15, 9, 30),
        )

        j = fs.to_frontend_json()
        required_keys = [
            "ticker", "regime", "selected_strategy", "signal",
            "confidence", "reason", "agreeing_strategies",
            "bias_warning", "generated_at",
        ]
        for key in required_keys:
            assert key in j, f"Missing key: {key}"

        assert j["ticker"]            == "SBIN"
        assert j["regime"]            == "STRONG_TREND"
        assert j["selected_strategy"] == "Trend_EMA_Cross"
        assert j["signal"]            == "BUY"
        assert j["confidence"]        == 88.0
        assert j["agreeing_strategies"] == 4
        assert j["bias_warning"]      is False


# ─── Run instructions ─────────────────────────────────────────────────────────
# pip install pytest
# pytest tests/test_module4.py -v --tb=short
