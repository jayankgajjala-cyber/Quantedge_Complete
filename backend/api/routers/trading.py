"""
backend/api/routers/trading.py
================================
All trading data endpoints — all JWT-protected.

POST /api/trading/portfolio/upload          → Zerodha CSV ingest
GET  /api/trading/portfolio/holdings        → list all holdings
POST /api/trading/paper/open                → open paper trade with ledger
POST /api/trading/paper/{id}/close          → close with P&L
GET  /api/trading/paper/trades              → list trades (with live MTM)
GET  /api/trading/paper/summary             → portfolio summary + live P&L
GET  /api/trading/paper/budget              → current month budget status
POST /api/trading/paper/allocate            → calculate suggested allocation
GET  /api/trading/backtest/run/{ticker}     → run backtest for one ticker
"""

import io
import logging
import math
from datetime import datetime
from typing import Optional

import pandas as pd
import yfinance as yf
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import desc
from sqlalchemy.orm import Session

from backend.core.auth     import get_current_user
from backend.core.config   import get_settings
from backend.core.database import get_db
from backend.models.paper     import (
    AllocationEvent, AllocationStatus, BudgetCycle, LedgerEntryType,
    PaperTrade, TradeDirection, TradeStatus, VirtualLedger,
)
from backend.models.portfolio import DataQuality, Holding
from backend.services.quant_service import get_quant_service, fetch_with_cache

logger = logging.getLogger(__name__)
cfg    = get_settings()
router = APIRouter(
    prefix="/trading",
    tags=["Trading"],
    dependencies=[Depends(get_current_user)],
)

_REQUIRED_COLS = {"instrument", "qty.", "avg. cost"}


# ─── Portfolio upload ─────────────────────────────────────────────────────────

@router.post("/portfolio/upload",
             summary="Upload Zerodha holdings CSV")
async def upload_portfolio(
    file: UploadFile = File(..., description="Zerodha holdings CSV"),
    db:   Session    = Depends(get_db),
):
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Portfolio file not found or invalid format")
    try:
        df = pd.read_csv(io.BytesIO(raw))
    except Exception:
        raise HTTPException(400, "Portfolio file not found or invalid format")

    df.columns = [str(c).strip().lower() for c in df.columns]
    missing    = _REQUIRED_COLS - set(df.columns)
    if missing:
        raise HTTPException(400,
            f"Portfolio file not found or invalid format — "
            f"missing required column(s): {', '.join(repr(m) for m in missing)}")

    df = df.dropna(subset=["instrument"])
    imported = skipped = 0
    for _, row in df.iterrows():
        try:
            symbol  = str(row["instrument"]).strip().upper()
            qty     = float(str(row["qty."]).replace(",",""))
            avg_p   = float(str(row["avg. cost"]).replace(",","").replace("₹",""))
            if qty <= 0 or avg_p <= 0:
                skipped += 1; continue

            h = db.query(Holding).filter(Holding.symbol == symbol).first()
            if h:
                h.quantity      = qty
                h.average_price = avg_p
                h.updated_at    = datetime.utcnow()
            else:
                h = Holding(symbol=symbol, quantity=qty, average_price=avg_p)
                db.add(h)
            imported += 1
        except Exception:
            skipped += 1
    db.commit()
    return {"message": f"Imported {imported} holdings, {skipped} skipped.",
            "imported": imported, "skipped": skipped}


