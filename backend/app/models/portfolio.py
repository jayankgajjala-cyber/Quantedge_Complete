"""backend/models/portfolio.py — Portfolio holdings and OHLCV history.

Transactional CSV upload pattern
---------------------------------
Any route that replaces holdings from a CSV upload MUST wrap its
delete-then-insert sequence inside a single transaction.  If the insert
fails half-way (bad row, constraint violation, network drop) the delete
must be rolled back so the table never ends up empty.

Correct pattern — context-manager form (preferred):

    with db.begin_nested():        # savepoint; commits or rolls back atomically
        db.query(Holding).delete(synchronize_session=False)
        db.bulk_save_objects(new_holding_objects)
    db.commit()
    # If bulk_save_objects raises, the savepoint is rolled back automatically
    # and the outer session is left intact for error handling.

Alternatively with explicit commit/rollback:

    try:
        db.query(Holding).delete(synchronize_session=False)
        for row in parsed_csv_rows:
            db.add(Holding(**row))
        db.commit()
    except Exception:
        db.rollback()
        raise

NEVER call db.commit() between the delete and the inserts — that makes
the delete permanent before the inserts succeed, leaving an empty
holdings table on any subsequent error.
"""

import enum
from datetime import datetime
from sqlalchemy import Column, Integer, Float, String, DateTime, Enum, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import relationship
from backend.core.database import Base


class DataQuality(str, enum.Enum):
    SUFFICIENT   = "SUFFICIENT"
    INSUFFICIENT = "INSUFFICIENT DATA"   # 5-9 years
    LOW_CONFIDENCE = "LOW CONFIDENCE"    # < 5 years


class IntervalType(str, enum.Enum):
    ONE_MIN = "1min"
    DAILY   = "daily"


class Holding(Base):
    __tablename__ = "holdings"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    symbol        = Column(String(50), nullable=False, unique=True, index=True)
    isin          = Column(String(20), nullable=True)
    exchange      = Column(String(20), nullable=False, default="NSE")
    quantity      = Column(Float, nullable=False)
    average_price = Column(Float, nullable=False)
    current_price = Column(Float, nullable=True)
    pnl           = Column(Float, nullable=True)
    pnl_pct       = Column(Float, nullable=True)
    sector        = Column(String(100), nullable=True)
    data_quality  = Column(Enum(DataQuality), default=DataQuality.SUFFICIENT)
    uploaded_at   = Column(DateTime, default=datetime.utcnow)
    updated_at    = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    historical_data = relationship("HistoricalData", back_populates="holding",
                                    cascade="all, delete-orphan")
    paper_trades    = relationship("PaperTrade", back_populates="holding",
                                    cascade="all, delete-orphan")


class HistoricalData(Base):
    __tablename__ = "historical_data"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    holding_id   = Column(Integer, ForeignKey("holdings.id", ondelete="CASCADE"), nullable=False)
    symbol       = Column(String(50), nullable=False, index=True)
    interval = Column(Enum(IntervalType, name="intervaltype"), nullable=False)
    timestamp    = Column(DateTime, nullable=False, index=True)
    open         = Column(Float, nullable=False)
    high         = Column(Float, nullable=False)
    low          = Column(Float, nullable=False)
    close        = Column(Float, nullable=False)
    volume       = Column(Float, nullable=False)
    data_quality = Column(Enum(DataQuality), default=DataQuality.SUFFICIENT)
    fetched_at   = Column(DateTime, default=datetime.utcnow)

    holding = relationship("Holding", back_populates="historical_data")

    __table_args__ = (
        UniqueConstraint("symbol", "interval", "timestamp", name="uq_hist_sym_int_ts"),
        Index("ix_hist_sym_int", "symbol", "interval"),
    )


# ─── Transactional CSV replace helper ────────────────────────────────────────

def replace_holdings_from_csv(
    db,
    new_holdings: list[dict],
) -> int:
    """
    Atomically replace every row in the `holdings` table with the rows
    parsed from a CSV upload.

    The delete and all inserts execute inside a single nested transaction
    (savepoint).  If any insert fails the delete is rolled back and the
    original data is preserved — the table is never left empty.

    Parameters
    ----------
    db           : SQLAlchemy Session (caller owns the outer session)
    new_holdings : list of dicts matching Holding column names

    Returns
    -------
    Number of rows inserted.

    Usage
    -----
        from backend.models.portfolio import replace_holdings_from_csv
        from backend.core.database import get_db_context

        with get_db_context() as db:
            n = replace_holdings_from_csv(db, parsed_rows)
            # Session is committed by get_db_context on clean exit
    """
    if not new_holdings:
        raise ValueError("new_holdings is empty — refusing to wipe the table with no replacement data")

    with db.begin_nested():   # savepoint — rolls back on exception, leaves outer tx open
        db.query(Holding).delete(synchronize_session=False)
        objects = [Holding(**row) for row in new_holdings]
        db.bulk_save_objects(objects)

    return len(objects)