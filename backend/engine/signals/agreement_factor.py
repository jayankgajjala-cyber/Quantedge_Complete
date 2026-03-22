"""
Agreement Factor & Bias Guardrail
====================================
Implements the two cross-strategy validation rules:

Agreement Factor
----------------
If 3+ strategies (regardless of regime) agree on the same direction
for a ticker, add +20 to the base confidence score.

Bias Guardrail
--------------
If 80%+ of all signals across all tickers in a scan are HOLD,
flag the scan with: "Signal bias detected — checking for regime lag"
and demote confidence scores by 10 points.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from models.signals_db import SignalType

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

AGREEMENT_THRESHOLD   = 3       # strategies that must agree to earn the bonus
AGREEMENT_BONUS       = 20.0    # confidence points added when threshold met
BIAS_HOLD_PCT         = 0.80    # fraction of HOLD signals that triggers bias flag
BIAS_CONFIDENCE_PENALTY = 10.0  # confidence deducted when bias is detected


# ─── Per-ticker vote tally ────────────────────────────────────────────────────

@dataclass
class VoteTally:
    ticker:         str
    buy_votes:      int   = 0
    sell_votes:     int   = 0
    hold_votes:     int   = 0

    @property
    def total(self) -> int:
        return self.buy_votes + self.sell_votes + self.hold_votes

    @property
    def dominant_signal(self) -> SignalType:
        votes = {
            SignalType.BUY:  self.buy_votes,
            SignalType.SELL: self.sell_votes,
            SignalType.HOLD: self.hold_votes,
        }
        return max(votes, key=votes.get)

    @property
    def dominant_count(self) -> int:
        return max(self.buy_votes, self.sell_votes, self.hold_votes)

    @property
    def agreement_pct(self) -> float:
        if self.total == 0:
            return 0.0
        return self.dominant_count / self.total * 100

    @property
    def agreement_bonus(self) -> float:
        """Return AGREEMENT_BONUS if ≥ threshold strategies agree, else 0."""
        if self.dominant_count >= AGREEMENT_THRESHOLD:
            logger.debug(
                "Agreement bonus earned for %s: %d strategies agree on %s",
                self.ticker, self.dominant_count, self.dominant_signal.value,
            )
            return AGREEMENT_BONUS
        return 0.0


@dataclass
class AgreementResult:
    """Result for a single ticker after vote counting."""
    ticker:             str
    tally:              VoteTally
    agreement_bonus:    float
    dominant_signal:    SignalType
    agreement_pct:      float


@dataclass
class ScanBiasResult:
    """Scan-level bias detection result."""
    total_signals:      int
    hold_count:         int
    hold_pct:           float
    bias_detected:      bool
    confidence_penalty: float
    message:            str


# ─── Core functions ───────────────────────────────────────────────────────────

def compute_agreement(
    raw_signals: list[dict],     # list of {ticker, strategy_name, signal_type}
    ticker:      str,
) -> AgreementResult:
    """
    Count votes from all strategies for *ticker* and compute the bonus.

    Parameters
    ----------
    raw_signals : list of signal dicts (from LiveSignal rows)
    ticker      : the stock ticker to tally

    Returns
    -------
    AgreementResult with bonus, dominant signal, and agreement %
    """
    tally = VoteTally(ticker=ticker)

    ticker_signals = [s for s in raw_signals if s.get("ticker") == ticker]

    for sig in ticker_signals:
        stype = sig.get("signal_type")
        if stype == SignalType.BUY or stype == "BUY":
            tally.buy_votes += 1
        elif stype == SignalType.SELL or stype == "SELL":
            tally.sell_votes += 1
        else:
            tally.hold_votes += 1

    bonus = tally.agreement_bonus

    logger.info(
        "Agreement [%s]: BUY=%d SELL=%d HOLD=%d → %s (%.0f%%) bonus=+%.0f",
        ticker,
        tally.buy_votes, tally.sell_votes, tally.hold_votes,
        tally.dominant_signal.value,
        tally.agreement_pct,
        bonus,
    )

    return AgreementResult(
        ticker          = ticker,
        tally           = tally,
        agreement_bonus = bonus,
        dominant_signal = tally.dominant_signal,
        agreement_pct   = tally.agreement_pct,
    )


def detect_scan_bias(
    all_signals: list[dict],   # all {ticker, signal_type} from this scan
) -> ScanBiasResult:
    """
    Check whether the entire scan is dominated by HOLD signals.
    If ≥ 80% of signals are HOLD, raise a bias warning.

    Returns ScanBiasResult with a confidence penalty to apply globally.
    """
    if not all_signals:
        return ScanBiasResult(
            total_signals=0, hold_count=0, hold_pct=0.0,
            bias_detected=False, confidence_penalty=0.0,
            message="No signals to evaluate",
        )

    total = len(all_signals)
    holds = sum(
        1 for s in all_signals
        if s.get("signal_type") in (SignalType.HOLD, "HOLD")
    )
    hold_pct = holds / total

    bias = hold_pct >= BIAS_HOLD_PCT
    message = ""
    penalty = 0.0

    if bias:
        penalty = BIAS_CONFIDENCE_PENALTY
        message = (
            f"Signal bias detected — checking for regime lag. "
            f"{holds}/{total} signals ({hold_pct*100:.0f}%) are HOLD. "
            f"The regime classification may be stale or the market is "
            f"in a low-volatility consolidation phase."
        )
        logger.warning(message)
    else:
        message = f"No bias: {holds}/{total} HOLD ({hold_pct*100:.0f}%)"

    return ScanBiasResult(
        total_signals     = total,
        hold_count        = holds,
        hold_pct          = hold_pct,
        bias_detected     = bias,
        confidence_penalty= penalty,
        message           = message,
    )


def apply_confidence_adjustments(
    base_confidence:  float,
    agreement_bonus:  float,
    bias_penalty:     float,
    min_score:        float = 5.0,
    max_score:        float = 100.0,
) -> float:
    """
    Apply bonuses and penalties to a base confidence score.

    Formula:  final = clamp(base + agreement_bonus - bias_penalty, 5, 100)
    """
    final = base_confidence + agreement_bonus - bias_penalty
    return round(max(min_score, min(max_score, final)), 1)