@router.get("/portfolio/holdings", summary="List all holdings with live P&L")
def list_holdings(db: Session = Depends(get_db)):
    """
    Returns all holdings enriched with live LTP from yfinance.
    current_price, pnl, and pnl_pct are computed here — they are NOT stored
    in the DB (they change every tick). Falls back to average_price if the
    yfinance call fails so the row is always valid.
    """
    holdings = db.query(Holding).order_by(Holding.symbol).all()
    if not holdings:
        return []

    # Batch-fetch live prices for all symbols
    live_prices: dict = {}
    for h in holdings:
        try:
            info  = yf.Ticker(f"{h.symbol}.NS").fast_info
            price = float(info.get("last_price") or info.get("previous_close") or 0)
            if price > 0:
                live_prices[h.symbol] = price
        except Exception:
            pass

    result = []
    for h in holdings:
        ltp     = live_prices.get(h.symbol)
        cost    = h.average_price * h.quantity
        cur_val = (ltp * h.quantity) if ltp else cost
        pnl     = cur_val - cost
        pnl_pct = (pnl / cost * 100) if cost > 0 else 0.0
        result.append({
            "id":            h.id,
            "symbol":        h.symbol,
            "exchange":      getattr(h, "exchange", "NSE"),
            "quantity":      h.quantity,
            "average_price": h.average_price,
            "current_price": round(ltp, 2) if ltp else None,
            "pnl":           round(pnl, 2),
            "pnl_pct":       round(pnl_pct, 2),
            "sector":        getattr(h, "sector", None),
            "data_quality":  h.data_quality.value if hasattr(h.data_quality, "value") else str(h.data_quality),
            "uploaded_at":   h.updated_at.isoformat() if getattr(h, "updated_at", None) else None,
        })
    return result


# ─── Budget helpers ───────────────────────────────────────────────────────────

def _get_or_create_budget(db: Session) -> BudgetCycle:
    now   = datetime.utcnow()
    cycle = db.query(BudgetCycle).filter(
        BudgetCycle.year == now.year, BudgetCycle.month == now.month
    ).first()
    if not cycle:
        cycle = BudgetCycle(year=now.year, month=now.month,
                            total_budget=cfg.MONTHLY_BUDGET_INR)
        db.add(cycle); db.commit(); db.refresh(cycle)
    return cycle


# ─── Paper trading ────────────────────────────────────────────────────────────

class OpenTradeIn(BaseModel):
    symbol:        str   = Field(..., min_length=1)
    direction:     str   = Field(..., pattern="^(BUY|SELL)$")
    quantity:      float = Field(..., gt=0)
    entry_price:   float = Field(..., gt=0)
    stop_loss:     Optional[float] = None
    target:        Optional[float] = None
    strategy_name: Optional[str]   = None


class CloseTradeIn(BaseModel):
    exit_price: float = Field(..., gt=0)
    reason:     str   = "MANUAL_CLOSE"


@router.post("/paper/open", status_code=status.HTTP_201_CREATED,
             summary="Open a paper trade with virtual ledger entry")
def open_paper_trade(payload: OpenTradeIn, db: Session = Depends(get_db)):
    trade = PaperTrade(
        symbol        = payload.symbol.upper(),
        direction     = TradeDirection(payload.direction),
        quantity      = payload.quantity,
        entry_price   = payload.entry_price,
        stop_loss     = payload.stop_loss,
        target        = payload.target,
        strategy_name = payload.strategy_name,
        status        = TradeStatus.OPEN,
    )
    db.add(trade); db.flush()

    gross      = payload.entry_price * payload.quantity
    commission = gross * cfg.COMMISSION_PCT
    db.add(VirtualLedger(
        trade_id    = trade.id, symbol = trade.symbol,
        entry_type  = LedgerEntryType.TRADE_OPEN,
        price       = payload.entry_price, quantity = payload.quantity,
        gross_value = gross, commission = commission,
        net_value   = gross + commission,
    ))

    cycle = _get_or_create_budget(db)
    cycle.allocated  += gross
    cycle.open_trades = max(0, cycle.open_trades) + 1
    db.commit(); db.refresh(trade)
    return {"id": trade.id, "symbol": trade.symbol, "status": trade.status.value,
            "entry_price": trade.entry_price, "quantity": trade.quantity}


