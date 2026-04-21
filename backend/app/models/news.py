"""backend/models/news.py — News articles, analysis, and sentiment overrides."""

import enum
import hashlib
from datetime import datetime
from sqlalchemy import Column, Integer, Float, String, DateTime, Boolean, Enum, Text, Index
from backend.core.database import Base


class SentimentLabel(str, enum.Enum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    NEUTRAL  = "NEUTRAL"


class NewsSource(str, enum.Enum):
    NEWSAPI       = "NEWSAPI"
    GOOGLE_NEWS   = "GOOGLE_NEWS"
    YAHOO_FINANCE = "YAHOO_FINANCE"


class OverrideAction(str, enum.Enum):
    DEMOTED   = "DEMOTED"
    UPGRADED  = "UPGRADED"
    NO_CHANGE = "NO_CHANGE"


class NewsArticle(Base):
    __tablename__ = "news_articles"
    id              = Column(Integer, primary_key=True, autoincrement=True)
    url_hash        = Column(String(64), unique=True, index=True)
    ticker          = Column(String(50), index=True)
    source_name     = Column(Enum(NewsSource))
    title           = Column(Text)
    description     = Column(Text, nullable=True)
    url             = Column(Text, nullable=True)
    published_at    = Column(DateTime, index=True)
    fetched_at      = Column(DateTime, default=datetime.utcnow)
    sentiment_label = Column(Enum(SentimentLabel), nullable=True)
    sentiment_score = Column(Float, nullable=True)
    analysed_at     = Column(DateTime, nullable=True)
    __table_args__  = (Index("ix_article_ticker_pub", "ticker", "published_at"),)

    @staticmethod
    def make_url_hash(url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()[:64]


class NewsAnalysis(Base):
    __tablename__ = "news_analysis"
    id                    = Column(Integer, primary_key=True, autoincrement=True)
    ticker                = Column(String(50), index=True)
    avg_sentiment_score   = Column(Float, nullable=True)
    sentiment_label       = Column(Enum(SentimentLabel), nullable=True)
    sentiment_std_dev     = Column(Float, nullable=True)
    articles_analysed     = Column(Integer, default=0)
    positive_count        = Column(Integer, default=0)
    negative_count        = Column(Integer, default=0)
    neutral_count         = Column(Integer, default=0)
    conflict_detected     = Column(Boolean, default=False)
    conflict_detail       = Column(Text, nullable=True)
    executive_summary     = Column(Text, nullable=True)   # JSON list of 3 bullets
    forecast_outlook      = Column(String(500), nullable=True)
    forecast_direction    = Column(String(20), nullable=True)
    forecast_confidence   = Column(Float, nullable=True)
    price_slope_annual    = Column(Float, nullable=True)
    revenue_cagr          = Column(Float, nullable=True)
    insufficient_coverage = Column(Boolean, default=False)
    coverage_message      = Column(Text, nullable=True)
    analysed_at           = Column(DateTime, default=datetime.utcnow, index=True)
    cache_expires_at      = Column(DateTime, nullable=True)
    is_cache_valid        = Column(Boolean, default=True)
    __table_args__        = (Index("ix_analysis_ticker_time", "ticker", "analysed_at"),)


class SentimentOverride(Base):
    __tablename__ = "sentiment_overrides"
    id                   = Column(Integer, primary_key=True, autoincrement=True)
    ticker               = Column(String(50), index=True)
    original_signal      = Column(String(20))
    override_action      = Column(Enum(OverrideAction))
    final_signal         = Column(String(50))
    sentiment_score      = Column(Float)
    technical_confidence = Column(Float, nullable=True)
    reason               = Column(Text)
    alert_sent           = Column(Boolean, default=False)
    created_at           = Column(DateTime, default=datetime.utcnow, index=True)
