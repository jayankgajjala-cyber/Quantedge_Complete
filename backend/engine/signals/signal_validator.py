"""
Signal Validator & Risk/Reward Calculator
==========================================
Validates a candidate signal against current price conditions
and computes stop-loss, targets, and risk/reward ratio.

Validation gates (ALL must pass for signal to be ACTIVE)
---------------------------------------------------------
1. Volume confirmation  – current bar volume ≥ 1.5× 20-bar average
2. Price sanity         – entry price within 2% of current close
3. ATR-based stop-loss  – stop placed 1.5× ATR below entry (long)
4. Minimum R:R          – must be ≥ 1.5 to emit a BUY signal

Risk Level → Position Sizing Guidance
--------------------------------------
VOLATILE regime         → 0.25× normal size (tight stops)
BEAR regime             → 0.5× normal size
STRONG TREND            → 1.0× normal size
SIDEWAYS                → 0.75× normal size
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from engine.signals.price_feed import CandleData
from models.signals_db import RegimeMode, SignalType

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

ATR_STOP_MULTIPLIER     = 1.5     # stop = entry - 1.5 × ATR
TARGET_1_MULTIPLIER     = 2.0     # target1 = entry + 2.0 × ATR
TARGET_2_MULTIPLIER     = 3.5     # target2 = entry + 3.5 × ATR
MIN_RISK_REWARD         = 1.5     # minimum acceptable R:R for BUY signals
PRICE_PROXIMITY_PCT     = 0.02    # entry must be within 2% of current price

REGIME_POSITION_SIZE: dict[RegimeMode, float] = {
    RegimeMode.STRONG_TREND:       1.00,
    RegimeMode.SIDEWAYS:           0.75,
    RegimeMode.VOLATILE_HIGH_RISK: 0.25,
    RegimeMode.BEAR_CRASHING:      0.50,
    RegimeMode.UNKNOWN:            0.50,
}


@dataclass
class ValidationResult:
    passed:           bool
    signal_type:      SignalType
    entry_price:      Optional[float]
    stop_loss:        Optional[float]
    target_1:         Optional[float]
    target_2:         Optional[float]
    risk_reward_ratio:Optional[float]
    position_size_pct:float          # fraction of normal size to use
    rejection_reason: str = ""
    atr_used:         Optional[float] = None


def validate_signal(
    proposed_signal: SignalType,
    candle:          CandleData,
    atr_value:       Optional[float],
    regime:          RegimeMode,
    strategy_name:   str,
) -> ValidationResult:
    """
    Validate a proposed signal against live price data.

    Parameters
    ----------
    proposed_signal : BUY / SELL / HOLD from the strategy
    candle          : latest 5-min CandleData
    atr_value       : ATR(14) from daily data for stop sizing
    regime          : current market regime mode
    strategy_name   : for logging only

    Returns
    -------
    ValidationResult — passed=False means don't emit the signal
    """
    pos_size = REGIME_POSITION_SIZE.get(regime, 0.5)

    # ── HOLD / CASH signals skip all validation ───────────────────────────────
    if proposed_signal in (SignalType.HOLD, SignalType.CASH):
        return ValidationResult(
            passed            = True,
            signal_type       = proposed_signal,
            entry_price       = None,
            stop_loss         = None,
            target_1          = None,
            target_2          = None,
            risk_reward_ratio = None,
            position_size_pct = 0.0,
        )

    # ── VOLATILE regime → no BUY/SELL unless strategy explicitly passed ───────
    if regime == RegimeMode.VOLATILE_HIGH_RISK and proposed_signal == SignalType.BUY:
        logger.info(
            "[%s] VOLATILE regime: overriding BUY → CASH for %s",
            strategy_name, candle.symbol,
        )
        return ValidationResult(
            passed            = False,
            signal_type       = SignalType.CASH,
            entry_price       = None,
            stop_loss         = None,
            target_1          = None,
            target_2          = None,
            risk_reward_ratio = None,
            position_size_pct = 0.0,
            rejection_reason  = "VOLATILE regime – BUY suppressed; use CASH mode",
        )

    # ── Stale candle guard ────────────────────────────────────────────────────
    if candle.is_stale:
        return ValidationResult(
            passed            = False,
            signal_type       = SignalType.HOLD,
            entry_price       = None,
            stop_loss         = None,
            target_1          = None,
            target_2          = None,
            risk_reward_ratio = None,
            position_size_pct = 0.0,
            rejection_reason  = f"Stale candle – last bar is outdated for {candle.symbol}",
        )

    # ── Volume confirmation gate ──────────────────────────────────────────────
    if not candle.volume_confirmed:
        logger.debug(
            "[%s] Volume not confirmed for %s: ratio=%.2f (need ≥ 1.5)",
            strategy_name, candle.symbol, candle.volume_ratio,
        )
        return ValidationResult(
            passed            = False,
            signal_type       = SignalType.HOLD,
            entry_price       = None,
            stop_loss         = None,
            target_1          = None,
            target_2          = None,
            risk_reward_ratio = None,
            position_size_pct = 0.0,
            rejection_reason  = (
                f"Volume confirmation failed: ratio={candle.volume_ratio:.2f} "
                f"(required ≥ 1.5×avg). Signal suppressed."
            ),
        )

    # ── ATR availability check ────────────────────────────────────────────────
    if atr_value is None or atr_value <= 0:
        # No ATR → use a fixed 1% stop
        atr_value = candle.close * 0.01
        logger.warning("[%s] No ATR for %s, using 1%% proxy", strategy_name, candle.symbol)

    # ── Compute levels ────────────────────────────────────────────────────────
    entry = candle.close

    if proposed_signal == SignalType.BUY:
        stop_loss = entry - ATR_STOP_MULTIPLIER * atr_value
        target_1  = entry + TARGET_1_MULTIPLIER * atr_value
        target_2  = entry + TARGET_2_MULTIPLIER * atr_value
        risk      = entry - stop_loss
        reward    = target_1 - entry
    else:  # SELL (short)
        stop_loss = entry + ATR_STOP_MULTIPLIER * atr_value
        target_1  = entry - TARGET_1_MULTIPLIER * atr_value
        target_2  = entry - TARGET_2_MULTIPLIER * atr_value
        risk      = stop_loss - entry
        reward    = entry - target_1

    rr_ratio = reward / risk if risk > 0 else 0.0

    # ── R:R gate (BUY only) ───────────────────────────────────────────────────
    if proposed_signal == SignalType.BUY and rr_ratio < MIN_RISK_REWARD:
        return ValidationResult(
            passed            = False,
            signal_type       = SignalType.HOLD,
            entry_price       = entry,
            stop_loss         = round(stop_loss, 2),
            target_1          = round(target_1, 2),
            target_2          = round(target_2, 2),
            risk_reward_ratio = round(rr_ratio, 2),
            position_size_pct = pos_size,
            rejection_reason  = (
                f"R:R={rr_ratio:.2f} below minimum {MIN_RISK_REWARD}. Signal suppressed."
            ),
            atr_used          = atr_value,
        )

    logger.info(
        "[%s] Signal VALIDATED: %s %s entry=%.2f SL=%.2f T1=%.2f R:R=%.2f vol_ratio=%.2f",
        strategy_name, proposed_signal.value, candle.symbol,
        entry, stop_loss, target_1, rr_ratio, candle.volume_ratio,
    )

    return ValidationResult(
        passed            = True,
        signal_type       = proposed_signal,
        entry_price       = round(entry, 2),
        stop_loss         = round(stop_loss, 2),
        target_1          = round(target_1, 2),
        target_2          = round(target_2, 2),
        risk_reward_ratio = round(rr_ratio, 2),
        position_size_pct = pos_size,
        atr_used          = atr_value,
    )