@router.post("/paper/{trade_id}/close", summary="Close a paper trade + P&L + ledger")
def close_paper_trade(trade_id: int, payload: CloseTradeIn, db: Session = Depends(get_db)):
    trade = db.query(PaperTrade).filter(
        PaperTrade.id == trade_id, PaperTrade.status == TradeStatus.OPEN
    ).first()
    if not trade:
        raise HTTPException(404, f"Open trade id={trade_id} not found")

    trade.exit_price = payload.exit_price
    trade.exit_time  = datetime.utcnow()
    trade.status     = TradeStatus.CLOSED
    trade.pnl        = ((payload.exit_price - trade.entry_price) * trade.quantity
                        if trade.direction == TradeDirection.BUY
                        else (trade.entry_price - payload.exit_price) * trade.quantity)
    trade.pnl_pct    = (trade.pnl / (trade.entry_price * trade.quantity)) * 100

    gross      = payload.exit_price * trade.quantity
    commission = gross * cfg.COMMISSION_PCT
    db.add(VirtualLedger(
        trade_id     = trade.id, symbol=trade.symbol,
        entry_type   = LedgerEntryType(payload.reason) if payload.reason in [e.value for e in LedgerEntryType] else LedgerEntryType.MANUAL_CLOSE,
        price        = payload.exit_price, quantity=trade.quantity,
        gross_value  = gross, commission=commission, net_value=gross - commission,
        realised_pnl = trade.pnl, realised_pnl_pct=trade.pnl_pct,
        close_reason = payload.reason,
    ))

    cycle = _get_or_create_budget(db)
    cycle.realised_pnl  = (cycle.realised_pnl or 0.0) + trade.pnl
    cycle.open_trades   = max(0, (cycle.open_trades or 1) - 1)
    cycle.closed_trades = (cycle.closed_trades or 0) + 1
    if trade.pnl > 0:
        cycle.winning_trades = (cycle.winning_trades or 0) + 1

    db.commit(); db.refresh(trade)
    return {"id": trade.id, "symbol": trade.symbol,
            "pnl": trade.pnl, "pnl_pct": trade.pnl_pct,
            "exit_price": trade.exit_price}


@router.get("/paper/trades", summary="List paper trades with live MTM P&L")
def list_trades(
    trade_status: Optional[str] = Query(None, alias="status"),
    limit:        int           = Query(100, ge=1, le=500),
    db:           Session       = Depends(get_db),
):
    q = db.query(PaperTrade)
    if trade_status:
        try:
            q = q.filter(PaperTrade.status == TradeStatus(trade_status.upper()))
        except ValueError:
            raise HTTPException(400, f"Invalid status '{trade_status}'")
    trades = q.order_by(desc(PaperTrade.entry_time)).limit(limit).all()

    # Enrich open trades with live price
    result = []
    open_syms = list({t.symbol for t in trades if t.status == TradeStatus.OPEN})
    live_prices: dict = {}
    for sym in open_syms:
        try:
            info = yf.Ticker(f"{sym}.NS").fast_info
            price = float(info.get("last_price") or info.get("previous_close") or 0)
            if price > 0:
                live_prices[sym] = price
        except Exception:
            pass

    for t in trades:
        d = {
            "id": t.id, "symbol": t.symbol, "direction": t.direction.value,
            "quantity": t.quantity, "entry_price": t.entry_price,
            "exit_price": t.exit_price, "stop_loss": t.stop_loss, "target": t.target,
            "status": t.status.value, "strategy_name": t.strategy_name,
            "pnl": t.pnl, "pnl_pct": t.pnl_pct,
            "entry_time": t.entry_time, "exit_time": t.exit_time,
        }
        if t.status == TradeStatus.OPEN and t.symbol in live_prices:
            ltp = live_prices[t.symbol]
            mtm = ((ltp - t.entry_price) if t.direction == TradeDirection.BUY
                   else (t.entry_price - ltp)) * t.quantity
            d["ltp"]     = ltp
            d["mtm_pnl"] = round(mtm, 2)
            d["mtm_pct"] = round(mtm / (t.entry_price * t.quantity) * 100, 2)
        result.append(d)
    return result


