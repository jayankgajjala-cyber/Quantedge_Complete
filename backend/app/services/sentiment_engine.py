"""
app/services/sentiment_engine.py — Dynamic Hybrid Sentiment Engine
===================================================================

PIPELINE
  FinBERT + VADER + Macro Context, weights selected dynamically per article
  via WEIGHTING_PROFILES keyed on section / event_type.

WEIGHTING PROFILES
  corporate   {finbert:0.7, macro:0.1, vader:0.2}  Earnings, M&A
  macro_heavy {finbert:0.2, macro:0.7, vader:0.1}  macro_impact section / RBI / Fed / Inflation
  commodity   {finbert:0.3, macro:0.5, vader:0.2}  Oil, Gold, Metals sector
  default     {finbert:0.4, macro:0.3, vader:0.3}  all other cases

THRESHOLDS
  Buy  ≥ +0.15  |  Sell ≤ -0.15  |  Hold otherwise
  Confidence gate: < 0.40 → Hold regardless of score

DATA FLOW
  Raw text  ← MongoDB    (news_service.py feeds enrich_batch)
  Scores    → Neon       (sentiment_results table via _persist_to_neon)
  Macro     ← MongoDB    (macro_signals collection, 24h rolling)

OCI ARM FREE TIER SAFETY
  TORCH_DEVICE  read from settings (default: "cpu")  — prevents GPU OOM
  HF_HOME       read from settings (default: "/tmp/huggingface") — keeps
                model cache off the small root disk; /tmp is on tmpfs

FEATURES
  • Dynamic weight resolution (section + event_type + sectors)
  • Time-decay applied per article (exponential, half-life = 12h)
  • Source reliability weighting (Tier1=1.0, Tier2=0.7, Unknown=0.4)
  • Entity extraction → primary/secondary stocks (NSE/BSE) + sectors
  • Event classification → Earnings | Regulation | Macro | M&A | Fraud/Negative
  • Confidence score = f(FinBERT prob, macro confidence, source reliability)
  • Async batch processing — DB-safe, non-blocking
  • Neon persistence via upsert (url is primary key — idempotent)
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── OCI ARM / HuggingFace environment setup ───────────────────────────────────
# Applied at import time so that transformers picks up HF_HOME before any
# model download or cache lookup occurs.

def _apply_oci_env() -> None:
    from app.core.config import settings  # lazy to avoid circular imports at module level
    # HF_HOME controls where transformers caches tokenizers and model weights.
    # /tmp/huggingface keeps it off the 47 GB root volume on OCI ARM free tier.
    hf_home = settings.HF_HOME or "/tmp/huggingface"
    os.environ.setdefault("HF_HOME",            hf_home)
    os.environ.setdefault("TRANSFORMERS_CACHE", hf_home)  # legacy compat
    os.makedirs(hf_home, exist_ok=True)

_apply_oci_env()


# ── Dynamic Weighting Profiles ────────────────────────────────────────────────

class _WeightProfile:
    __slots__ = ("finbert", "macro", "vader", "name")

    def __init__(self, name: str, finbert: float, macro: float, vader: float) -> None:
        assert abs(finbert + macro + vader - 1.0) < 1e-6, "Weights must sum to 1.0"
        self.name    = name
        self.finbert = finbert
        self.macro   = macro
        self.vader   = vader


WEIGHTING_PROFILES: Dict[str, _WeightProfile] = {
    "corporate":   _WeightProfile("corporate",   finbert=0.7, macro=0.1, vader=0.2),
    "macro_heavy": _WeightProfile("macro_heavy", finbert=0.2, macro=0.7, vader=0.1),
    "commodity":   _WeightProfile("commodity",   finbert=0.3, macro=0.5, vader=0.2),
    "default":     _WeightProfile("default",     finbert=0.4, macro=0.3, vader=0.3),
}

_SECTION_PROFILE: Dict[str, str] = {
    "macro_impact":  "macro_heavy",
    "global_market": "macro_heavy",
    "indian_market": "default",
    "swing_signals": "corporate",
}

_EVENT_PROFILE: Dict[str, str] = {
    "Earnings":       "corporate",
    "M&A":            "corporate",
    "Regulation":     "macro_heavy",
    "Macro":          "macro_heavy",
    "Fraud/Negative": "corporate",
    "General":        "default",
}

_COMMODITY_SECTORS = {"Energy", "Metals"}


def _resolve_profile(section: str, event_type: str, sectors: List[str]) -> _WeightProfile:
    """Priority: event_type > commodity sector > section > default."""
    if event_type in _EVENT_PROFILE and event_type != "General":
        return WEIGHTING_PROFILES[_EVENT_PROFILE[event_type]]
    if any(s in _COMMODITY_SECTORS for s in sectors):
        return WEIGHTING_PROFILES["commodity"]
    return WEIGHTING_PROFILES.get(
        _SECTION_PROFILE.get(section, "default"),
        WEIGHTING_PROFILES["default"],
    )


# ── Static lookup tables ──────────────────────────────────────────────────────

DECAY_HALF_LIFE_H = 12.0

SOURCE_RELIABILITY: Dict[str, float] = {
    "reuters": 1.0, "bloomberg": 1.0, "financial times": 1.0,
    "ft": 1.0, "wsj": 1.0, "wall street journal": 1.0,
    "economic times": 0.7, "livemint": 0.7, "mint": 0.7,
    "moneycontrol": 0.7, "cnbc": 0.7, "business standard": 0.7,
    "hindu businessline": 0.7, "gnews": 0.7, "yahoo finance": 0.7,
}

EVENT_WEIGHT_MULTIPLIER: Dict[str, float] = {
    "Earnings": 1.20, "Regulation": 1.10, "Macro": 1.05,
    "M&A": 1.15, "Fraud/Negative": 1.30, "General": 1.00,
}

NSE_ENTITY_MAP: Dict[str, str] = {
    "reliance": "RELIANCE", "ril": "RELIANCE",
    "tcs": "TCS", "tata consultancy": "TCS",
    "infosys": "INFY", "infy": "INFY",
    "hdfc bank": "HDFCBANK", "hdfcbank": "HDFCBANK",
    "icici bank": "ICICIBANK", "icici": "ICICIBANK",
    "kotak": "KOTAKBANK", "kotak mahindra": "KOTAKBANK",
    "wipro": "WIPRO", "hcl": "HCLTECH", "hcltech": "HCLTECH",
    "bajaj finance": "BAJFINANCE", "bajfinance": "BAJFINANCE",
    "bajaj finserv": "BAJAJFINSV",
    "bharti airtel": "BHARTIARTL", "airtel": "BHARTIARTL",
    "asian paints": "ASIANPAINT", "maruti": "MARUTI",
    "maruti suzuki": "MARUTI", "titan": "TITAN",
    "nestle india": "NESTLEIND", "nestle": "NESTLEIND",
    "hindustan unilever": "HINDUNILVR", "hul": "HINDUNILVR",
    "itc": "ITC", "axis bank": "AXISBANK",
    "state bank": "SBIN", "sbi": "SBIN",
    "sun pharma": "SUNPHARMA",
    "dr reddy": "DRREDDY", "dr. reddy": "DRREDDY",
    "cipla": "CIPLA", "adani": "ADANIENT",
    "adani ports": "ADANIPORTS", "adani green": "ADANIGREEN",
    "adani enterprises": "ADANIENT",
    "tata motors": "TATAMOTORS", "tata steel": "TATASTEEL",
    "tata power": "TATAPOWER", "ongc": "ONGC", "ntpc": "NTPC",
    "power grid": "POWERGRID", "coal india": "COALINDIA",
    "upl": "UPL", "divis": "DIVISLAB", "divi's": "DIVISLAB",
    "dmart": "DMART", "avenue supermarts": "DMART",
    "zomato": "ZOMATO", "paytm": "PAYTM", "one97": "PAYTM",
    "nykaa": "NYKAA", "policybazaar": "POLICYBZR",
    "tech mahindra": "TECHM", "ltimindtree": "LTIM", "lti": "LTIM",
    "l&t": "LT", "larsen": "LT", "larsen & toubro": "LT",
    "ultratech": "ULTRACEMCO", "grasim": "GRASIM",
    "hindalco": "HINDALCO", "jsw steel": "JSWSTEEL",
    "m&m": "M&M", "mahindra": "M&M",
    "hero motocorp": "HEROMOTOCO", "hero": "HEROMOTOCO",
    "eicher": "EICHERMOT", "shriram finance": "SHRIRAMFIN",
    "srf": "SRF", "pi industries": "PIIND",
    "mphasis": "MPHASIS", "persistent": "PERSISTENT",
    "coforge": "COFORGE",
    "indusind": "INDUSINDBK", "indusind bank": "INDUSINDBK",
    "yes bank": "YESBANK", "bandhan": "BANDHANBNK",
    "federal bank": "FEDERALBNK", "rbl bank": "RBLBANK",
    "canara bank": "CANARABANK", "bank of baroda": "BANKBARODA",
    "pnb": "PNB", "punjab national": "PNB",
    "godrej": "GODREJCP", "dabur": "DABUR", "marico": "MARICO",
    "britannia": "BRITANNIA", "colgate": "COLPAL",
    "pidilite": "PIDILITIND", "berger paints": "BERGEPAINT",
    "abbott india": "ABBOTINDIA", "torrent pharma": "TORNTPHARM",
    "lupin": "LUPIN", "aurobindo": "AUROPHARMA",
    "biocon": "BIOCON", "alkem": "ALKEM", "zydus": "ZYDUSLIFE",
    "havells": "HAVELLS", "voltas": "VOLTAS",
    "dixon": "DIXON", "amber": "AMBER",
    "irctc": "IRCTC", "indian railway": "IRCTC",
    "interglobe": "INDIGO", "indigo": "INDIGO",
    "spicejet": "SPICEJET", "concor": "CONCOR",
    "balkrishna": "BALKRISIND", "bkt": "BALKRISIND",
    "mrf": "MRF", "apollo tyres": "APOLLOTYRE",
    "ceat": "CEATLTD", "exide": "EXIDEIND",
    "amara raja": "AMARAJABAT", "manappuram": "MANAPPURAM",
    "muthoot": "MUTHOOTFIN", "cholafin": "CHOLAFIN", "chola": "CHOLAFIN",
    "page industries": "PAGEIND", "varun beverages": "VBL",
    "united spirits": "MCDOWELL-N", "radico": "RADICO",
    "jubilant": "JUBLFOOD", "jubilant foodworks": "JUBLFOOD",
    "westlife": "WESTLIFE", "devyani": "DEVYANI",
    "nse": "NSEI", "sensex": "SENSEX", "nifty": "NIFTY",
}

SECTOR_KEYWORDS: Dict[str, List[str]] = {
    "Banking":  ["bank", "nbfc", "lending", "deposit", "npa", "credit"],
    "IT":       ["software", "it ", "tech", "digital", "cloud", "saas", "outsourcing"],
    "Pharma":   ["pharma", "drug", "medicine", "fda", "usfda", "api", "biotech"],
    "Energy":   ["oil", "gas", "refinery", "petroleum", "crude", "power", "renewable", "solar", "wind"],
    "Auto":     ["auto", "vehicle", "ev ", "electric vehicle", "car", "motorcycle", "tyre"],
    "FMCG":     ["fmcg", "consumer", "staples", "food", "beverage", "household"],
    "Metals":   ["steel", "aluminium", "copper", "zinc", "metals", "mining"],
    "Infra":    ["infrastructure", "cement", "construction", "road", "highway", "airport"],
    "Finance":  ["insurance", "mutual fund", "asset management", "wealth", "brokerage"],
    "Telecom":  ["telecom", "5g", "spectrum", "airtel", "jio", "vi "],
    "Realty":   ["real estate", "realty", "housing", "property", "reit"],
    "Aviation": ["airline", "aviation", "aircraft", "flight"],
}

EVENT_KEYWORDS: Dict[str, List[str]] = {
    "Earnings":       ["earnings", "profit", "revenue", "quarterly", "q1 ", "q2 ", "q3 ", "q4 ",
                       "results", "ebitda", "pat", "net income", "loss"],
    "Regulation":     ["rbi", "sebi", "regulation", "policy", "compliance", "ban", "penalty",
                       "fine", "licence", "circular", "guidelines", "norms", "rate cut", "rate hike"],
    "Macro":          ["gdp", "inflation", "cpi", "wpi", "repo rate", "fed", "federal reserve",
                       "fiscal", "budget", "trade deficit", "fii", "dii", "foreign investment",
                       "s&p 500", "dow jones", "nifty 50", "global market"],
    "M&A":            ["merger", "acquisition", "takeover", "buyout", "stake", "deal",
                       "joint venture", "partnership", "collaboration", "mou"],
    "Fraud/Negative": ["fraud", "scam", "ponzi", "money laundering", "investigation", "arrest",
                       "bankruptcy", "default", "npa", "write-off", "probe", "raid", "cbi", "ed "],
}


# ── Lazy model singletons ─────────────────────────────────────────────────────

_finbert_pipeline = None
_vader_analyzer   = None
_finbert_lock     = asyncio.Lock()
_vader_lock       = asyncio.Lock()


async def _get_finbert():
    global _finbert_pipeline
    async with _finbert_lock:
        if _finbert_pipeline is not None:
            return _finbert_pipeline
        try:
            from transformers import pipeline as hf_pipeline  # type: ignore
            from app.core.config import settings

            # Resolve device from settings — keeps OCI ARM Free Tier safe
            raw_device = (settings.TORCH_DEVICE or "cpu").strip().lower()
            if raw_device == "cpu":
                device_arg = -1
            elif raw_device.startswith("cuda"):
                try:
                    device_arg = int(raw_device.split(":")[-1]) if ":" in raw_device else 0
                except ValueError:
                    device_arg = 0
            else:
                device_arg = -1   # unknown → safe CPU fallback

            logger.info(
                "[sentiment] Loading FinBERT via HuggingFace Transformers "
                "(device=%s, HF_HOME=%s)…",
                raw_device, os.environ.get("HF_HOME", "not set"),
            )
            _finbert_pipeline = hf_pipeline(
                "text-classification",
                model="ProsusAI/finbert",
                tokenizer="ProsusAI/finbert",
                top_k=None,
                device=device_arg,
                truncation=True,
                max_length=512,
            )
            logger.info("[sentiment] FinBERT ready (device=%s)", raw_device)
        except Exception as exc:
            logger.warning(
                "[sentiment] FinBERT unavailable (%s) — VADER-only fallback active", exc
            )
            _finbert_pipeline = None
        return _finbert_pipeline


async def _get_vader():
    global _vader_analyzer
    async with _vader_lock:
        if _vader_analyzer is not None:
            return _vader_analyzer
        try:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer  # type: ignore
            _vader_analyzer = SentimentIntensityAnalyzer()
        except Exception as exc:
            logger.warning("[sentiment] VADER unavailable: %s", exc)
            _vader_analyzer = None
        return _vader_analyzer


# ── Source reliability lookup ─────────────────────────────────────────────────

def _source_reliability(source: str) -> float:
    s = source.lower().strip()
    for name, score in SOURCE_RELIABILITY.items():
        if name in s:
            return score
    return 0.4


# ── Time decay ────────────────────────────────────────────────────────────────

def _time_decay(published_at: datetime) -> float:
    now   = datetime.now(timezone.utc)
    pub   = published_at.replace(tzinfo=timezone.utc) if published_at.tzinfo is None else published_at
    age_h = (now - pub).total_seconds() / 3600.0
    return math.exp(-math.log(2) * age_h / DECAY_HALF_LIFE_H)


# ── Entity extraction ─────────────────────────────────────────────────────────

_NOISE = {
    "THE", "AND", "FOR", "NSE", "BSE", "RBI", "FED", "FII", "DII",
    "GDP", "CPI", "IPO", "ETF", "USD", "INR", "USA", "CEO", "CFO",
    "WITH", "FROM", "INTO", "THIS", "THAT",
}


def _extract_entities(text: str) -> Tuple[List[str], List[str], List[str]]:
    lower    = text.lower()
    primary:  List[str] = []
    secondary: List[str] = []

    for alias, symbol in NSE_ENTITY_MAP.items():
        if alias in lower and symbol not in primary:
            primary.append(symbol)

    caps = re.findall(r'\b([A-Z]{2,12})\b', text)
    for c in caps:
        if c not in _NOISE and c not in primary and len(c) >= 3:
            secondary.append(c)

    sectors: List[str] = []
    for sector, kws in SECTOR_KEYWORDS.items():
        if any(kw in lower for kw in kws):
            sectors.append(sector)

    return (
        list(dict.fromkeys(primary))[:5],
        list(dict.fromkeys(secondary))[:5],
        list(dict.fromkeys(sectors))[:3],
    )


# ── Event classification ──────────────────────────────────────────────────────

def _classify_event(text: str) -> str:
    lower  = text.lower()
    scores = {ev: sum(1 for kw in kws if kw in lower) for ev, kws in EVENT_KEYWORDS.items()}
    best   = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else "General"


# ── FinBERT inference (sync, runs in executor) ────────────────────────────────

def _finbert_score_sync(pipe, text: str) -> Tuple[float, float]:
    try:
        results   = pipe(text[:512])
        label_map = {"positive": 1.0, "negative": -1.0, "neutral": 0.0}
        best      = max(results[0], key=lambda x: x["score"])
        return label_map.get(best["label"].lower(), 0.0), best["score"]
    except Exception as exc:
        logger.debug("[sentiment] FinBERT inference error: %s", exc)
        return 0.0, 0.5


async def _finbert_score(text: str) -> Tuple[float, float]:
    pipe = await _get_finbert()
    if pipe is None:
        return 0.0, 0.5
    return await asyncio.get_event_loop().run_in_executor(
        None, _finbert_score_sync, pipe, text
    )


# ── VADER inference ───────────────────────────────────────────────────────────

def _vader_score_sync(analyzer, text: str) -> float:
    try:
        return analyzer.polarity_scores(text)["compound"]
    except Exception:
        return 0.0


async def _vader_score(text: str) -> float:
    analyzer = await _get_vader()
    if analyzer is None:
        return 0.0
    return await asyncio.get_event_loop().run_in_executor(
        None, _vader_score_sync, analyzer, text
    )


# ── Macro Context Layer (reads from MongoDB macro_signals) ────────────────────

async def _get_macro_signal() -> Tuple[float, float]:
    try:
        from motor.motor_asyncio import AsyncIOMotorClient  # type: ignore
        from app.core.config import settings

        client = AsyncIOMotorClient(settings.MONGODB_URI, serverSelectionTimeoutMS=3000)
        col    = client["quantedge"]["macro_signals"]
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        cursor = col.find({"updated_at": {"$gte": cutoff}})

        total_weight = weighted_dir = 0.0
        confidences: List[float] = []

        async for doc in cursor:
            direction  = float(doc.get("direction",  0))
            weight     = float(doc.get("weight",     1.0))
            confidence = float(doc.get("confidence", 0.5))
            weighted_dir += direction * weight * confidence
            total_weight += weight
            confidences.append(confidence)

        if total_weight == 0:
            return 0.0, 0.3

        return (
            max(-1.0, min(1.0, weighted_dir / total_weight)),
            sum(confidences) / len(confidences),
        )
    except Exception as exc:
        logger.debug("[sentiment] Macro signal fetch failed: %s", exc)
        return 0.0, 0.3


# ── Seed default macro signals on startup ────────────────────────────────────

async def ensure_macro_signals() -> None:
    try:
        from motor.motor_asyncio import AsyncIOMotorClient  # type: ignore
        from pymongo import UpdateOne  # type: ignore
        from app.core.config import settings

        client = AsyncIOMotorClient(settings.MONGODB_URI, serverSelectionTimeoutMS=3000)
        col    = client["quantedge"]["macro_signals"]
        now    = datetime.now(timezone.utc)
        baseline = [
            {"factor": "RBI_POLICY",     "direction":  0,    "weight": 1.5, "confidence": 0.50, "updated_at": now},
            {"factor": "INDIA_CPI",      "direction": -0.5,  "weight": 1.0, "confidence": 0.60, "updated_at": now},
            {"factor": "SP500_TREND",    "direction":  1,    "weight": 1.2, "confidence": 0.70, "updated_at": now},
            {"factor": "DOW_JONES_TREND","direction":  1,    "weight": 1.0, "confidence": 0.60, "updated_at": now},
            {"factor": "NIFTY50_TREND",  "direction":  0.5,  "weight": 1.3, "confidence": 0.65, "updated_at": now},
            {"factor": "FII_FLOW",       "direction":  0,    "weight": 1.1, "confidence": 0.50, "updated_at": now},
            {"factor": "DXY_INDEX",      "direction": -0.3,  "weight": 0.8, "confidence": 0.55, "updated_at": now},
            {"factor": "CRUDE_OIL",      "direction": -0.5,  "weight": 0.9, "confidence": 0.60, "updated_at": now},
        ]
        ops = [UpdateOne({"factor": s["factor"]}, {"$setOnInsert": s}, upsert=True) for s in baseline]
        await col.bulk_write(ops, ordered=False)
    except Exception as exc:
        logger.debug("[sentiment] ensure_macro_signals failed: %s", exc)


# ── Composite scoring ─────────────────────────────────────────────────────────

def _normalize(score: float) -> float:
    return max(-1.0, min(1.0, score))


def _label_from_score(score: float) -> str:
    if score >  0.15: return "Bullish"
    if score < -0.15: return "Bearish"
    return "Neutral"


def _action_from_score(score: float, confidence: float) -> str:
    if confidence < 0.40:
        return "Hold"
    if score >=  0.15: return "Buy"
    if score <= -0.15: return "Sell"
    return "Hold"


def _build_reasoning(
    finbert_score: float,
    vader_score:   float,
    macro_score:   float,
    macro_conf:    float,
    event_type:    str,
    source_rel:    float,
    decay:         float,
    sectors:       List[str],
    profile_name:  str = "default",
) -> str:
    parts = [f"FinBERT: {_label_from_score(finbert_score)} ({finbert_score:+.2f})"]
    if abs(macro_score) > 0.05:
        parts.append(f"Macro: {_label_from_score(macro_score)} (conf={macro_conf:.0%})")
    if event_type != "General":
        parts.append(f"Event: {event_type}")
    if sectors:
        parts.append(f"Sectors: {', '.join(sectors[:2])}")
    if decay < 0.5:
        parts.append(f"Age-decay: {decay:.0%}")
    tier = "T1" if source_rel >= 1.0 else ("T2" if source_rel >= 0.7 else "Unk")
    parts.append(f"Source: {tier} | Profile: {profile_name}")
    return " | ".join(parts)


async def score_article(
    title:        str,
    summary:      str,
    source:       str,
    published_at: datetime,
    section:      str = "indian_market",
) -> Dict:
    """
    Core scoring function. Pulls raw text, runs FinBERT+VADER+Macro in parallel,
    resolves dynamic weight profile, and returns the full enriched payload.
    The caller (enrich_batch → news_service) is responsible for MongoDB upsert
    of raw news and for calling _persist_to_neon for the score record.
    """
    text = f"{title}. {summary}"

    (finbert_raw, finbert_prob), vader_raw, (macro_raw, macro_conf) = await asyncio.gather(
        _finbert_score(text),
        _vader_score(text),
        _get_macro_signal(),
    )

    decay      = _time_decay(published_at)
    source_rel = _source_reliability(source)

    event_type                              = _classify_event(text)
    primary_stocks, secondary_stocks, sectors = _extract_entities(text)

    profile    = _resolve_profile(section, event_type, sectors)
    event_mult = EVENT_WEIGHT_MULTIPLIER.get(event_type, 1.0)

    composite = (
        profile.finbert * finbert_raw +
        profile.vader   * vader_raw   +
        profile.macro   * macro_raw
    ) * source_rel * event_mult * decay
    composite  = _normalize(composite)

    confidence = _normalize(
        finbert_prob * 0.5 + macro_conf * 0.3 + source_rel * 0.2
    )

    return {
        "sentiment_score":    round(composite,    4),
        "sentiment_label":    _label_from_score(composite),
        "confidence":         round(confidence,   4),
        "confidence_pct":     round(confidence * 100, 1),
        "action":             _action_from_score(composite, confidence),
        "reasoning":          _build_reasoning(
                                  finbert_raw, vader_raw, macro_raw, macro_conf,
                                  event_type, source_rel, decay, sectors, profile.name,
                              ),
        "event_type":         event_type,
        "weight_profile":     profile.name,
        "primary_stocks":     primary_stocks,
        "secondary_stocks":   secondary_stocks,
        "sectors":            sectors,
        "source_reliability": source_rel,
        "time_decay":         round(decay,        4),
        "finbert_score":      round(finbert_raw,  4),
        "finbert_prob":       round(finbert_prob, 4),
        "vader_score":        round(vader_raw,    4),
        "macro_score":        round(macro_raw,    4),
        "macro_confidence":   round(macro_conf,   4),
    }


# ── Neon persistence ──────────────────────────────────────────────────────────

async def _persist_to_neon(articles: List[Dict]) -> None:
    """
    Upsert sentiment scores into Neon (sentiment_results table).
    url is the primary key — re-running is fully idempotent.
    Silently skips if Neon is not configured.
    """
    try:
        from app.db.session import _NeonSession  # type: ignore
        if _NeonSession is None:
            return

        from sqlalchemy.dialects.postgresql import insert as pg_insert  # type: ignore
        from app.models.sentiment_result import SentimentResult

        rows = []
        for a in articles:
            if not a.get("url") or not a.get("sentiment_score") is not None:
                continue
            rows.append({
                "url":               a["url"],
                "title":             a.get("title", ""),
                "source":            a.get("source", ""),
                "section":           a.get("section", ""),
                "published_at":      a.get("published_at", datetime.now(timezone.utc)),
                "scored_at":         datetime.now(timezone.utc),
                "sentiment_score":   a.get("sentiment_score",   0.0),
                "sentiment_label":   a.get("sentiment_label",   "Neutral"),
                "confidence":        a.get("confidence",        0.3),
                "confidence_pct":    a.get("confidence_pct",    30.0),
                "action":            a.get("action",            "Hold"),
                "reasoning":         a.get("reasoning",         ""),
                "weight_profile":    a.get("weight_profile",    "default"),
                "finbert_score":     a.get("finbert_score"),
                "finbert_prob":      a.get("finbert_prob"),
                "vader_score":       a.get("vader_score"),
                "macro_score":       a.get("macro_score"),
                "macro_confidence":  a.get("macro_confidence"),
                "event_type":        a.get("event_type",        "General"),
                "primary_stocks":    a.get("primary_stocks",    []),
                "secondary_stocks":  a.get("secondary_stocks",  []),
                "sectors":           a.get("sectors",           []),
                "source_reliability":a.get("source_reliability"),
                "time_decay":        a.get("time_decay"),
            })

        if not rows:
            return

        async with _NeonSession() as session:
            stmt = (
                pg_insert(SentimentResult)
                .values(rows)
                .on_conflict_do_update(
                    index_elements=["url"],
                    set_={
                        "sentiment_score":   pg_insert(SentimentResult).excluded.sentiment_score,
                        "sentiment_label":   pg_insert(SentimentResult).excluded.sentiment_label,
                        "confidence":        pg_insert(SentimentResult).excluded.confidence,
                        "confidence_pct":    pg_insert(SentimentResult).excluded.confidence_pct,
                        "action":            pg_insert(SentimentResult).excluded.action,
                        "reasoning":         pg_insert(SentimentResult).excluded.reasoning,
                        "weight_profile":    pg_insert(SentimentResult).excluded.weight_profile,
                        "finbert_score":     pg_insert(SentimentResult).excluded.finbert_score,
                        "finbert_prob":      pg_insert(SentimentResult).excluded.finbert_prob,
                        "vader_score":       pg_insert(SentimentResult).excluded.vader_score,
                        "macro_score":       pg_insert(SentimentResult).excluded.macro_score,
                        "macro_confidence":  pg_insert(SentimentResult).excluded.macro_confidence,
                        "event_type":        pg_insert(SentimentResult).excluded.event_type,
                        "primary_stocks":    pg_insert(SentimentResult).excluded.primary_stocks,
                        "secondary_stocks":  pg_insert(SentimentResult).excluded.secondary_stocks,
                        "sectors":           pg_insert(SentimentResult).excluded.sectors,
                        "source_reliability":pg_insert(SentimentResult).excluded.source_reliability,
                        "time_decay":        pg_insert(SentimentResult).excluded.time_decay,
                        "scored_at":         pg_insert(SentimentResult).excluded.scored_at,
                    },
                )
            )
            await session.execute(stmt)
            await session.commit()
        logger.info("[sentiment] Persisted %d score(s) to Neon", len(rows))
    except Exception as exc:
        logger.warning("[sentiment] Neon persist failed (non-fatal): %s", exc)


# ── Batch processor ───────────────────────────────────────────────────────────

BATCH_SIZE    = 8
BATCH_TIMEOUT = 30


async def enrich_batch(articles: List[Dict]) -> List[Dict]:
    """
    Scores a list of raw news dicts in batches of BATCH_SIZE.
    After scoring, persists results to Neon asynchronously (fire-and-forget).
    Returns the enriched list (raw news + sentiment fields merged).
    """
    enriched: List[Dict] = []

    for i in range(0, len(articles), BATCH_SIZE):
        chunk = articles[i : i + BATCH_SIZE]
        tasks = [
            score_article(
                title        = a.get("title", ""),
                summary      = a.get("summary", ""),
                source       = a.get("source", ""),
                published_at = a.get("published_at", datetime.now(timezone.utc)),
                section      = a.get("section", "indian_market"),
            )
            for a in chunk
        ]
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=BATCH_TIMEOUT,
            )
            batch_enriched: List[Dict] = []
            for article, result in zip(chunk, results):
                merged = dict(article)
                if isinstance(result, dict):
                    merged.update(result)
                else:
                    merged.update({
                        "sentiment_score":   0.0,
                        "sentiment_label":  "Neutral",
                        "confidence":        0.3,
                        "confidence_pct":    30.0,
                        "action":           "Hold",
                        "reasoning":        "Scoring error — defaulting to neutral",
                        "event_type":       "General",
                        "weight_profile":   "default",
                        "primary_stocks":   [],
                        "secondary_stocks": [],
                        "sectors":          [],
                    })
                batch_enriched.append(merged)

            # Persist scores to Neon (non-blocking — do not await failure)
            asyncio.create_task(_persist_to_neon(batch_enriched))
            enriched.extend(batch_enriched)

        except asyncio.TimeoutError:
            logger.warning("[sentiment] Batch %d timed out — skipping", i)
            enriched.extend(chunk)

    return enriched


# ── Clustering ────────────────────────────────────────────────────────────────

def cluster_news(articles: List[Dict]) -> Dict[str, List[Dict]]:
    clusters: Dict[str, List[Dict]] = {}
    for art in articles:
        primaries = art.get("primary_stocks", [])
        sectors   = art.get("sectors",        [])
        event     = art.get("event_type",     "General")

        keys: List[str] = []
        for s in primaries[:1]:
            keys.append(f"{s}::{event}")
        for sec in sectors[:1]:
            keys.append(f"SECTOR:{sec}::{event}")
        if not keys:
            keys.append(f"GENERAL::{event}")

        for key in keys:
            if key not in clusters:
                clusters[key] = []
            prefix = art.get("title", "")[:60].lower()
            if not any(a.get("title", "")[:60].lower() == prefix for a in clusters[key]):
                clusters[key].append(art)

    return clusters
