"""
app/services/news_service.py — Async News Ingestion Engine
===========================================================

DATABASE ROUTING
  MongoDB  → raw news storage (news_feed collection, 7-day TTL archive)
  Neon     → sentiment scores (via sentiment_engine._persist_to_neon,
              called inside enrich_batch — this file never touches Neon directly)

PIPELINE PER CYCLE
  1. Fetch RSS + GNews  → List[raw NewsItem]
  2. enrich_batch()     → List[enriched dict]  (scores computed + saved to Neon)
  3. _upsert_items()    → MongoDB news_feed     (raw text + sentiment fields merged)
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import List, Optional

from typing_extensions import TypedDict

logger = logging.getLogger(__name__)


class NewsItem(TypedDict):
    url:          str
    title:        str
    summary:      str
    source:       str
    section:      str
    published_at: datetime
    created_at:   datetime
    image_url:    Optional[str]


# ── RSS / GNews feed registry ─────────────────────────────────────────────────

_RSS_FEEDS: dict[str, list[tuple[str, str]]] = {
    "indian_market": [
        ("LiveMint Markets",    "https://www.livemint.com/rss/markets"),
        ("LiveMint Companies",  "https://www.livemint.com/rss/companies"),
        ("MoneyControl News",   "https://www.moneycontrol.com/rss/latestnews.xml"),
        ("ET Markets",          "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ],
    "global_market": [
        ("Reuters Business",    "https://feeds.reuters.com/reuters/businessNews"),
        ("CNBC World",          "https://www.cnbc.com/id/100727362/device/rss/rss.html"),
        ("FT Markets",          "https://www.ft.com/markets?format=rss"),
    ],
    "macro_impact": [
        ("RBI Press Releases",  "https://www.rbi.org.in/Scripts/RSSFeedsPublicDomain.aspx"),
        ("ET Economy",          "https://economictimes.indiatimes.com/news/economy/rssfeeds/1373380680.cms"),
        ("MoneyControl Economy","https://www.moneycontrol.com/rss/economy.xml"),
        ("Reuters Fed",         "https://feeds.reuters.com/reuters/USFocusNews"),
    ],
    "swing_signals": [
        ("ET Stocks",           "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms"),
        ("MC Stock Reports",    "https://www.moneycontrol.com/rss/stockreports.xml"),
        ("LiveMint IPO",        "https://www.livemint.com/rss/IPO"),
    ],
}

_GNEWS_QUERIES: dict[str, str] = {
    "global_market":  "stock market OR Fed Reserve OR S&P 500",
    "macro_impact":   "RBI OR Federal Reserve OR inflation OR FII DII India",
    "swing_signals":  "stock upgrade downgrade analyst India NSE",
}


# ── RSS parser ────────────────────────────────────────────────────────────────

def _parse_feed_sync(xml_text: str) -> list[dict]:
    try:
        import feedparser  # type: ignore
        return feedparser.parse(xml_text).entries
    except ImportError:
        logger.warning("[news] feedparser not installed — RSS disabled")
        return []


async def _fetch_rss(session, url: str, source: str, section: str) -> List[NewsItem]:
    items: List[NewsItem] = []
    try:
        async with session.get(url, timeout=8) as resp:
            if resp.status not in (200, 301, 302):
                return items
            xml = await resp.text(errors="replace")
    except Exception as exc:
        logger.debug("[news] RSS %s error: %r", source, exc)
        return items

    entries = await asyncio.get_event_loop().run_in_executor(None, _parse_feed_sync, xml)
    now     = datetime.now(timezone.utc)

    for e in entries[:10]:
        title   = (e.get("title") or "").strip()
        link    = (e.get("link") or "").strip()
        summary = re.sub(r"<[^>]+>", "", e.get("summary") or e.get("description") or "").strip()[:400]
        if not title or not link:
            continue

        pub = now
        if e.get("published_parsed"):
            try:
                pub = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
            except Exception:
                pass

        items.append({
            "url":          link,
            "title":        title,
            "summary":      summary,
            "source":       source,
            "section":      section,
            "published_at": pub,
            "created_at":   now,
            "image_url":    None,
        })
    return items


# ── GNews fetcher ─────────────────────────────────────────────────────────────

async def _fetch_gnews(session, query: str, section: str) -> List[NewsItem]:
    api_key = os.getenv("GNEWS_API_KEY", "")
    if not api_key:
        return []

    url  = (
        f"https://gnews.io/api/v4/search"
        f"?q={query.replace(' ', '+')}&lang=en&max=10&apikey={api_key}"
    )
    now   = datetime.now(timezone.utc)
    items: List[NewsItem] = []

    try:
        async with session.get(url, timeout=8) as resp:
            if resp.status in (403, 429):
                return items
            if resp.status != 200:
                return items
            data = await resp.json()
    except Exception as exc:
        logger.debug("[news] GNews error: %r", exc)
        return items

    for art in data.get("articles", []):
        title   = (art.get("title") or "").strip()
        link    = (art.get("url") or "").strip()
        summary = (art.get("description") or "").strip()[:400]
        if not title or not link:
            continue

        pub = now
        try:
            pub = datetime.fromisoformat(art["publishedAt"].replace("Z", "+00:00"))
        except Exception:
            pass

        items.append({
            "url":          link,
            "title":        title,
            "summary":      summary,
            "source":       art.get("source", {}).get("name", "GNews"),
            "section":      section,
            "published_at": pub,
            "created_at":   now,
            "image_url":    art.get("image"),
        })
    return items


# ── MongoDB — lazy singleton ──────────────────────────────────────────────────

_mongo_col = None


def _get_collection():
    global _mongo_col
    if _mongo_col is not None:
        return _mongo_col
    try:
        from motor.motor_asyncio import AsyncIOMotorClient  # type: ignore
        from app.core.config import settings
        client     = AsyncIOMotorClient(settings.MONGODB_URI, serverSelectionTimeoutMS=5000)
        _mongo_col = client["quantedge"]["news_feed"]
        logger.info("[news] MongoDB connected (raw news archive)")
    except Exception as exc:
        logger.error("[news] MongoDB init failed: %s", exc)
        _mongo_col = None
    return _mongo_col


# ── MongoDB upsert (raw news + merged sentiment fields) ───────────────────────

_BASE_FIELDS = {
    "url", "title", "summary", "source", "section",
    "published_at", "created_at", "image_url",
}
_SENTIMENT_FIELDS = {
    "sentiment_score", "sentiment_label", "confidence", "confidence_pct",
    "action", "reasoning", "event_type", "weight_profile",
    "primary_stocks", "secondary_stocks", "sectors",
    "source_reliability", "time_decay",
    "finbert_score", "finbert_prob", "vader_score",
    "macro_score", "macro_confidence",
}


async def _upsert_items(items: List[dict]) -> tuple[int, int]:
    """
    Upserts enriched items into MongoDB news_feed collection.
    Base fields are $setOnInsert (immutable once written).
    Sentiment fields are $set (refreshed on re-score).
    """
    col = _get_collection()
    if col is None or not items:
        return 0, 0

    from pymongo import UpdateOne  # type: ignore
    from pymongo.errors import BulkWriteError  # type: ignore

    ops = [
        UpdateOne(
            {"url": item["url"]},
            {
                "$setOnInsert": {k: v for k, v in item.items() if k in _BASE_FIELDS},
                "$set":         {k: v for k, v in item.items() if k in _SENTIMENT_FIELDS},
            },
            upsert=True,
        )
        for item in items
    ]

    try:
        result   = await col.bulk_write(ops, ordered=False)
        inserted = result.upserted_count
        return inserted, len(items) - inserted
    except BulkWriteError as bwe:
        inserted = bwe.details.get("nUpserted", 0)
        return inserted, len(items) - inserted
    except Exception as exc:
        logger.warning("[news] MongoDB upsert error: %s", exc)
        return 0, len(items)


# ── Fetch + enrich cycle ──────────────────────────────────────────────────────

async def run_fetch_cycle() -> dict[str, int]:
    """
    Full pipeline per cycle:
      RSS/GNews → enrich_batch (FinBERT+VADER+Macro → Neon) → MongoDB upsert
    """
    try:
        import aiohttp  # type: ignore
    except ImportError:
        logger.error("[news] aiohttp not installed")
        return {}

    from app.services.sentiment_engine import enrich_batch  # type: ignore

    totals:  dict[str, int] = {}
    timeout = aiohttp.ClientTimeout(total=15)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for section, feeds in _RSS_FEEDS.items():
            all_items: List[dict] = []

            rss_results = await asyncio.gather(
                *[_fetch_rss(session, url, src, section) for src, url in feeds],
                return_exceptions=True,
            )
            for r in rss_results:
                if isinstance(r, list):
                    all_items.extend(r)

            if section in _GNEWS_QUERIES:
                gnews = await _fetch_gnews(session, _GNEWS_QUERIES[section], section)
                all_items.extend(gnews)

            # ── Sentiment enrichment (also fires Neon persist task) ──────────
            enriched = await enrich_batch(all_items)

            # ── Raw news + scores → MongoDB 7-day archive ────────────────────
            inserted, skipped = await _upsert_items(enriched)
            totals[section]   = inserted
            logger.info(
                "[news] %-18s  fetched=%d  inserted=%d  dup=%d",
                section, len(all_items), inserted, skipped,
            )

    return totals


# ── Background loop ───────────────────────────────────────────────────────────

_loop_task:      Optional[asyncio.Task] = None  # type: ignore[type-arg]
POLL_INTERVAL_S = 300   # 5 minutes


async def _loop() -> None:
    logger.info("[news] Background loop started (interval=%ds)", POLL_INTERVAL_S)
    while True:
        try:
            totals = await run_fetch_cycle()
            logger.info("[news] Cycle complete: %s", totals)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("[news] Cycle error: %s", exc)
        await asyncio.sleep(POLL_INTERVAL_S)


def start_news_loop() -> None:
    global _loop_task
    if _loop_task is None or _loop_task.done():
        _loop_task = asyncio.create_task(_loop())
        logger.info("[news] Background task created")


def stop_news_loop() -> None:
    global _loop_task
    if _loop_task and not _loop_task.done():
        _loop_task.cancel()
        logger.info("[news] Background task cancelled")


# ── FastAPI route helpers ─────────────────────────────────────────────────────

async def get_news_feed(
    section:    Optional[str] = None,
    limit:      int           = 20,
    event_type: Optional[str] = None,
    action:     Optional[str] = None,
    stock:      Optional[str] = None,
) -> List[dict]:
    """Fetch latest enriched articles from MongoDB news_feed collection."""
    col = _get_collection()
    if col is None:
        return []

    query: dict = {}
    if section:
        query["section"] = section
    if event_type:
        query["event_type"] = event_type
    if action:
        query["action"] = action
    if stock:
        query["$or"] = [
            {"primary_stocks":   stock.upper()},
            {"secondary_stocks": stock.upper()},
        ]

    cursor  = col.find(query, {"_id": 0}).sort("published_at", -1).limit(limit)
    results = []
    async for doc in cursor:
        for field in ("published_at", "created_at"):
            if isinstance(doc.get(field), datetime):
                doc[field] = doc[field].isoformat()
        results.append(doc)
    return results


async def get_clustered_feed(section: Optional[str] = None, limit: int = 50) -> dict:
    from app.services.sentiment_engine import cluster_news  # type: ignore
    items = await get_news_feed(section=section, limit=limit)
    return cluster_news(items)