@router.get("/paper/budget", summary="Current month ₹15,000 budget status")
def get_budget(db: Session = Depends(get_db)):
    cycle = _get_or_create_budget(db)
    return {
        "year":             cycle.year,
        "month":            cycle.month,
        "total_budget":     cycle.total_budget,
        "allocated":        cycle.allocated,
        "remaining":        cycle.remaining_budget,
        "utilisation_pct":  cycle.utilisation_pct,
        "open_trades":      cycle.open_trades,
        "closed_trades":    cycle.closed_trades,
        "realised_pnl":     cycle.realised_pnl,
    }


class AllocateIn(BaseModel):
    ticker:            str   = Field(..., min_length=1)
    signal_confidence: float = Field(..., ge=0, le=100)
    stop_loss:         Optional[float] = None
    target:            Optional[float] = None


@router.post("/paper/allocate", summary="Calculate budget allocation for a high-confidence signal")
def allocate(payload: AllocateIn, db: Session = Depends(get_db)):
    if payload.signal_confidence < cfg.HIGH_CONFIDENCE_THRESHOLD:
        return {
            "can_allocate": False,
            "reason": f"Confidence {payload.signal_confidence:.0f}% < threshold {cfg.HIGH_CONFIDENCE_THRESHOLD:.0f}%",
        }
    # Live price
    try:
        info  = yf.Ticker(f"{payload.ticker.upper()}.NS").fast_info
        price = float(info.get("last_price") or info.get("previous_close") or 0)
    except Exception:
        price = 0.0
    if price <= 0:
        return {"can_allocate": False, "reason": f"Cannot fetch live price for {payload.ticker}"}

    cycle     = _get_or_create_budget(db)
    remaining = cycle.remaining_budget
    if remaining < price:
        return {"can_allocate": False, "reason": f"Budget exhausted: ₹{remaining:.0f} < ₹{price:.0f} (1 share)"}

    max_alloc = min(remaining, cfg.MONTHLY_BUDGET_INR * cfg.MAX_SINGLE_TRADE_PCT)
    quantity  = math.floor(max_alloc / price)
    if quantity < 1:
        return {"can_allocate": False, "reason": "Quantity rounds to 0 shares"}

    actual_cost = quantity * price
    commission  = actual_cost * cfg.COMMISSION_PCT * 2
    risk_per_trade = (price - payload.stop_loss) * quantity if payload.stop_loss else None

    db.add(AllocationEvent(
        ticker=payload.ticker.upper(), signal_confidence=payload.signal_confidence,
        current_price=price, budget_remaining=remaining,
        allocation_amount=max_alloc, allocation_pct=max_alloc / cfg.MONTHLY_BUDGET_INR * 100,
        suggested_quantity=quantity, actual_cost=actual_cost,
        stop_loss=payload.stop_loss, target=payload.target,
        risk_per_trade_inr=risk_per_trade,
        status=AllocationStatus.SUGGESTED,
    ))
    db.commit()

    return {
        "can_allocate":      True,
        "ticker":            payload.ticker.upper(),
        "current_price":     price,
        "suggested_quantity": quantity,
        "actual_cost":       round(actual_cost, 2),
        "allocation_pct":    round(max_alloc / cfg.MONTHLY_BUDGET_INR * 100, 2),
        "commission":        round(commission, 2),
        "risk_per_trade":    round(risk_per_trade, 2) if risk_per_trade else None,
        "budget_remaining_after": round(remaining - actual_cost, 2),
    }


@router.get("/backtest/run/{ticker}",
            summary="Run 10-year backtest for a single ticker (all 8 strategies)")
def run_backtest(ticker: str):
    """
    Fetches data with parquet cache, runs all 8 strategies, persists results.
    Returns DataQuality flag: SUFFICIENT / INSUFFICIENT DATA / LOW CONFIDENCE.
    """
    try:
        svc    = get_quant_service()
        result = svc.run_backtest_for_ticker(ticker.upper())
        return result
    except Exception as exc:
        logger.error("Backtest failed for %s: %s", ticker, exc, exc_info=True)
        raise HTTPException(500, f"Backtest error: {exc}")
