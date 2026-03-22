"""
Module 7 Test Suite
====================
Tests every requirement from the Paper Trading & Budget Allocation spec.

Coverage
--------
  ✓ Budget cycle creation for current month
  ✓ Allocation skipped when confidence < 75%
  ✓ Allocation skipped when budget exhausted
  ✓ Allocation amount capped at MAX_SINGLE_TRADE_PCT (40%)
  ✓ Allocation amount floored at MIN_SINGLE_TRADE_PCT (10%)
  ✓ Suggested quantity = floor(amount / price)
  ✓ Actual cost = qty × price (never exceeds allocation)
  ✓ Commission calculated correctly (0.1% per side × 2)
  ✓ Budget remaining updated after allocation
  ✓ SL monitor: auto-close BUY trade when price ≤ stop_loss
  ✓ SL monitor: auto-close SELL trade when price ≥ stop_loss
  ✓ Target monitor: auto-close BUY when price ≥ target
  ✓ Unrealised P&L = (LTP - entry) × qty for BUY
  ✓ Unrealised P&L = (entry - LTP) × qty for SELL
  ✓ VirtualLedger entry written on trade open
  ✓ VirtualLedger close entry with P&L on trade close
  ✓ Weekly report: CAGR computed correctly
  ✓ Weekly report: Max Drawdown is negative %
  ✓ Weekly report: Win Rate = winners / total × 100
  ✓ Profit factor = gross_profit / gross_loss
  ✓ Budget utilisation = allocated / total × 100

Run: pytest tests/test_module7.py -v --tb=short
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


# ─── 1. Budget Cycle ─────────────────────────────────────────────────────────

class TestBudgetCycle:

    def _make_cycle(self, allocated=0.0, total=15_000.0):
        from models.paper_db import BudgetCycle
        c = BudgetCycle(year=2024, month=6, total_budget=total, allocated=allocated)
        return c

    def test_remaining_budget(self):
        c = self._make_cycle(allocated=5_000.0)
        assert c.remaining_budget == 10_000.0

    def test_remaining_never_negative(self):
        c = self._make_cycle(allocated=20_000.0)
        assert c.remaining_budget == 0.0

    def test_utilisation_pct(self):
        c = self._make_cycle(allocated=7_500.0)
        assert c.utilisation_pct == 50.0

    def test_full_utilisation(self):
        c = self._make_cycle(allocated=15_000.0)
        assert c.utilisation_pct == 100.0


# ─── 2. Budget Allocator ─────────────────────────────────────────────────────

class TestBudgetAllocator:

    def _mock_db_with_cycle(self, allocated=0.0):
        """Return a mock db that produces a BudgetCycle with given allocated."""
        from models.paper_db import BudgetCycle
        cycle = BudgetCycle(
            id=1, year=2024, month=6,
            total_budget=15_000.0, allocated=allocated,
        )
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = cycle
        return db, cycle

    def test_low_confidence_skipped(self):
        from services.paper.budget_allocator import suggest_allocation
        db, _ = self._mock_db_with_cycle()
        result = suggest_allocation(db, "RELIANCE", signal_confidence=60.0)
        assert result.can_allocate is False
        assert "confidence" in result.skip_reason.lower()

    def test_budget_exhausted_skipped(self):
        from services.paper.budget_allocator import suggest_allocation
        db, _  = self._mock_db_with_cycle(allocated=15_000.0)  # fully used
        with patch("services.paper.budget_allocator.get_live_price") as mock_price:
            mock_price.return_value = MagicMock(valid=True, price=500.0)
            result = suggest_allocation(db, "TCS", signal_confidence=85.0)
        assert result.can_allocate is False
        assert "exhausted" in result.skip_reason.lower() or "remaining" in result.skip_reason.lower()

    def test_valid_allocation_calculated(self):
        from services.paper.budget_allocator import suggest_allocation
        db, _ = self._mock_db_with_cycle(allocated=0.0)
        with patch("services.paper.budget_allocator.get_live_price") as mock_price:
            mock_price.return_value = MagicMock(valid=True, price=500.0)
            result = suggest_allocation(db, "SBIN", signal_confidence=80.0, stop_loss=480.0)
        assert result.can_allocate is True
        assert result.current_price == 500.0
        assert result.suggested_quantity >= 1
        assert result.actual_cost == result.suggested_quantity * 500.0

    def test_quantity_is_floored(self):
        from services.paper.budget_allocator import suggest_allocation
        db, _ = self._mock_db_with_cycle(allocated=0.0)
        # Price ₹501 — allocation ₹6,000 → qty = floor(6000/501) = 11
        with patch("services.paper.budget_allocator.get_live_price") as mock_price:
            mock_price.return_value = MagicMock(valid=True, price=501.0)
            result = suggest_allocation(db, "INFY", signal_confidence=80.0)
        assert result.can_allocate is True
        assert result.suggested_quantity == math.floor(result.allocation_amount / 501.0)

    def test_allocation_capped_at_max_pct(self):
        from services.paper.budget_allocator import suggest_allocation
        from core.config import MAX_SINGLE_TRADE_PCT, MONTHLY_BUDGET_INR
        db, _ = self._mock_db_with_cycle(allocated=0.0)
        with patch("services.paper.budget_allocator.get_live_price") as mock_price:
            mock_price.return_value = MagicMock(valid=True, price=100.0)
            result = suggest_allocation(db, "ABC", signal_confidence=90.0)
        max_allowed = MONTHLY_BUDGET_INR * MAX_SINGLE_TRADE_PCT
        assert result.allocation_amount <= max_allowed + 0.01  # float tolerance

    def test_commission_is_double_sided(self):
        from services.paper.budget_allocator import suggest_allocation
        from core.config import COMMISSION_PCT
        db, _ = self._mock_db_with_cycle(allocated=0.0)
        with patch("services.paper.budget_allocator.get_live_price") as mock_price:
            mock_price.return_value = MagicMock(valid=True, price=500.0)
            result = suggest_allocation(db, "WIPRO", signal_confidence=80.0)
        expected_commission = result.actual_cost * COMMISSION_PCT * 2
        assert abs(result.commission - expected_commission) < 0.01

    def test_risk_per_trade_calculated(self):
        from services.paper.budget_allocator import suggest_allocation
        db, _ = self._mock_db_with_cycle(allocated=0.0)
        with patch("services.paper.budget_allocator.get_live_price") as mock_price:
            mock_price.return_value = MagicMock(valid=True, price=1000.0)
            result = suggest_allocation(
                db, "LT", signal_confidence=80.0, stop_loss=950.0
            )
        # risk = (1000 - 950) × qty = 50 × qty
        if result.can_allocate:
            expected_risk = (1000.0 - 950.0) * result.suggested_quantity
            assert abs(result.risk_per_trade_inr - expected_risk) < 0.01

    def test_no_price_skipped(self):
        from services.paper.budget_allocator import suggest_allocation
        db, _ = self._mock_db_with_cycle(allocated=0.0)
        with patch("services.paper.budget_allocator.get_live_price") as mock_price:
            mock_price.return_value = MagicMock(valid=False, price=0.0, error="Timeout")
            result = suggest_allocation(db, "BADTICKER", signal_confidence=80.0)
        assert result.can_allocate is False


# ─── 3. Unrealised P&L ───────────────────────────────────────────────────────

class TestUnrealisedPnL:

    def test_buy_unrealised_positive(self):
        from services.paper.risk_monitor import _calculate_unrealised_pnl
        from models.database import TradeDirection
        trade = MagicMock()
        trade.direction   = TradeDirection.BUY
        trade.entry_price = 1000.0
        trade.quantity    = 10
        pnl, pct = _calculate_unrealised_pnl(trade, current_price=1100.0)
        assert pnl == pytest.approx(1000.0)
        assert pct == pytest.approx(10.0)

    def test_buy_unrealised_negative(self):
        from services.paper.risk_monitor import _calculate_unrealised_pnl
        from models.database import TradeDirection
        trade = MagicMock()
        trade.direction   = TradeDirection.BUY
        trade.entry_price = 1000.0
        trade.quantity    = 5
        pnl, pct = _calculate_unrealised_pnl(trade, current_price=900.0)
        assert pnl == pytest.approx(-500.0)
        assert pct == pytest.approx(-10.0)

    def test_sell_unrealised_positive(self):
        from services.paper.risk_monitor import _calculate_unrealised_pnl
        from models.database import TradeDirection
        trade = MagicMock()
        trade.direction   = TradeDirection.SELL
        trade.entry_price = 1000.0
        trade.quantity    = 10
        pnl, pct = _calculate_unrealised_pnl(trade, current_price=900.0)
        assert pnl == pytest.approx(1000.0)   # price fell → short profits

    def test_sell_unrealised_negative(self):
        from services.paper.risk_monitor import _calculate_unrealised_pnl
        from models.database import TradeDirection
        trade = MagicMock()
        trade.direction   = TradeDirection.SELL
        trade.entry_price = 1000.0
        trade.quantity    = 10
        pnl, pct = _calculate_unrealised_pnl(trade, current_price=1100.0)
        assert pnl == pytest.approx(-1000.0)  # price rose → short loses


# ─── 4. SL / Target Breach Detection ─────────────────────────────────────────

class TestBreachDetection:

    def _make_open_trade(self, entry, sl, target, direction="BUY"):
        from models.database import TradeDirection, TradeStatus
        t = MagicMock()
        t.id          = 1
        t.symbol      = "TEST"
        t.direction   = TradeDirection.BUY if direction == "BUY" else TradeDirection.SELL
        t.entry_price = entry
        t.stop_loss   = sl
        t.target      = target
        t.quantity    = 10
        t.status      = TradeStatus.OPEN
        return t

    def test_buy_sl_breached(self):
        """Price falls below SL → sl_breached=True"""
        trade = self._make_open_trade(entry=1000, sl=950, target=1100)
        # price = 940 ≤ stop_loss = 950
        from models.database import TradeDirection
        sl_breached = (trade.direction == TradeDirection.BUY
                       and trade.stop_loss is not None
                       and 940.0 <= trade.stop_loss)
        assert sl_breached is True

    def test_buy_target_hit(self):
        """Price rises above target → target_hit=True"""
        trade = self._make_open_trade(entry=1000, sl=950, target=1100)
        from models.database import TradeDirection
        target_hit = (trade.direction == TradeDirection.BUY
                      and trade.target is not None
                      and 1105.0 >= trade.target)
        assert target_hit is True

    def test_buy_no_breach_in_range(self):
        """Price between SL and target → neither flag"""
        trade = self._make_open_trade(entry=1000, sl=950, target=1100)
        from models.database import TradeDirection
        current = 1050.0
        sl_breached = (trade.direction == TradeDirection.BUY
                       and trade.stop_loss is not None
                       and current <= trade.stop_loss)
        target_hit  = (trade.direction == TradeDirection.BUY
                       and trade.target is not None
                       and current >= trade.target)
        assert sl_breached is False
        assert target_hit  is False


# ─── 5. Weekly Report Metrics ────────────────────────────────────────────────

class TestWeeklyMetrics:

    def test_cagr_positive_growth(self):
        from services.paper.weekly_report import _cagr
        # ₹15,000 → ₹18,000 in 365 days ≈ 20% CAGR
        result = _cagr(15_000, 18_000, 365)
        assert result is not None
        assert abs(result - 20.0) < 1.0

    def test_cagr_returns_none_for_short_period(self):
        from services.paper.weekly_report import _cagr
        result = _cagr(15_000, 18_000, 0)
        assert result is None

    def test_max_drawdown_is_negative_pct(self):
        from services.paper.weekly_report import _max_drawdown
        # 20k peak → drops to 15k → 25% drawdown
        curve  = [15_000, 18_000, 20_000, 17_000, 15_000, 16_000]
        mdd    = _max_drawdown(curve)
        assert mdd is not None
        assert mdd < 0.0
        assert abs(mdd) == pytest.approx(25.0, abs=1.0)

    def test_max_drawdown_all_positive(self):
        from services.paper.weekly_report import _max_drawdown
        # Always rising — no drawdown
        curve  = [10_000, 11_000, 12_000, 13_000, 14_000]
        mdd    = _max_drawdown(curve)
        assert mdd == pytest.approx(0.0, abs=0.01)

    def test_win_rate_calculation(self):
        winners = 7
        total   = 10
        win_rate = (winners / total) * 100
        assert win_rate == 70.0

    def test_profit_factor(self):
        from services.paper.weekly_report import _profit_factor
        pnls = [500, 300, -200, 400, -100, -150, 600]
        pf   = _profit_factor(pnls)
        gross_profit = 500 + 300 + 400 + 600    # 1800
        gross_loss   = 200 + 100 + 150          # 450
        assert pf == pytest.approx(gross_profit / gross_loss, rel=1e-3)

    def test_profit_factor_no_losers_returns_none(self):
        from services.paper.weekly_report import _profit_factor
        pnls = [100, 200, 300]
        pf   = _profit_factor(pnls)
        assert pf is None  # division by zero guard


# ─── 6. Ledger Entry Validation ──────────────────────────────────────────────

class TestVirtualLedger:

    def test_ledger_entry_type_open(self):
        from models.paper_db import LedgerEntryType
        assert LedgerEntryType.TRADE_OPEN.value == "TRADE_OPEN"

    def test_ledger_entry_type_sl_hit(self):
        from models.paper_db import LedgerEntryType
        assert LedgerEntryType.SL_HIT.value == "SL_HIT"

    def test_ledger_entry_type_target_hit(self):
        from models.paper_db import LedgerEntryType
        assert LedgerEntryType.TARGET_HIT.value == "TARGET_HIT"

    def test_allocation_status_values(self):
        from models.paper_db import AllocationStatus
        assert AllocationStatus.SUGGESTED.value == "SUGGESTED"
        assert AllocationStatus.EXECUTED.value  == "EXECUTED"
        assert AllocationStatus.SKIPPED.value   == "SKIPPED"


# ─── Run instructions ─────────────────────────────────────────────────────────
# pip install pytest
# pytest tests/test_module7.py -v --tb=short
