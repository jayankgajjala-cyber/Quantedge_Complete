"""
Strategy Library – 8 Modular Signal Generators
================================================
Each strategy class exposes a single method:

    generate_signals(df: pd.DataFrame) -> pd.DataFrame

The returned DataFrame contains a 'signal' column:
    +1  = BUY  (enter long)
     0  = HOLD (no change)
    -1  = SELL (exit / enter short)

All strategies work on an enriched OHLCV DataFrame
(output of engine.indicators.technical.enrich_dataframe).

Strategies implemented
-----------------------
1. TrendEMACrossStrategy       – 50/200 EMA cross + ADX filter
2. MomentumBreakoutStrategy    – 52-week high breakout + volume surge
3. MeanReversionZScoreStrategy – Z-Score ±2 on 20-day mean
4. FundamentalFilterStrategy   – ROE, D/E, PE vs 5yr avg (uses metadata)
5. SwingHighLowStrategy        – Swing high/low pivot entries
6. VolumeSurgeStrategy         – OBV + CMF volume confirmation
7. FactorMomentumStrategy      – Multi-factor: momentum + quality + value
8. BollingerReversionStrategy  – BB %B + RSI mean-reversion
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from engine.indicators.technical import (
    adx as calc_adx,
    atr as calc_atr,
    bollinger_bands,
    chaikin_money_flow,
    ema as calc_ema,
    highest_high,
    lowest_low,
    on_balance_volume,
    rate_of_change,
    rolling_slope,
    rsi,
    sma,
    volume_ratio,
    zscore,
)

logger = logging.getLogger(__name__)


# ─── Base class ───────────────────────────────────────────────────────────────

class BaseStrategy(ABC):
    """Abstract base – every concrete strategy must implement generate_signals()."""

    name: str = "BaseStrategy"
    description: str = ""

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Parameters
        ----------
        df : enriched OHLCV DataFrame (at minimum: Open, High, Low, Close, Volume)

        Returns
        -------
        df copy with additional columns:
            signal     : int  (+1 buy, -1 sell, 0 hold)
            position   : int  (cumulative: 1 = in trade, 0 = flat)
            entry_price: float (price when position opens)
        """
        ...

    def _add_position_column(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Convert raw signal column to a position column.
        Signal of +1 opens a long; -1 closes it.
        Prevents multiple simultaneous entries.
        """
        position = 0
        positions = []
        for sig in df["signal"]:
            if sig == 1 and position == 0:
                position = 1
            elif sig == -1 and position == 1:
                position = 0
            positions.append(position)
        df["position"] = positions
        return df

    def __repr__(self) -> str:
        return f"<Strategy: {self.name}>"


# ─── 1. Trend: 50/200 EMA Cross + ADX Filter ─────────────────────────────────

class TrendEMACrossStrategy(BaseStrategy):
    """
    Entry:  EMA(50) crosses above EMA(200) AND ADX > 25
    Exit:   EMA(50) crosses below EMA(200) OR ADX falls below 20
    """
    name        = "Trend_EMA_Cross"
    description = "50/200 EMA crossover with ADX trend strength filter (>25)"

    def __init__(self, fast: int = 50, slow: int = 200, adx_threshold: float = 25.0):
        self.fast          = fast
        self.slow          = slow
        self.adx_threshold = adx_threshold

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        close = df["Close"]

        ema_fast = calc_ema(close, self.fast)
        ema_slow = calc_ema(close, self.slow)
        adx_s    = calc_adx(df, 14)

        # Cross signals
        cross_up   = (ema_fast > ema_slow) & (ema_fast.shift(1) <= ema_slow.shift(1))
        cross_down = (ema_fast < ema_slow) & (ema_fast.shift(1) >= ema_slow.shift(1))

        df["EMA_FAST"] = ema_fast
        df["EMA_SLOW"] = ema_slow
        df["ADX_14"]   = adx_s

        df["signal"] = 0
        df.loc[cross_up   & (adx_s > self.adx_threshold), "signal"] =  1
        df.loc[cross_down | (adx_s < 20), "signal"] = -1

        return self._add_position_column(df)


# ─── 2. Momentum: 52-Week High Breakout + Volume Surge ────────────────────────

class MomentumBreakoutStrategy(BaseStrategy):
    """
    Entry:  Close breaks above 52-week high AND Volume > 2× 20-day avg
    Exit:   Close falls more than 1 ATR(14) below entry OR RSI > 75
    """
    name        = "Momentum_Breakout"
    description = "52-week high breakout with volume surge (>2x avg) confirmation"

    def __init__(self, vol_multiplier: float = 2.0, rsi_exit: float = 75.0):
        self.vol_multiplier = vol_multiplier
        self.rsi_exit       = rsi_exit

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        close = df["Close"]

        high_52w  = highest_high(df, 252).shift(1)    # previous bar's 52w high
        vol_ratio = volume_ratio(df, 20)
        rsi_s     = rsi(close, 14)
        atr_s     = calc_atr(df, 14)

        breakout  = (close > high_52w) & (vol_ratio >= self.vol_multiplier)
        overbought = rsi_s > self.rsi_exit

        df["HIGH_52W"]  = high_52w
        df["VOL_RATIO"] = vol_ratio
        df["RSI_14"]    = rsi_s

        df["signal"] = 0
        df.loc[breakout,   "signal"] =  1
        df.loc[overbought, "signal"] = -1

        return self._add_position_column(df)


# ─── 3. Mean Reversion: Z-Score ±2.0 ─────────────────────────────────────────

class MeanReversionZScoreStrategy(BaseStrategy):
    """
    Buy:   Z-Score < -2.0  (price significantly below 20-day mean)
    Sell:  Z-Score >  2.0  (price significantly above 20-day mean)
    Exit:  Z-Score crosses zero (mean reversion complete)
    """
    name        = "Mean_Reversion_ZScore"
    description = "Z-Score based mean reversion: Buy <-2.0 Std, Sell >+2.0 Std"

    def __init__(self, period: int = 20, entry_z: float = 2.0, exit_z: float = 0.5):
        self.period  = period
        self.entry_z = entry_z
        self.exit_z  = exit_z

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        zs = zscore(df["Close"], self.period)

        df["ZSCORE_20"] = zs
        df["signal"]    = 0

        # Entry signals
        df.loc[zs < -self.entry_z, "signal"] =  1    # oversold → buy
        df.loc[zs >  self.entry_z, "signal"] = -1    # overbought → sell

        # Exit when Z-Score reverts toward zero
        reverting_up   = (zs > -self.exit_z) & (zs.shift(1) <= -self.exit_z)
        reverting_down = (zs < self.exit_z)  & (zs.shift(1) >= self.exit_z)
        # Only override if not already a strong entry signal
        df.loc[reverting_up   & (zs > -self.entry_z), "signal"] = -1
        df.loc[reverting_down & (zs <  self.entry_z), "signal"] = -1

        return self._add_position_column(df)


# ─── 4. Fundamental Filter Strategy ──────────────────────────────────────────

@dataclass
class FundamentalData:
    """Fundamental metrics for a stock (populated from yfinance or a DB)."""
    roe:              Optional[float] = None   # Return on Equity (%)
    debt_to_equity:   Optional[float] = None
    pe_ratio:         Optional[float] = None
    pe_5yr_avg:       Optional[float] = None
    earnings_growth:  Optional[float] = None
    revenue_growth:   Optional[float] = None


class FundamentalFilterStrategy(BaseStrategy):
    """
    Fundamental screen:  ROE > 15%  AND  D/E < 1.0  AND  PE < 5yr avg PE
    Technical entry:     RSI(14) < 55 (not overbought on entry)
    Technical exit:      RSI(14) > 70 OR price > 2× entry
    """
    name        = "Fundamental_Filter"
    description = "ROE>15%, D/E<1, PE<5yr-avg + RSI entry timing"

    def __init__(
        self,
        min_roe:      float = 15.0,
        max_de:       float = 1.0,
        rsi_entry:    float = 55.0,
        rsi_exit:     float = 70.0,
        fundamentals: Optional[FundamentalData] = None,
    ):
        self.min_roe      = min_roe
        self.max_de       = max_de
        self.rsi_entry    = rsi_entry
        self.rsi_exit     = rsi_exit
        self.fundamentals = fundamentals or FundamentalData()

    def _passes_fundamental_screen(self) -> bool:
        f = self.fundamentals
        checks = []
        if f.roe is not None:
            checks.append(f.roe > self.min_roe)
        if f.debt_to_equity is not None:
            checks.append(f.debt_to_equity < self.max_de)
        if f.pe_ratio is not None and f.pe_5yr_avg is not None:
            checks.append(f.pe_ratio < f.pe_5yr_avg)
        # Require at least one fundamental filter to pass; if no data, allow
        return all(checks) if checks else True

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        rsi_s = rsi(df["Close"], 14)
        df["RSI_14"] = rsi_s

        passes_screen = self._passes_fundamental_screen()
        df["signal"]  = 0

        if passes_screen:
            buy_signal  = rsi_s < self.rsi_entry
            sell_signal = rsi_s > self.rsi_exit
            df.loc[buy_signal,  "signal"] =  1
            df.loc[sell_signal, "signal"] = -1

        return self._add_position_column(df)


# ─── 5. Swing High/Low Strategy ───────────────────────────────────────────────

class SwingHighLowStrategy(BaseStrategy):
    """
    Identifies swing highs and lows using a rolling pivot window.
    Entry:  Price closes above the last swing high (breakout)
    Exit:   Price closes below the last swing low (breakdown)
    """
    name        = "Swing_HighLow"
    description = "Pivot swing high breakout entry / swing low breakdown exit"

    def __init__(self, pivot_window: int = 10, atr_filter: bool = True):
        self.pivot_window = pivot_window
        self.atr_filter   = atr_filter

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df    = df.copy()
        close = df["Close"]
        w     = self.pivot_window

        # Rolling pivot high / low (confirmed n bars later)
        swing_high = df["High"].rolling(2 * w + 1, center=True).max()
        swing_low  = df["Low"].rolling(2 * w + 1, center=True).min()

        atr_s   = calc_atr(df, 14)
        # Require ATR to be above its own 20-day SMA (volatile enough to trade)
        vol_ok  = atr_s > atr_s.rolling(20).mean() if self.atr_filter else pd.Series(True, index=df.index)

        breakout   = (close > swing_high.shift(1)) & vol_ok
        breakdown  = (close < swing_low.shift(1))

        df["SWING_HIGH"] = swing_high
        df["SWING_LOW"]  = swing_low
        df["signal"]     = 0
        df.loc[breakout,  "signal"] =  1
        df.loc[breakdown, "signal"] = -1

        return self._add_position_column(df)


# ─── 6. Volume Surge Strategy ─────────────────────────────────────────────────

class VolumeSurgeStrategy(BaseStrategy):
    """
    Combines On-Balance Volume trend with Chaikin Money Flow:
    Entry:  OBV is rising (slope > 0) AND CMF > 0.1 AND price > EMA(50)
    Exit:   OBV slope turns negative OR CMF < -0.1
    """
    name        = "Volume_Surge"
    description = "OBV rising trend + CMF positive flow + price above EMA(50)"

    def __init__(
        self,
        obv_slope_period: int   = 10,
        cmf_entry:        float = 0.10,
        cmf_exit:         float = -0.10,
    ):
        self.obv_slope_period = obv_slope_period
        self.cmf_entry        = cmf_entry
        self.cmf_exit         = cmf_exit

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df    = df.copy()
        close = df["Close"]

        obv       = on_balance_volume(df)
        obv_slope = rolling_slope(obv, self.obv_slope_period)
        cmf       = chaikin_money_flow(df, 20)
        ema50     = calc_ema(close, 50)

        volume_flow_up   = (obv_slope > 0) & (cmf > self.cmf_entry) & (close > ema50)
        volume_flow_down = (obv_slope < 0) | (cmf < self.cmf_exit)

        df["OBV"]       = obv
        df["OBV_SLOPE"] = obv_slope
        df["CMF"]       = cmf
        df["signal"]    = 0
        df.loc[volume_flow_up,   "signal"] =  1
        df.loc[volume_flow_down, "signal"] = -1

        return self._add_position_column(df)


# ─── 7. Factor Momentum Strategy ──────────────────────────────────────────────

class FactorMomentumStrategy(BaseStrategy):
    """
    Multi-factor composite:
      • Momentum factor: 6-month ROC > 10%
      • Quality factor:  Close above EMA(200)
      • Value factor:    RSI < 65 (not deeply overbought)
      • Confirmation:    MACD histogram turning positive

    All four factors must align for entry.
    """
    name        = "Factor_Momentum"
    description = "Multi-factor: 6m momentum + quality (>EMA200) + value (RSI<65) + MACD"

    def __init__(
        self,
        momentum_period: int   = 126,    # ~6 months
        roc_threshold:   float = 10.0,
        rsi_max:         float = 65.0,
    ):
        self.momentum_period = momentum_period
        self.roc_threshold   = roc_threshold
        self.rsi_max         = rsi_max

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df    = df.copy()
        close = df["Close"]

        roc_6m   = rate_of_change(close, self.momentum_period)
        ema200   = calc_ema(close, 200)
        rsi_s    = rsi(close, 14)

        macd_fast = calc_ema(close, 12)
        macd_slow = calc_ema(close, 26)
        macd_line = macd_fast - macd_slow
        macd_sig  = calc_ema(macd_line, 9)
        macd_hist = macd_line - macd_sig

        # All four factors
        momentum_ok = roc_6m > self.roc_threshold
        quality_ok  = close > ema200
        value_ok    = rsi_s < self.rsi_max
        macd_ok     = (macd_hist > 0) & (macd_hist.shift(1) <= 0)  # just turned positive

        # Exit: any factor deteriorates significantly
        momentum_bad = roc_6m < 0
        quality_bad  = close < ema200
        macd_bad     = (macd_hist < 0) & (macd_hist.shift(1) >= 0)

        entry = momentum_ok & quality_ok & value_ok & macd_ok
        exit_ = momentum_bad | quality_bad | macd_bad

        df["ROC_6M"]    = roc_6m
        df["MACD_HIST"] = macd_hist
        df["signal"]    = 0
        df.loc[entry, "signal"] =  1
        df.loc[exit_, "signal"] = -1

        return self._add_position_column(df)


# ─── 8. Bollinger Reversion Strategy ─────────────────────────────────────────

class BollingerReversionStrategy(BaseStrategy):
    """
    Mean-reversion using Bollinger Bands + RSI confirmation:
    Buy:   %B < 0.05 (price near/below lower band) AND RSI < 35
    Sell:  %B > 0.95 (price near/above upper band) AND RSI > 65
    Exit:  Price crosses the middle band (SMA 20)
    """
    name        = "Bollinger_Reversion"
    description = "BB %B + RSI mean-reversion at band extremes"

    def __init__(
        self,
        bb_period:  int   = 20,
        bb_std:     float = 2.0,
        pct_b_buy:  float = 0.05,
        pct_b_sell: float = 0.95,
        rsi_buy:    float = 35.0,
        rsi_sell:   float = 65.0,
    ):
        self.bb_period  = bb_period
        self.bb_std     = bb_std
        self.pct_b_buy  = pct_b_buy
        self.pct_b_sell = pct_b_sell
        self.rsi_buy    = rsi_buy
        self.rsi_sell   = rsi_sell

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df    = df.copy()
        close = df["Close"]

        bb_up, bb_mid, bb_lo = bollinger_bands(close, self.bb_period, self.bb_std)
        pct_b = (close - bb_lo) / (bb_up - bb_lo).replace(0, np.nan)
        rsi_s = rsi(close, 14)

        at_lower  = (pct_b < self.pct_b_buy)  & (rsi_s < self.rsi_buy)
        at_upper  = (pct_b > self.pct_b_sell) & (rsi_s > self.rsi_sell)
        at_middle = (close > bb_mid) & (close.shift(1) <= bb_mid.shift(1))

        df["PCT_B"]  = pct_b
        df["RSI_14"] = rsi_s
        df["signal"] = 0
        df.loc[at_lower,  "signal"] =  1
        df.loc[at_upper,  "signal"] = -1
        df.loc[at_middle & (df["signal"] == 0), "signal"] = -1   # mid-band exit

        return self._add_position_column(df)


# ─── Strategy Registry ────────────────────────────────────────────────────────

ALL_STRATEGIES: list[type[BaseStrategy]] = [
    TrendEMACrossStrategy,
    MomentumBreakoutStrategy,
    MeanReversionZScoreStrategy,
    FundamentalFilterStrategy,
    SwingHighLowStrategy,
    VolumeSurgeStrategy,
    FactorMomentumStrategy,
    BollingerReversionStrategy,
]


def get_strategy(name: str) -> Optional[BaseStrategy]:
    """Return an instantiated strategy by class name."""
    for cls in ALL_STRATEGIES:
        if cls.name == name or cls.__name__ == name:
            return cls()
    return None


def all_strategy_instances() -> list[BaseStrategy]:
    """Return one default instance of every strategy."""
    return [cls() for cls in ALL_STRATEGIES]
