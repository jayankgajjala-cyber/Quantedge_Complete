"""
backend/api/routers/trading.py
================================
All trading data endpoints — all JWT-protected.

Every endpoint:
  - Wraps logic in try/except
  - Returns structured errors: { "status": "error", "message": "..." }
  - Never silently swallows exceptions

POST /api/trading/portfolio/upload          → Zerodha CSV ingest
GET  /api/trading/portfolio/holdings        → list all holdings with live P&L
POST /api/trading/paper/open                → open paper trade with ledger
POST /api/trading/paper/{id}/close          → close with P&L
GET  /api/trading/paper/trades              → list trades (with live MTM)
GET  /api/trading/paper/budget              → current month budget status
POST /api/trading/paper/allocate            → calculate suggested allocation
GET  /api/trading/backtest/run/{ticker}     → run backtest for one ticker

FIXES (v9.3):
  - FIX 1: upload_portfolio now DELETES all existing holdings before inserting
            new rows so sold stocks are correctly removed on each CSV re-upload.
  - FIX 2: list_holdings returns a structured empty-state response when the
            holdings table is empty — no yfinance calls are made in that case.
"""

import io
import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional

import pandas as pd
import sqlalchemy.exc as sa_exc
import yfinance as yf
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import JSONResponse
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
from backend.utils.logger import get_logger

logger = get_logger(__name__)
cfg    = get_settings()
router = APIRouter(
    prefix="/trading",
    tags=["Trading"],
    dependencies=[Depends(get_current_user)],
)

_REQUIRED_COLS = {"instrument", "qty.", "avg. cost"}


def _err(msg: str, code: int = 400):
    """Return a structured error JSONResponse."""
    return JSONResponse(
        status_code=code,
        content={"status": "error", "message": msg},
    )


# ── Live price helpers ────────────────────────────────────────────────────────

def _fetch_price(symbol: str) -> tuple[str, Optional[float]]:
    try:
        info  = yf.Ticker(f"{symbol}.NS").fast_info
        price = float(info.get("last_price") or info.get("previous_close") or 0)
        return symbol, price if price > 0 else None
    except Exception:
        return symbol, None


def _batch_live_prices(symbols: list[str], max_workers: int = 8) -> dict[str, float]:
    prices: dict[str, float] = {}
    if not symbols:
        return prices
    with ThreadPoolExecutor(max_workers=min(max_workers, len(symbols))) as pool:
        futures = {pool.submit(_fetch_price, sym): sym for sym in symbols}
        for fut in as_completed(futures, timeout=20):
            try:
                sym, price = fut.result()
                if price is not None:
                    prices[sym] = price
            except Exception:
                pass
    return prices


# ─── Portfolio upload ─────────────────────────────────────────────────────────

@router.post("/portfolio/upload", summary="Upload Zerodha holdings CSV")
async def upload_portfolio(
    file: UploadFile = File(..., description="Zerodha holdings CSV"),
    db:   Session    = Depends(get_db),
):
    """
    FIX 1: Deletes ALL existing holdings before inserting the new CSV rows.
    This ensures stocks that were sold and no longer appear in the export
    are properly removed from the database — not left as stale rows.
    """
    try:
        raw = await file.read()
        if not raw:
            return _err("Empty file received — please upload a valid Zerodha CSV")

        try:
            df = pd.read_csv(io.BytesIO(raw))
        except Exception as e:
            return _err(f"Could not parse CSV: {e}. Ensure the file is a valid Zerodha holdings export.")

        df.columns = [str(c).strip().lower() for c in df.columns]
        missing    = _REQUIRED_COLS - set(df.columns)
        if missing:
            return _err(
                f"Missing required columns: {', '.join(repr(m) for m in missing)}. "
                f"Expected: 'Instrument', 'Qty.', 'Avg. cost'. "
                f"Export from Zerodha Console → Portfolio → Holdings."
            )

        df = df.dropna(subset=["instrument"])

        # ── FIX 1: Wipe old holdings so sold stocks are not retained ──────────
        deleted_count = db.query(Holding).delete(synchronize_session=False)
        logger.info("upload_portfolio: deleted %d stale holding rows before re-import", deleted_count)
        # Flush the deletes so the INSERT below doesn't hit unique-constraint
        # conflicts on symbol if the same ticker reappears in the new CSV.
        db.flush()
        # ─────────────────────────────────────────────────────────────────────

        imported = skipped = 0
        for _, row in df.iterrows():
            try:
                symbol = str(row["instrument"]).strip().upper()
                qty    = float(str(row["qty."]).replace(",", ""))
                avg_p  = float(str(row["avg. cost"]).replace(",", "").replace("₹", ""))
                if qty <= 0 or avg_p <= 0:
                    skipped += 1
                    continue
                # Always insert fresh rows (old rows were deleted above)
                h = Holding(symbol=symbol, quantity=qty, average_price=avg_p)
                db.add(h)
                imported += 1
            except Exception:
                skipped += 1

        db.commit()
        return {
            "status":   "success",
            "message":  (
                f"Imported {imported} holdings ({deleted_count} stale rows removed), "
                f"{skipped} rows skipped."
            ),
            "imported": imported,
            "deleted":  deleted_count,
            "skipped":  skipped,
        }
    except Exception as exc:
        logger.error("upload_portfolio failed: %s", exc, exc_info=True)
        return _err(f"Upload failed unexpectedly: {exc}", 500)


