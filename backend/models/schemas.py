"""
Pydantic schemas for request validation and API responses.
"""

from __future__ import annotations
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, validator

from models.database import DataQuality, Interval, TradeDirection, TradeStatus


# ─── Holdings ─────────────────────────────────────────────────────────────────

class HoldingOut(BaseModel):
    id:             int
    symbol:         str
    isin:           Optional[str]
    exchange:       str
    quantity:       float
    average_price:  float
    current_price:  Optional[float]
    pnl:            Optional[float]
    pnl_pct:        Optional[float]
    sector:         Optional[str]
    uploaded_at:    datetime
    data_quality:   DataQuality

    class Config:
        from_attributes = True


class PortfolioUploadResponse(BaseModel):
    message:         str
    total_rows:      int
    imported:        int
    skipped:         int
    quality_summary: dict
    holdings:        List[HoldingOut]


# ─── HistoricalData ────────────────────────────────────────────────────────────

class HistoricalDataOut(BaseModel):
    id:           int
    symbol:       str
    interval:     Interval
    timestamp:    datetime
    open:         float
    high:         float
    low:          float
    close:        float
    volume:       float
    data_quality: DataQuality

    class Config:
        from_attributes = True


class FetchHistoricalRequest(BaseModel):
    symbol:   str = Field(..., min_length=1, max_length=50)
    interval: Interval = Interval.DAILY

    @validator("symbol")
    def upper_symbol(cls, v):
        return v.strip().upper()


class FetchHistoricalResponse(BaseModel):
    symbol:       str
    interval:     Interval
    records:      int
    years:        float
    data_quality: DataQuality
    message:      str


# ─── BacktestResults ──────────────────────────────────────────────────────────

class BacktestResultOut(BaseModel):
    id:                 int
    strategy_name:      str
    symbol:             str
    interval:           Interval
    start_date:         datetime
    end_date:           datetime
    initial_capital:    float
    final_capital:      Optional[float]
    total_return_pct:   Optional[float]
    cagr:               Optional[float]
    max_drawdown_pct:   Optional[float]
    sharpe_ratio:       Optional[float]
    win_rate:           Optional[float]
    total_trades:       Optional[int]
    data_quality:       DataQuality
    ran_at:             datetime

    class Config:
        from_attributes = True


# ─── PaperTrades ──────────────────────────────────────────────────────────────

class PaperTradeCreate(BaseModel):
    symbol:         str = Field(..., min_length=1, max_length=50)
    direction:      TradeDirection
    quantity:       float = Field(..., gt=0)
    entry_price:    float = Field(..., gt=0)
    stop_loss:      Optional[float] = None
    target:         Optional[float] = None
    strategy_name:  Optional[str]   = None
    notes:          Optional[str]   = None

    @validator("symbol")
    def upper_symbol(cls, v):
        return v.strip().upper()


class PaperTradeClose(BaseModel):
    exit_price: float = Field(..., gt=0)
    notes:      Optional[str] = None


class PaperTradeOut(BaseModel):
    id:             int
    symbol:         str
    direction:      TradeDirection
    quantity:       float
    entry_price:    float
    exit_price:     Optional[float]
    stop_loss:      Optional[float]
    target:         Optional[float]
    status:         TradeStatus
    strategy_name:  Optional[str]
    pnl:            Optional[float]
    pnl_pct:        Optional[float]
    entry_time:     datetime
    exit_time:      Optional[datetime]
    notes:          Optional[str]

    class Config:
        from_attributes = True


# ─── Generic ──────────────────────────────────────────────────────────────────

class ErrorResponse(BaseModel):
    error:   str
    detail:  Optional[str] = None


class HealthResponse(BaseModel):
    status:   str
    database: str
    version:  str
