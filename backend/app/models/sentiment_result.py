"""
app/models/sentiment_result.py — SQLAlchemy ORM model for Neon
==============================================================

Table: sentiment_results
Database: Neon (PostgreSQL)
Purpose: Persistent store for calculated FinBERT/VADER/Macro scores.
         Raw news text lives in MongoDB; this table holds only the derived
         analytical output, keeping Neon lean and query-friendly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Float, DateTime, ARRAY, Text, Index
)
from sqlalchemy.dialects.postgresql import JSONB
from app.db.neon_base import NeonBase


class SentimentResult(NeonBase):
    __tablename__ = "sentiment_results"

    # Primary key — same as news_feed.url in MongoDB (dedup anchor)
    url = Column(String(2048), primary_key=True)

    # Article metadata (denormalised for standalone queries)
    title        = Column(Text,   nullable=False)
    source       = Column(String(255), nullable=False)
    section      = Column(String(64),  nullable=False)
    published_at = Column(DateTime(timezone=True), nullable=False)
    scored_at    = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Composite score ───────────────────────────────────────────────────────
    sentiment_score = Column(Float, nullable=False)   # [-1, +1]
    sentiment_label = Column(String(16), nullable=False)  # Bullish/Bearish/Neutral
    confidence      = Column(Float, nullable=False)   # [0, 1]
    confidence_pct  = Column(Float, nullable=False)
    action          = Column(String(8), nullable=False)   # Buy/Sell/Hold
    reasoning       = Column(Text,  nullable=True)
    weight_profile  = Column(String(32), nullable=True)   # corporate/macro_heavy/...

    # ── Raw model outputs ─────────────────────────────────────────────────────
    finbert_score    = Column(Float, nullable=True)
    finbert_prob     = Column(Float, nullable=True)
    vader_score      = Column(Float, nullable=True)
    macro_score      = Column(Float, nullable=True)
    macro_confidence = Column(Float, nullable=True)

    # ── Derived metadata ──────────────────────────────────────────────────────
    event_type        = Column(String(32),  nullable=True)
    primary_stocks    = Column(ARRAY(String), nullable=True, default=list)
    secondary_stocks  = Column(ARRAY(String), nullable=True, default=list)
    sectors           = Column(ARRAY(String), nullable=True, default=list)
    source_reliability = Column(Float, nullable=True)
    time_decay        = Column(Float, nullable=True)

    __table_args__ = (
        # Fast lookups by section + published_at for the news feed UI
        Index("ix_sr_section_pub", "section", "published_at"),
        # Fast lookups by action for Buy/Sell/Hold filters
        Index("ix_sr_action", "action"),
        # Fast lookups by stock symbol (GIN on array)
        Index("ix_sr_primary_stocks", "primary_stocks", postgresql_using="gin"),
    )

    def __repr__(self) -> str:
        return (
            f"<SentimentResult url={self.url!r} "
            f"label={self.sentiment_label!r} action={self.action!r}>"
        )
