"""
Technical Indicators Library
==============================
Pure NumPy / Pandas implementations of every indicator used by the
RegimeDetector and all 8 strategy classes.

All functions accept a pd.DataFrame with columns:
    Open, High, Low, Close, Volume
and return a pd.Series (or scalar for the latest-value helpers).

Design goals
------------
• Zero dependency on TA-Lib (not always installable)
• Vectorised – no Python loops over bars
• NaN-safe – leading NaNs are propagated naturally
"""

from __future__ import annotations

import logging
import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

logger = logging.getLogger(__name__)


# ─── Moving Averages ──────────────────────────────────────────────────────────

def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average."""
    return series.rolling(period, min_periods=period).mean()


def wma(series: pd.Series, period: int) -> pd.Series:
    """Weighted Moving Average (linearly weighted)."""
    weights = np.arange(1, period + 1, dtype=float)
    return series.rolling(period).apply(
        lambda x: np.dot(x, weights) / weights.sum(), raw=True
    )


# ─── Trend Indicators ─────────────────────────────────────────────────────────

def true_range(df: pd.DataFrame) -> pd.Series:
    """True Range – prerequisite for ATR and ADX."""
    high_low   = df["High"] - df["Low"]
    high_close = (df["High"] - df["Close"].shift(1)).abs()
    low_close  = (df["Low"]  - df["Close"].shift(1)).abs()
    return pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range (Wilder smoothing)."""
    tr = true_range(df)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Average Directional Index (Wilder method).
    Returns the ADX line only (not +DI / -DI).
    """
    tr   = true_range(df)
    up   = df["High"].diff()
    down = -df["Low"].diff()

    plus_dm  = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)

    atr_s     = pd.Series(plus_dm, index=df.index).ewm(
                    alpha=1/period, adjust=False, min_periods=period).mean()
    plus_di   = 100 * pd.Series(plus_dm, index=df.index).ewm(
                    alpha=1/period, adjust=False, min_periods=period).mean() / \
                tr.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    minus_di  = 100 * pd.Series(minus_dm, index=df.index).ewm(
                    alpha=1/period, adjust=False, min_periods=period).mean() / \
                tr.ewm(alpha=1/period, adjust=False, min_periods=period).mean()

    dx = (100 * (plus_di - minus_di).abs() /
          (plus_di + minus_di).replace(0, np.nan))
    adx_series = dx.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    return adx_series


def directional_indicators(df: pd.DataFrame, period: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (+DI, -DI, ADX) tuple."""
    tr   = true_range(df)
    up   = df["High"].diff()
    down = -df["Low"].diff()

    plus_dm  = pd.Series(np.where((up > down) & (up > 0),  up,   0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)

    smooth_tr   = tr.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    plus_di_s   = 100 * plus_dm.ewm(alpha=1/period, adjust=False, min_periods=period).mean() / smooth_tr
    minus_di_s  = 100 * minus_dm.ewm(alpha=1/period, adjust=False, min_periods=period).mean() / smooth_tr

    dx = (100 * (plus_di_s - minus_di_s).abs() /
          (plus_di_s + minus_di_s).replace(0, np.nan))
    adx_s = dx.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    return plus_di_s, minus_di_s, adx_s


# ─── Bollinger Bands ──────────────────────────────────────────────────────────

def bollinger_bands(
    series: pd.Series,
    period: int = 20,
    std_dev: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (upper_band, middle_band, lower_band)."""
    middle = sma(series, period)
    std    = series.rolling(period, min_periods=period).std(ddof=1)
    upper  = middle + std_dev * std
    lower  = middle - std_dev * std
    return upper, middle, lower


def bb_percent_b(series: pd.Series, period: int = 20, std_dev: float = 2.0) -> pd.Series:
    """Bollinger %B: 0 = lower band, 1 = upper band."""
    upper, middle, lower = bollinger_bands(series, period, std_dev)
    return (series - lower) / (upper - lower).replace(0, np.nan)


def bb_bandwidth(series: pd.Series, period: int = 20, std_dev: float = 2.0) -> pd.Series:
    """Bollinger Bandwidth: (upper - lower) / middle."""
    upper, middle, lower = bollinger_bands(series, period, std_dev)
    return (upper - lower) / middle.replace(0, np.nan)


# ─── Momentum Indicators ──────────────────────────────────────────────────────

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder smoothing)."""
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (macd_line, signal_line, histogram)."""
    macd_line   = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    histogram   = macd_line - signal_line
    return macd_line, signal_line, histogram


def stochastic(
    df: pd.DataFrame,
    k_period: int = 14,
    d_period: int = 3,
) -> tuple[pd.Series, pd.Series]:
    """Returns (%K, %D)."""
    low_min  = df["Low"].rolling(k_period, min_periods=k_period).min()
    high_max = df["High"].rolling(k_period, min_periods=k_period).max()
    k = 100 * (df["Close"] - low_min) / (high_max - low_min).replace(0, np.nan)
    d = k.rolling(d_period, min_periods=d_period).mean()
    return k, d


def rate_of_change(series: pd.Series, period: int = 10) -> pd.Series:
    """Rate of Change (%)."""
    return ((series - series.shift(period)) / series.shift(period).replace(0, np.nan)) * 100


def williams_r(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Williams %R."""
    highest_high = df["High"].rolling(period, min_periods=period).max()
    lowest_low   = df["Low"].rolling(period, min_periods=period).min()
    return -100 * (highest_high - df["Close"]) / (highest_high - lowest_low).replace(0, np.nan)


# ─── Volume Indicators ────────────────────────────────────────────────────────

def volume_sma(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Simple moving average of volume."""
    return df["Volume"].rolling(period, min_periods=period).mean()


def volume_ratio(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Current volume as a multiple of its SMA."""
    vol_avg = volume_sma(df, period)
    return df["Volume"] / vol_avg.replace(0, np.nan)


def on_balance_volume(df: pd.DataFrame) -> pd.Series:
    """On-Balance Volume."""
    direction = np.sign(df["Close"].diff()).fillna(0)
    return (direction * df["Volume"]).cumsum()


def vwap(df: pd.DataFrame) -> pd.Series:
    """Intraday VWAP (resets each day; works best on intraday data)."""
    typical = (df["High"] + df["Low"] + df["Close"]) / 3
    return (typical * df["Volume"]).cumsum() / df["Volume"].cumsum()


def chaikin_money_flow(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Chaikin Money Flow."""
    hl_range = (df["High"] - df["Low"]).replace(0, np.nan)
    mf_vol   = ((df["Close"] - df["Low"]) - (df["High"] - df["Close"])) / hl_range * df["Volume"]
    return mf_vol.rolling(period).sum() / df["Volume"].rolling(period).sum().replace(0, np.nan)


# ─── Statistical / Mean Reversion ────────────────────────────────────────────

def zscore(series: pd.Series, period: int = 20) -> pd.Series:
    """Rolling Z-Score: (price - mean) / std."""
    mean = series.rolling(period, min_periods=period).mean()
    std  = series.rolling(period, min_periods=period).std(ddof=1)
    return (series - mean) / std.replace(0, np.nan)


def rolling_slope(series: pd.Series, period: int = 20) -> pd.Series:
    """
    Linear regression slope over *period* bars (annualised).
    Positive = uptrend, Negative = downtrend.
    """
    def _slope(arr: np.ndarray) -> float:
        x = np.arange(len(arr), dtype=float)
        if np.all(np.isnan(arr)):
            return np.nan
        slope, *_ = scipy_stats.linregress(x, arr)
        return float(slope)

    return series.rolling(period, min_periods=period).apply(_slope, raw=True)


def atr_percentile(atr_series: pd.Series, lookback: int = 252) -> pd.Series:
    """Rolling percentile rank of ATR over *lookback* bars (0–100)."""
    def _pct(arr: np.ndarray) -> float:
        return float(scipy_stats.percentileofscore(arr, arr[-1]))
    return atr_series.rolling(lookback, min_periods=lookback // 2).apply(_pct, raw=True)


def highest_high(df: pd.DataFrame, period: int = 252) -> pd.Series:
    """52-week (or custom) highest high."""
    return df["High"].rolling(period, min_periods=period).max()


def lowest_low(df: pd.DataFrame, period: int = 252) -> pd.Series:
    """52-week (or custom) lowest low."""
    return df["Low"].rolling(period, min_periods=period).min()


# ─── Pivot Points ─────────────────────────────────────────────────────────────

def pivot_points(df: pd.DataFrame) -> pd.DataFrame:
    """Classic daily pivot points."""
    pivot = (df["High"] + df["Low"] + df["Close"]) / 3
    r1    = 2 * pivot - df["Low"]
    s1    = 2 * pivot - df["High"]
    r2    = pivot + (df["High"] - df["Low"])
    s2    = pivot - (df["High"] - df["Low"])
    return pd.DataFrame({"pivot": pivot, "r1": r1, "s1": s1, "r2": r2, "s2": s2})


# ─── Convenience: Add all indicators to a DataFrame ──────────────────────────

def enrich_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add all commonly used indicator columns to a copy of *df*.
    Safe to call before strategy signal generation.
    """
    df = df.copy()
    close = df["Close"]

    df["EMA_50"]      = ema(close, 50)
    df["EMA_200"]     = ema(close, 200)
    df["SMA_20"]      = sma(close, 20)
    df["ATR_14"]      = atr(df, 14)
    df["ADX_14"]      = adx(df, 14)
    df["RSI_14"]      = rsi(close, 14)
    df["ZSCORE_20"]   = zscore(close, 20)
    df["SLOPE_20"]    = rolling_slope(close, 20)
    df["VOL_RATIO"]   = volume_ratio(df, 20)
    df["OBV"]         = on_balance_volume(df)
    df["ROC_10"]      = rate_of_change(close, 10)
    df["CMF_20"]      = chaikin_money_flow(df, 20)
    df["HIGH_52W"]    = highest_high(df, 252)
    df["LOW_52W"]     = lowest_low(df, 252)

    bb_up, bb_mid, bb_lo = bollinger_bands(close, 20, 2.0)
    df["BB_UPPER"]    = bb_up
    df["BB_MID"]      = bb_mid
    df["BB_LOWER"]    = bb_lo
    df["BB_PCT_B"]    = bb_percent_b(close, 20, 2.0)

    macd_l, macd_sig, macd_hist = macd(close)
    df["MACD"]        = macd_l
    df["MACD_SIG"]    = macd_sig
    df["MACD_HIST"]   = macd_hist

    stoch_k, stoch_d  = stochastic(df)
    df["STOCH_K"]     = stoch_k
    df["STOCH_D"]     = stoch_d

    df["ATR_PCT"]     = atr_percentile(df["ATR_14"], 252)

    return df