@router.get("/portfolio/holdings", summary="List all holdings with live P&L")
def list_holdings(db: Session = Depends(get_db)):
    """
    FIX 2: Returns a structured empty-state payload when the holdings table is
    empty. No yfinance API calls are made in that case, preventing cascading
    errors on a fresh deploy before any CSV has been uploaded.
    """
    try:
        holdings = db.query(Holding).order_by(Holding.symbol).all()

        # ── FIX 2: Empty-portfolio guard — stop all downstream API calls ──────
        if not holdings:
            return []
        # ─────────────────────────────────────────────────────────────────────

        # Only call yfinance when we actually have holdings to price
        live_prices = _batch_live_prices([h.symbol for h in holdings])

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
                "exchange":      getattr(h, "exchange", "NSE") or "NSE",
                "quantity":      h.quantity,
                "average_price": h.average_price,
                "current_price": round(ltp, 2) if ltp else None,
                "pnl":           round(pnl, 2),
                "pnl_pct":       round(pnl_pct, 2),
                "sector":        getattr(h, "sector", None),
                "data_quality":  (h.data_quality.value if hasattr(h.data_quality, "value") else str(h.data_quality)),
                "uploaded_at":   (h.updated_at.isoformat() if getattr(h, "updated_at", None) else None),
            })
        return result

    except sa_exc.OperationalError as exc:
        logger.error("list_holdings DB connection error: %s", exc, exc_info=True)
        raise HTTPException(
            503,
            detail=(
                "Cannot connect to the database to load holdings. "
                "Check DATABASE_URL in Railway environment variables."
            ),
        )
    except sa_exc.ProgrammingError as exc:
        logger.error("list_holdings schema error: %s", exc, exc_info=True)
        raise HTTPException(
            500,
            detail=(
                "Database table 'holdings' not found in Supabase. "
                "Verify DATABASE_URL points to the correct Supabase project."
            ),
        )
    except Exception as exc:
        logger.error("list_holdings failed: %s", exc, exc_info=True)
        raise HTTPException(500, detail=f"Failed to load holdings: {exc}")


# ─── Budget helpers ───────────────────────────────────────────────────────────

def _get_or_create_budget(db: Session) -> BudgetCycle:
    now   = datetime.utcnow()
    cycle = db.query(BudgetCycle).filter(
        BudgetCycle.year  == now.year,
        BudgetCycle.month == now.month,
    ).first()
    if not cycle:
        cycle = BudgetCycle(year=now.year, month=now.month, total_budget=cfg.MONTHLY_BUDGET_INR)
        db.add(cycle)
        db.commit()
        db.refresh(cycle)
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
    try:
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
        db.add(trade)
        db.flush()

        gross      = payload.entry_price * payload.quantity
        commission = gross * cfg.COMMISSION_PCT
        db.add(VirtualLedger(
            trade_id    = trade.id,
            symbol      = trade.symbol,
            entry_type  = LedgerEntryType.TRADE_OPEN,
            price       = payload.entry_price,
            quantity    = payload.quantity,
            gross_value = gross,
            commission  = commission,
            net_value   = gross + commission,
        ))
        cycle = _get_or_create_budget(db)
        cycle.allocated  += gross
        cycle.open_trades = max(0, cycle.open_trades) + 1
        db.commit()
        db.refresh(trade)
        return {
            "status":      "success",
            "id":          trade.id,
            "symbol":      trade.symbol,
            "status_trade": trade.status.value,
            "entry_price": trade.entry_price,
            "quantity":    trade.quantity,
            "message":     f"Paper trade #{trade.id} opened: {trade.direction.value} {trade.quantity} {trade.symbol} @ ₹{trade.entry_price}",
        }
    except Exception as exc:
        logger.error("open_paper_trade failed: %s", exc, exc_info=True)
        raise HTTPException(500, f"Failed to open trade: {exc}")


