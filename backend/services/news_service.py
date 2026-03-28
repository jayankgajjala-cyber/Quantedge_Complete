"""
backend/services/news_service.py — v9.8 (Fixed)
- Added fetch_and_persist() for the 60s news scheduler job
- NewsAPI key validation before request
- Google News and Yahoo fetcher hardened
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import statistics
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional
from xml.etree import ElementTree

import httpx
import numpy as np
import pandas as pd
import requests
import yfinance as yf
from bs4 import BeautifulSoup
from sklearn.linear_model import LinearRegression
from sqlalchemy import desc
from sqlalchemy.orm import Session

from backend.core.config import get_settings
from backend.core.database import get_db_context
from backend.models.news import (
    NewsAnalysis, NewsArticle, OverrideAction, SentimentLabel,
    SentimentOverride, NewsSource,
)

logger = logging.getLogger(__name__)
cfg    = get_settings()

NEWSAPI_URL  = "https://newsapi.org/v2/everything"
GOOGLE_RSS   = "https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
YAHOO_URL    = "https://finance.yahoo.com/quote/{sym}/news/"
NSE_SUFFIX   = ".NS"
HEADERS      = {"User-Agent": "Mozilla/5.0 (compatible; Quantedge/9.0)"}

_POSITIVE_KW = {"record","profit","growth","surge","beat","strong","upgrade","rally",
                "gain","revenue","dividend","outperform","bullish","positive","high"}
_NEGATIVE_KW = {"loss","decline","fall","drop","miss","weak","downgrade","crash",
                "fraud","debt","lawsuit","warning","risk","cut","bearish","negative"}


# ─── Article dataclass ────────────────────────────────────────────────────────

class Article:
    __slots__ = ("ticker","source","title","description","url","published_at","url_hash")
    def __init__(self, ticker, source, title, description, url, published_at):
        self.ticker       = ticker
        self.source       = source
        self.title        = str(title).strip()
        self.description  = str(description or "").strip()
        self.url          = str(url).strip()
        self.published_at = published_at.astimezone(timezone.utc) if published_at.tzinfo else published_at.replace(tzinfo=timezone.utc)
        self.url_hash     = hashlib.sha256(self.url.encode()).hexdigest()[:64]


def _safe_dt(raw: str) -> datetime:
    from email.utils import parsedate_to_datetime
    for parser in (lambda s: datetime.fromisoformat(s.replace("Z","+00:00")),
                   parsedate_to_datetime):
        try:
            return parser(raw)
        except Exception:
            continue
    return datetime.now(timezone.utc)


# ─── Fetchers ─────────────────────────────────────────────────────────────────

def _fetch_newsapi(ticker: str) -> list[Article]:
    if not cfg.NEWS_API_KEY:
        logger.debug("NewsAPI key not configured, skipping")
        return []
    try:
        resp = requests.get(NEWSAPI_URL, params={
            "q": f'"{ticker}" NSE India stock',
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": 15,
            "apiKey": cfg.NEWS_API_KEY,
        }, timeout=10)
        if resp.status_code == 401:
            logger.warning("NewsAPI: invalid API key")
            return []
        if resp.status_code == 426:
            logger.warning("NewsAPI: plan upgrade required")
            return []
        resp.raise_for_status()
        articles = []
        for item in resp.json().get("articles", []):
            url = item.get("url",""); title = (item.get("title") or "").strip()
            if not url or not title or title == "[Removed]": continue
            articles.append(Article(
                ticker, "NEWSAPI", title,
                item.get("description",""), url,
                _safe_dt(item.get("publishedAt",""))
            ))
        logger.info("NewsAPI: fetched %d articles for %s", len(articles), ticker)
        return articles
    except Exception as exc:
        logger.warning("NewsAPI fetch failed for %s: %s", ticker, exc)
        return []


def _fetch_google_news(ticker: str) -> list[Article]:
    try:
        time.sleep(0.5)
        query = requests.utils.quote(f"{ticker} NSE India stock")
        url  = GOOGLE_RSS.format(q=query)
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        articles = []
        root = ElementTree.fromstring(resp.content)
        ch   = root.find("channel")
        if ch is None: return []
        for item in list(ch.findall("item"))[:15]:
            title = (item.findtext("title") or "").strip()
            link  = (item.findtext("link")  or "").strip()
            if not title or not link: continue
            articles.append(Article(
                ticker, "GOOGLE_NEWS", title,
                re.sub(r"<[^>]+>","", item.findtext("description") or ""),
                link, _safe_dt(item.findtext("pubDate") or "")
            ))
        logger.info("Google News: fetched %d articles for %s", len(articles), ticker)
        return articles
    except Exception as exc:
        logger.warning("Google News fetch failed for %s: %s", ticker, exc)
        return []


def _fetch_yahoo(ticker: str) -> list[Article]:
    try:
        time.sleep(0.5)
        resp = requests.get(YAHOO_URL.format(sym=f"{ticker}{NSE_SUFFIX}"),
                            headers=HEADERS, timeout=10)
        resp.raise_for_status()
        soup  = BeautifulSoup(resp.text, "html.parser")
        items = soup.find_all("a", attrs={"data-rapid-subsec": "story"}) or \
                soup.find_all("li", attrs={"class": re.compile(r"js-stream")})
        articles = []
        now = datetime.now(timezone.utc)
        for elem in items[:15]:
            a    = elem.find("a") if elem.name != "a" else elem
            if not a: continue
            text = a.get_text(strip=True)
            href = a.get("href","")
            if len(text) < 10: continue
            if not href.startswith("http"): href = f"https://finance.yahoo.com{href}"
            articles.append(Article(ticker, "YAHOO_FINANCE", text, "", href, now))
        logger.info("Yahoo Finance: fetched %d articles for %s", len(articles), ticker)
        return articles
    except Exception as exc:
        logger.warning("Yahoo Finance fetch failed for %s: %s", ticker, exc)
        return []


def fetch_all_news(ticker: str) -> list[Article]:
    raw: list[Article] = []
    raw.extend(_fetch_newsapi(ticker))
    raw.extend(_fetch_google_news(ticker))
    raw.extend(_fetch_yahoo(ticker))
    seen, unique = set(), []
    for a in raw:
        if a.url_hash not in seen:
            seen.add(a.url_hash)
            unique.append(a)
    unique.sort(key=lambda a: a.published_at, reverse=True)
    return unique


# ─── NLP helpers ──────────────────────────────────────────────────────────────

def _hf_request(model: str, payload: dict) -> Optional[dict]:
    if not cfg.HF_API_KEY:
        return None
    url = f"{cfg.HF_INFERENCE_URL}/{model}"
    for attempt in range(3):
        try:
            resp = requests.post(url, json=payload, timeout=30,
                                 headers={"Authorization": f"Bearer {cfg.HF_API_KEY}"})
            if resp.status_code == 503:
                wait = resp.json().get("estimated_time", 20)
                logger.info("HF model loading, waiting %.0fs", wait)
                time.sleep(min(float(wait), 60))
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("HF request failed (attempt %d): %s", attempt+1, exc)
            time.sleep(5)
    return None


def _keyword_score(text: str) -> tuple[str, float]:
    tokens = set(re.findall(r"\b\w+\b", text.lower()))
    pos = len(tokens & _POSITIVE_KW)
    neg = len(tokens & _NEGATIVE_KW)
    if pos == neg:      return "NEUTRAL",  0.0
    if pos > neg:       return "POSITIVE", round(min(1.0, (pos-neg)/(pos+neg)), 3)
    return "NEGATIVE",  round(max(-1.0, -(neg-pos)/(pos+neg)), 3)


def score_sentiment(text: str) -> tuple[str, float]:
    if not text.strip(): return "NEUTRAL", 0.0
    result = _hf_request(cfg.FINBERT_MODEL, {"inputs": text[:512]})
    if result:
        try:
            candidates = result[0] if isinstance(result[0], list) else result
            best = max(candidates, key=lambda x: x["score"])
            lbl  = best["label"].upper()
            sc   = best["score"]
            return lbl, (sc if lbl == "POSITIVE" else (-sc if lbl == "NEGATIVE" else 0.0))
        except Exception: pass
    return _keyword_score(text)


def detect_conflict(scored: list[tuple], ticker: str) -> tuple[bool, str]:
    scores = [s for _,_,s in scored]
    if len(scores) < 3: return False, ""
    std = statistics.stdev(scores)
    if std < cfg.SENTIMENT_CONFLICT_THRESHOLD: return False, ""
    pos = max(scored, key=lambda x: x[2])
    neg = min(scored, key=lambda x: x[2])
    msg = (f"[NEWS CONFLICT DETECTED] for {ticker}: std_dev={std:.2f}. "
           f"Positive: \"{pos[0].title[:80]}\" ({pos[2]:+.2f}). "
           f"Negative: \"{neg[0].title[:80]}\" ({neg[2]:+.2f}).")
    logger.warning(msg)
    return True, msg


def generate_summary(articles: list[Article], ticker: str) -> list[str]:
    if not articles:
        return [f"Insufficient news coverage for reliable sentiment analysis for {ticker}."]
    combined = " ".join(f"{a.title}. {a.description}" for a in articles[:cfg.NEWS_MAX_ARTICLES])[:1024]
    result   = _hf_request(cfg.SUMMARIZER_MODEL, {
        "inputs": combined,
        "parameters": {"max_length": 256, "min_length": 80, "do_sample": False},
    })
    if result and isinstance(result, list) and result[0].get("summary_text"):
        raw     = result[0]["summary_text"].strip()
        sents   = re.split(r"(?<=[.!?])\s+", raw)
        bullets = [f"• {s.strip()}" for s in sents[:3]]
        while len(bullets) < 3:
            bullets.append("• Monitor closely for further developments.")
        return bullets
    bullets = [f"• {a.title[:120]}" for a in articles[:3]]
    while len(bullets) < 3:
        bullets.append("• Additional analysis pending.")
    return bullets


# ─── Forecast ─────────────────────────────────────────────────────────────────

def _generate_forecast(ticker: str, sentiment_label: str) -> dict:
    slope = rev_cagr = None
    r2    = None
    try:
        yf_sym = f"{ticker}{NSE_SUFFIX}"
        info   = yf.Ticker(yf_sym).info
        rg     = info.get("revenueGrowth")
        rev_cagr = round(float(rg)*100, 2) if rg else None

        df = pd.read_parquet(_parquet_path(ticker)) if Path(_parquet_path(ticker)).exists() else None
        if df is not None and len(df) >= 60:
            close = df["Close"].dropna().values[-500:]
            y     = np.log(close).reshape(-1,1)
            X     = np.arange(len(y)).reshape(-1,1)
            model = LinearRegression().fit(X, y)
            r2    = float(model.score(X, y))
            slope = round(float(model.coef_[0][0]) * 252 * 100, 2)
    except Exception as exc:
        logger.warning("Forecast data fetch failed for %s: %s", ticker, exc)

    bullish = bearish = 0
    parts   = []
    if slope is not None:
        if slope >= 10:   parts.append(f"+{slope:.0f}% Price Trend");  bullish += 1
        elif slope <= -5: parts.append(f"{slope:.0f}% Price Decline"); bearish += 1
    if rev_cagr is not None:
        if rev_cagr >= 8: parts.append(f"{rev_cagr:.0f}% Revenue CAGR"); bullish += 1
        elif rev_cagr < 0: parts.append("Declining Revenue"); bearish += 1
    if sentiment_label == "POSITIVE":   parts.append("Positive Sentiment Support"); bullish += 1
    elif sentiment_label == "NEGATIVE": parts.append("Negative Sentiment Headwind"); bearish += 1

    if not parts:
        return {"outlook": f"Insufficient data for {ticker} forecast.", "direction": "NEUTRAL", "confidence": 0.0, "slope": slope, "rev_cagr": rev_cagr}

    direction = "BULLISH" if bullish > bearish else ("BEARISH" if bearish > bullish else "NEUTRAL")
    prefix    = {"BULLISH": "Bullish", "BEARISH": "Bearish", "NEUTRAL": "Neutral"}[direction]
    outlook   = f"{prefix} — {' + '.join(parts)}"
    consensus = abs(bullish - bearish) / max(1, bullish + bearish)
    confidence = round(min(1.0, (r2 or 0.0) * 0.4 + consensus * 0.6), 2)
    return {"outlook": outlook, "direction": direction, "confidence": confidence, "slope": slope, "rev_cagr": rev_cagr}


def _parquet_path(symbol: str) -> str:
    return str(Path(cfg.PARQUET_CACHE_DIR) / f"{symbol}_10yr.parquet")


# ─── NewsService ──────────────────────────────────────────────────────────────

class NewsService:

    def fetch_and_persist(self, ticker: str, db: Session) -> int:
        """
        Lightweight fetch used by the 60s scheduler job.
        Only fetches raw articles and persists new ones — skips full sentiment
        analysis to keep each run fast.  Returns count of new articles inserted.
        """
        articles = fetch_all_news(ticker)
        if not articles:
            logger.debug("fetch_and_persist: no articles for %s", ticker)
            return 0

        new_count = 0
        for art in articles:
            exists = db.query(NewsArticle).filter(NewsArticle.url_hash == art.url_hash).first()
            if not exists:
                lbl, sc = score_sentiment(f"{art.title}. {art.description}")
                db.add(NewsArticle(
                    url_hash=art.url_hash, ticker=ticker,
                    source_name=NewsSource(art.source),
                    title=art.title[:1000], description=art.description[:2000],
                    url=art.url[:2000], published_at=art.published_at,
                    sentiment_label=SentimentLabel(lbl.upper()) if lbl.upper() in ("POSITIVE","NEGATIVE","NEUTRAL") else SentimentLabel.NEUTRAL,
                    sentiment_score=sc, analysed_at=datetime.utcnow(),
                ))
                new_count += 1

        if new_count:
            try:
                db.commit()
                logger.info("fetch_and_persist: %d new articles for %s", new_count, ticker)
            except Exception as exc:
                db.rollback()
                logger.warning("fetch_and_persist commit failed for %s: %s", ticker, exc)

        return new_count

    def analyse(self, ticker: str, db: Session) -> NewsAnalysis:
        """Full pipeline with 60-min cache."""
        cached = (
            db.query(NewsAnalysis)
              .filter(NewsAnalysis.ticker == ticker,
                      NewsAnalysis.is_cache_valid == True,
                      NewsAnalysis.cache_expires_at > datetime.utcnow())
              .order_by(desc(NewsAnalysis.analysed_at))
              .first()
        )
        if cached:
            logger.info("News cache HIT for %s", ticker)
            return cached

        logger.info("Running news analysis for %s (cache miss)", ticker)
        articles = fetch_all_news(ticker)

        scored = []
        for art in articles:
            lbl, sc = score_sentiment(f"{art.title}. {art.description}")
            scored.append((art, lbl, sc))

        conflict, conflict_detail = detect_conflict(scored, ticker)
        summary  = generate_summary(articles, ticker)
        forecast = _generate_forecast(ticker, scored[0][1] if scored else "NEUTRAL")

        scores = [s for _,_,s in scored]
        avg    = round(statistics.mean(scores), 4) if scores else None
        std    = round(statistics.stdev(scores), 4) if len(scores) >= 2 else 0.0
        if avg is None:       lbl_agg = SentimentLabel.NEUTRAL
        elif avg > 0.15:      lbl_agg = SentimentLabel.POSITIVE
        elif avg < -0.15:     lbl_agg = SentimentLabel.NEGATIVE
        else:                 lbl_agg = SentimentLabel.NEUTRAL

        for art, lbl, sc in scored:
            exists = db.query(NewsArticle).filter(NewsArticle.url_hash == art.url_hash).first()
            if not exists:
                db.add(NewsArticle(
                    url_hash=art.url_hash, ticker=ticker,
                    source_name=NewsSource(art.source),
                    title=art.title[:1000], description=art.description[:2000],
                    url=art.url[:2000], published_at=art.published_at,
                    sentiment_label=SentimentLabel(lbl.upper()) if lbl.upper() in ("POSITIVE","NEGATIVE","NEUTRAL") else SentimentLabel.NEUTRAL,
                    sentiment_score=sc, analysed_at=datetime.utcnow(),
                ))

        db.query(NewsAnalysis).filter(NewsAnalysis.ticker == ticker).update({"is_cache_valid": False})

        row = NewsAnalysis(
            ticker                = ticker,
            avg_sentiment_score   = avg,
            sentiment_label       = lbl_agg,
            sentiment_std_dev     = std,
            articles_analysed     = len(articles),
            positive_count        = sum(1 for _,l,_ in scored if l == "POSITIVE"),
            negative_count        = sum(1 for _,l,_ in scored if l == "NEGATIVE"),
            neutral_count         = sum(1 for _,l,_ in scored if l == "NEUTRAL"),
            conflict_detected     = conflict,
            conflict_detail       = conflict_detail if conflict else None,
            executive_summary     = json.dumps(summary),
            forecast_outlook      = forecast["outlook"],
            forecast_direction    = forecast["direction"],
            forecast_confidence   = forecast["confidence"],
            price_slope_annual    = forecast["slope"],
            revenue_cagr          = forecast["rev_cagr"],
            insufficient_coverage = len(articles) == 0,
            coverage_message      = (f"Insufficient news coverage for reliable sentiment analysis for {ticker}."
                                     if len(articles) == 0 else None),
            analysed_at           = datetime.utcnow(),
            cache_expires_at      = datetime.utcnow() + timedelta(seconds=cfg.NEWS_CACHE_TTL_SECS),
            is_cache_valid        = True,
        )
        db.add(row)
        db.flush()
        db.refresh(row)
        db.commit()
        logger.info("News analysis saved for %s (score=%.3f label=%s articles=%d conflict=%s)",
                    ticker, avg or 0, lbl_agg.value, len(articles), conflict)
        return row

    def apply_sentiment_override(
        self,
        ticker:       str,
        signal_dict:  dict,
        analysis:     NewsAnalysis,
        db:           Session,
    ) -> dict:
        result        = dict(signal_dict)
        score         = analysis.avg_sentiment_score or 0.0
        orig_signal   = signal_dict.get("signal", "HOLD")
        confidence    = float(signal_dict.get("confidence", 50.0))
        override      = OverrideAction.NO_CHANGE

        if score < cfg.SENTIMENT_NEGATIVE_THRESHOLD and orig_signal == "BUY":
            result["signal"]     = "HOLD / CAUTION: High Negative News"
            result["confidence"] = max(5.0, confidence - 30.0)
            override             = OverrideAction.DEMOTED
            logger.warning("OVERRIDE [%s]: BUY demoted (sentiment=%.3f)", ticker, score)

        elif score > cfg.SENTIMENT_POSITIVE_THRESHOLD and orig_signal == "HOLD":
            result["signal"]     = "WATCH: Improving Sentiment"
            result["confidence"] = min(100.0, confidence + 15.0)
            override             = OverrideAction.UPGRADED
            logger.info("OVERRIDE [%s]: HOLD upgraded (sentiment=%.3f)", ticker, score)

        result["sentiment_score"]    = round(score, 4)
        result["sentiment_label"]    = analysis.sentiment_label.value if analysis.sentiment_label else "NEUTRAL"
        result["sentiment_override"] = (override != OverrideAction.NO_CHANGE)
        result["original_signal"]    = orig_signal
        result["conflict_warning"]   = analysis.conflict_detail if analysis.conflict_detected else None
        result["executive_summary"]  = json.loads(analysis.executive_summary or "[]")
        result["forecast_outlook"]   = analysis.forecast_outlook

        if override != OverrideAction.NO_CHANGE:
            db.add(SentimentOverride(
                ticker=ticker, original_signal=orig_signal,
                override_action=override,
                final_signal=result["signal"],
                sentiment_score=score,
                technical_confidence=confidence,
                reason=f"sentiment={score:.3f} threshold={cfg.SENTIMENT_NEGATIVE_THRESHOLD if override==OverrideAction.DEMOTED else cfg.SENTIMENT_POSITIVE_THRESHOLD}",
            ))
            db.flush()

        return result


_news_service: Optional[NewsService] = None

def get_news_service() -> NewsService:
    global _news_service
    if _news_service is None:
        _news_service = NewsService()
    return _news_service