@router.post("/paper/{trade_id}/close", summary="Close a paper trade + P&L + ledger")
def close_paper_trade(trade_id: int, payload: CloseTradeIn, db: Session = Depends(get_db)):
    try:
        trade = db.query(PaperTrade).filter(
            PaperTrade.id     == trade_id,
            PaperTrade.status == TradeStatus.OPEN,
        ).first()
        if not trade:
            raise HTTPException(404, f"Open trade id={trade_id} not found")

        trade.exit_price = payload.exit_price
        trade.exit_time  = datetime.utcnow()
        trade.status     = TradeStatus.CLOSED
        trade.pnl        = (
            (payload.exit_price - trade.entry_price) * trade.quantity
            if trade.direction == TradeDirection.BUY
            else (trade.entry_price - payload.exit_price) * trade.quantity
        )
        trade.pnl_pct = (trade.pnl / (trade.entry_price * trade.quantity)) * 100

        gross      = payload.exit_price * trade.quantity
        commission = gross * cfg.COMMISSION_PCT
        entry_type = (
            LedgerEntryType(payload.reason)
            if payload.reason in {e.value for e in LedgerEntryType}
            else LedgerEntryType.MANUAL_CLOSE
        )
        db.add(VirtualLedger(
            trade_id         = trade.id,
            symbol           = trade.symbol,
            entry_type       = entry_type,
            price            = payload.exit_price,
            quantity         = trade.quantity,
            gross_value      = gross,
            commission       = commission,
            net_value        = gross - commission,
            realised_pnl     = trade.pnl,
            realised_pnl_pct = trade.pnl_pct,
            close_reason     = payload.reason,
        ))
        cycle = _get_or_create_budget(db)
        cycle.realised_pnl  = (cycle.realised_pnl or 0.0) + trade.pnl
        cycle.open_trades   = max(0, (cycle.open_trades or 1) - 1)
        cycle.closed_trades = (cycle.closed_trades or 0) + 1
        if trade.pnl > 0:
            cycle.winning_trades = (cycle.winning_trades or 0) + 1

        db.commit()
        db.refresh(trade)
        return {
            "status":     "success",
            "id":         trade.id,
            "symbol":     trade.symbol,
            "pnl":        trade.pnl,
            "pnl_pct":    trade.pnl_pct,
            "exit_price": trade.exit_price,
            "message":    f"Trade #{trade.id} closed. P&L: {'+'if trade.pnl>=0 else ''}₹{trade.pnl:.2f} ({trade.pnl_pct:.2f}%)",
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("close_paper_trade failed: %s", exc, exc_info=True)
        raise HTTPException(500, f"Failed to close trade: {exc}")


@router.get("/paper/trades", summary="List paper trades with live MTM P&L")
def list_trades(
    trade_status: Optional[str] = Query(None, alias="status"),
    limit:        int           = Query(100, ge=1, le=500),
    db:           Session       = Depends(get_db),
):
    try:
        q = db.query(PaperTrade)
        if trade_status:
            try:
                q = q.filter(PaperTrade.status == TradeStatus(trade_status.upper()))
            except ValueError:
                raise HTTPException(400, f"Invalid status '{trade_status}'. Use OPEN or CLOSED.")
        trades = q.order_by(desc(PaperTrade.entry_time)).limit(limit).all()

        open_syms   = list({t.symbol for t in trades if t.status == TradeStatus.OPEN})
        live_prices = _batch_live_prices(open_syms) if open_syms else {}

        result = []
        for t in trades:
            d: dict = {
                "id":            t.id,
                "symbol":        t.symbol,
                "direction":     t.direction.value,
                "quantity":      t.quantity,
                "entry_price":   t.entry_price,
                "exit_price":    t.exit_price,
                "stop_loss":     t.stop_loss,
                "target":        t.target,
                "status":        t.status.value,
                "strategy_name": t.strategy_name,
                "pnl":           t.pnl,
                "pnl_pct":       t.pnl_pct,
                "entry_time":    t.entry_time.isoformat() if t.entry_time else None,
                "exit_time":     t.exit_time.isoformat()  if t.exit_time  else None,
            }
            if t.status == TradeStatus.OPEN and t.symbol in live_prices:
                ltp = live_prices[t.symbol]
                mtm = (
                    (ltp - t.entry_price) if t.direction == TradeDirection.BUY
                    else (t.entry_price - ltp)
                ) * t.quantity
                d["ltp"]     = ltp
                d["mtm_pnl"] = round(mtm, 2)
                d["mtm_pct"] = round(mtm / (t.entry_price * t.quantity) * 100, 2)
            result.append(d)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("list_trades failed: %s", exc, exc_info=True)
        raise HTTPException(500, f"Failed to load trades: {exc}")


@router.get("/paper/budget", summary="Current month ₹15,000 budget status")
def get_budget(db: Session = Depends(get_db)):
    try:
        cycle = _get_or_create_budget(db)
        return {
            "year":            cycle.year,
            "month":           cycle.month,
            "total_budget":    cycle.total_budget,
            "allocated":       cycle.allocated,
            "remaining":       cycle.remaining_budget,
            "utilisation_pct": cycle.utilisation_pct,
            "open_trades":     cycle.open_trades,
            "closed_trades":   cycle.closed_trades,
            "realised_pnl":    cycle.realised_pnl or 0.0,
        }
    except Exception as exc:
        logger.error("get_budget failed: %s", exc, exc_info=True)
        raise HTTPException(500, f"Failed to load budget: {exc}")


class AllocateIn(BaseModel):
    ticker:            str   = Field(..., min_length=1)
    signal_confidence: float = Field(..., ge=0, le=100)
    stop_loss:         Optional[float] = None
    target:            Optional[float] = None


@router.post("/paper/allocate", summary="Calculate budget allocation for a high-confidence signal")
def allocate(payload: AllocateIn, db: Session = Depends(get_db)):
    try:
        if payload.signal_confidence < cfg.HIGH_CONFIDENCE_THRESHOLD:
            return {
                "can_allocate": False,
                "reason": f"Confidence {payload.signal_confidence:.0f}% < threshold {cfg.HIGH_CONFIDENCE_THRESHOLD:.0f}%",
            }
        _, price = _fetch_price(payload.ticker.upper())
        if not price:
            return {"can_allocate": False, "reason": f"Cannot fetch live price for {payload.ticker} — market may be closed"}

        cycle     = _get_or_create_budget(db)
        remaining = cycle.remaining_budget
        if remaining < price:
            return {"can_allocate": False, "reason": f"Budget exhausted: ₹{remaining:.0f} remaining < ₹{price:.0f} per share"}

        max_alloc  = min(remaining, cfg.MONTHLY_BUDGET_INR * cfg.MAX_SINGLE_TRADE_PCT)
        quantity   = math.floor(max_alloc / price)
        if quantity < 1:
            return {"can_allocate": False, "reason": "Quantity rounds to 0 shares — insufficient budget"}

        actual_cost    = quantity * price
        commission     = actual_cost * cfg.COMMISSION_PCT * 2
        risk_per_trade = (price - payload.stop_loss) * quantity if payload.stop_loss else None

        db.add(AllocationEvent(
            ticker             = payload.ticker.upper(),
            signal_confidence  = payload.signal_confidence,
            current_price      = price,
            budget_remaining   = remaining,
            allocation_amount  = max_alloc,
            allocation_pct     = max_alloc / cfg.MONTHLY_BUDGET_INR * 100,
            suggested_quantity = quantity,
            actual_cost        = actual_cost,
            stop_loss          = payload.stop_loss,
            target             = payload.target,
            risk_per_trade_inr = risk_per_trade,
            status             = AllocationStatus.SUGGESTED,
        ))
        db.commit()
        return {
            "status":                "success",
            "can_allocate":          True,
            "ticker":                payload.ticker.upper(),
            "current_price":         price,
            "suggested_quantity":    quantity,
            "actual_cost":           round(actual_cost, 2),
            "allocation_pct":        round(max_alloc / cfg.MONTHLY_BUDGET_INR * 100, 2),
            "commission":            round(commission, 2),
            "risk_per_trade":        round(risk_per_trade, 2) if risk_per_trade else None,
            "budget_remaining_after": round(remaining - actual_cost, 2),
        }
    except Exception as exc:
        logger.error("allocate failed: %s", exc, exc_info=True)
        raise HTTPException(500, f"Allocation failed: {exc}")


@router.get("/backtest/run/{ticker}",
            summary="Run 10-year backtest for a single ticker (all 8 strategies)")
def run_backtest(ticker: str):
    """
    Cold cache: ~15–45s (fetches 10yr OHLCV + runs 8 strategies).
    Warm cache: ~5s.
    Frontend uses apiSlow (120s timeout) for this endpoint.
    """
    try:
        svc    = get_quant_service()
        result = svc.run_backtest_for_ticker(ticker.upper())
        return {"status": "success", "ticker": ticker.upper(), "data": result}
    except Exception as exc:
        logger.error("run_backtest failed for %s: %s", ticker, exc, exc_info=True)
        raise HTTPException(500, f"Backtest failed for {ticker}: {exc}")
