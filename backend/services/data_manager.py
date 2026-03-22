"""
backend/services/data_manager.py  — v9.2 (Production-Hardened)

FIX 3: All Investing.com and Moneycontrol requests are wrapped with
        SCRAPERAPI_KEY when configured — bypasses Cloudflare bot detection
        on data-center IPs (Render, Railway, AWS).
        Falls back to cloudscraper (which rotates browser fingerprints)
        when SCRAPERAPI_KEY is not set.
        Falls back to direct requests as last resort (works locally).
FIX 6: PARQUET_CACHE_DIR is /tmp/parquet by default — always writable.

GRACEFUL DEGRADATION RULE (unchanged):
  Each source is fetched independently. On any failure:
  1. WARNING logged with exact exception
  2. Field set to DATA_UNAVAILABLE sentinel string
  3. All other sources continue
  4. source_flags records delivery status per-source

INCEPTION DATE LOGIC (preserved):
  years >= 10 → SUFFICIENT
  2 <= years < 10 → INSUFFICIENT — banner: "Backtesting from inception [Date] only"
  years < 2  → LOW_CONFIDENCE  — banner + LOW CONFIDENCE label
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import requests
import yfinance as yf
from bs4 import BeautifulSoup

from backend.core.config import get_settings

logger = logging.getLogger(__name__)
cfg    = get_settings()

DATA_UNAVAILABLE: str = "DATA_UNAVAILABLE"
NSE_SUFFIX        = ".NS"
REQUEST_TIMEOUT   = 15

# ── Browser-grade User-Agent pool ────────────────────────────────────────────
_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
]
_ua_index = 0


def _next_ua() -> str:
    global _ua_index
    ua = _UA_POOL[_ua_index % len(_UA_POOL)]
    _ua_index += 1
    return ua


def _base_headers() -> dict:
    return {
        "User-Agent":      _next_ua(),
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection":      "keep-alive",
        "Cache-Control":   "no-cache",
    }


def _get_scraper_session():
    """
    FIX 3: Returns a requests Session configured for cloud deployment.

    Priority:
      1. ScraperAPI proxy (if SCRAPERAPI_KEY set) — residential IPs, bypasses CF
      2. cloudscraper session (rotates browser fingerprints)
      3. Plain requests session with rotating UA (works locally, may fail on DC IPs)
    """
    if cfg.SCRAPERAPI_KEY:
        # ScraperAPI proxy session — wraps every request through residential IPs
        session = requests.Session()
        session.headers.update(_base_headers())
        # ScraperAPI proxy URL (used as HTTP/HTTPS proxy)
        proxy_url = f"http://scraperapi:{cfg.SCRAPERAPI_KEY}@proxy-server.scraperapi.com:8001"
        session.proxies = {"http": proxy_url, "https": proxy_url}
        logger.debug("Scraper session: ScraperAPI proxy (residential IP)")
        return session, False

    try:
        import cloudscraper  # type: ignore
        session = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False},
            delay=3,
        )
        logger.debug("Scraper session: cloudscraper (browser fingerprint rotation)")
        return session, False
    except ImportError:
        logger.warning(
            "cloudscraper not installed. "
            "Install with: pip install cloudscraper  "
            "Using plain requests — may fail on cloud IPs without SCRAPERAPI_KEY."
        )

    session = requests.Session()
    session.headers.update(_base_headers())
    logger.debug("Scraper session: plain requests (local dev only)")
    return session, False


def _scrape_url(url: str, label: str) -> Optional[str]:
    """
    Fetch a URL using the best available scraper session.
    Returns HTML string or None on failure. Never raises.
    """
    # If ScraperAPI key set, use the ScraperAPI GET endpoint directly
    if cfg.SCRAPERAPI_KEY:
        api_url = "https://api.scraperapi.com/"
        try:
            resp = requests.get(
                api_url,
                params={
                    "api_key":   cfg.SCRAPERAPI_KEY,
                    "url":       url,
                    "render":    "false",
                    "country_code": "in",  # Indian residential IPs for MC/Investing
                },
                timeout=REQUEST_TIMEOUT + 10,  # ScraperAPI adds latency
            )
            if resp.status_code == 200:
                logger.debug("ScraperAPI: %s fetched (%d bytes)", label, len(resp.text))
                return resp.text
            logger.warning("ScraperAPI HTTP %d for %s", resp.status_code, label)
        except Exception as exc:
            logger.warning("ScraperAPI request failed for %s: %s", label, exc)
        return None

    # cloudscraper / plain requests fallback
    session, _ = _get_scraper_session()
    try:
        session.headers.update({"User-Agent": _next_ua()})
        resp = session.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.text
    except requests.exceptions.ConnectionError as exc:
        logger.warning("[Scraper] Connection error for %s: %s", label, exc)
    except requests.exceptions.Timeout:
        logger.warning("[Scraper] Timeout for %s", label)
    except requests.exceptions.HTTPError as exc:
        logger.warning("[Scraper] HTTP %s for %s", exc.response.status_code, label)
    except Exception as exc:
        logger.warning("[Scraper] Unexpected error for %s: %s", label, exc)
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# RESULT DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class OHLCVResult:
    symbol:          str
    df:              Optional[pd.DataFrame]
    inception_date:  Optional[datetime]
    years_available: float
    quality:         str             # SUFFICIENT | INSUFFICIENT | LOW_CONFIDENCE
    quality_message: str
    is_inception:    bool
    source_flags:    dict = field(default_factory=dict)

    @property
    def ui_banner(self) -> Optional[str]:
        """Return the mandatory UI banner text, or None for SUFFICIENT tickers."""
        if self.quality == "SUFFICIENT":
            return None
        return self.quality_message


@dataclass
class MacroContext:
    us_10y_yield: Any
    dxy_index:    Any
    brent_crude:  Any
    fetched_at:   datetime = field(default_factory=datetime.utcnow)
    source_flags: dict     = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "us_10y_yield": self.us_10y_yield,
            "dxy_index":    self.dxy_index,
            "brent_crude":  self.brent_crude,
            "fetched_at":   self.fetched_at.isoformat(),
            "source_flags": self.source_flags,
        }


@dataclass
class NewsContext:
    headlines:    Any
    rbi_updates:  Any
    market_mood:  Any
    fetched_at:   datetime = field(default_factory=datetime.utcnow)
    source_flags: dict     = field(default_factory=dict)


@dataclass
class TVConsensus:
    summary:         Any
    oscillators:     Any
    moving_averages: Any
    source_flags:    dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE A — Yahoo Finance + Inception Date Logic
# ═══════════════════════════════════════════════════════════════════════════════

def _parquet_path(symbol: str) -> Path:
    p = Path(cfg.PARQUET_CACHE_DIR)   # FIX 6: /tmp/parquet by default
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{symbol}_ohlcv.parquet"


def _cache_fresh(path: Path, max_hours: int = 20) -> bool:
    if not path.exists():
        return False
    return (time.time() - path.stat().st_mtime) < max_hours * 3600


def _assess_quality(df: pd.DataFrame, symbol: str) -> tuple[str, float, str, bool, Optional[datetime]]:
    """
    10-Year Inception Rule — STRICT, no fake data.

    years >= MIN_YEARS_SUFFICIENT (10)  → SUFFICIENT
    MIN_YEARS_CONFIDENCE (2) <= years < 10  → INSUFFICIENT
        UI banner: "Backtesting performed from inception [Date] only;
                    10-year historical data unavailable for {symbol}."
    years < MIN_YEARS_CONFIDENCE (2)  → LOW_CONFIDENCE
        Same banner + "LOW CONFIDENCE — only X months of history."
    """
    if df is None or df.empty:
        return ("LOW_CONFIDENCE", 0.0,
                f"No data available for {symbol}.", False, None)

    first_ts     = pd.Timestamp(df.index[0])
    last_ts      = pd.Timestamp(df.index[-1])
    inception_dt = first_ts.to_pydatetime().replace(tzinfo=None)
    days         = (last_ts - first_ts).days
    years        = days / 365.25

    ten_yr_cutoff = pd.Timestamp.now() - pd.DateOffset(years=cfg.MIN_YEARS_SUFFICIENT)
    is_inception  = first_ts > ten_yr_cutoff

    if years >= cfg.MIN_YEARS_SUFFICIENT:
        return ("SUFFICIENT", years,
                f"{years:.1f} years of data — full 10-year analysis available.",
                False, inception_dt)

    inception_str = inception_dt.strftime("%d %b %Y")

    if years >= cfg.MIN_YEARS_CONFIDENCE:
        return (
            "INSUFFICIENT", years,
            (f"Backtesting performed from inception [{inception_str}] only; "
             f"10-year historical data unavailable for {symbol}. "
             f"({years:.1f} years available)"),
            True, inception_dt,
        )

    months = int(years * 12)
    return (
        "LOW_CONFIDENCE", years,
        (f"Backtesting performed from inception [{inception_str}] only; "
         f"10-year historical data unavailable for {symbol}. "
         f"LOW CONFIDENCE — only {months} months of history (minimum: {cfg.MIN_YEARS_CONFIDENCE * 12} months)."),
        True, inception_dt,
    )


def fetch_ohlcv(symbol: str, force_refresh: bool = False) -> OHLCVResult:
    """
    Source A: Yahoo Finance OHLCV with parquet cache and inception-date logic.
    NEVER raises — returns LOW_CONFIDENCE with df=None on total failure.
    """
    path = _parquet_path(symbol)

    if not force_refresh and _cache_fresh(path, max_hours=20):
        try:
            df = pd.read_parquet(path)
            qual, years, msg, is_inc, inc_dt = _assess_quality(df, symbol)
            logger.debug("Parquet cache HIT: %s (%d bars, %.1f yrs)", symbol, len(df), years)
            return OHLCVResult(
                symbol=symbol, df=df, inception_date=inc_dt, years_available=years,
                quality=qual, quality_message=msg, is_inception=is_inc,
                source_flags={"yahoo_finance": "CACHE_HIT"},
            )
        except Exception as exc:
            logger.warning("Parquet read failed for %s (re-fetching): %s", symbol, exc)

    yf_sym = f"{symbol}{NSE_SUFFIX}"
    try:
        raw = yf.Ticker(yf_sym).history(period="max", interval="1d", auto_adjust=True)
        if raw.empty:
            raise ValueError("yfinance returned empty DataFrame")
        df = raw[["Open","High","Low","Close","Volume"]].rename(columns=str.title)
        df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
        df = df.dropna()
        df.to_parquet(path, index=True)
        qual, years, msg, is_inc, inc_dt = _assess_quality(df, symbol)
        logger.info("yfinance: %s — %d bars, %.1f yrs, quality=%s", symbol, len(df), years, qual)
        return OHLCVResult(
            symbol=symbol, df=df, inception_date=inc_dt, years_available=years,
            quality=qual, quality_message=msg, is_inception=is_inc,
            source_flags={"yahoo_finance": "LIVE"},
        )
    except Exception as exc:
        logger.error("[Source A] yfinance failed for %s: %s", symbol, exc)
        return OHLCVResult(
            symbol=symbol, df=None, inception_date=None, years_available=0.0,
            quality="LOW_CONFIDENCE",
            quality_message=f"No OHLCV data for {symbol} — fetch failed.",
            is_inception=False,
            source_flags={"yahoo_finance": DATA_UNAVAILABLE},
        )


def fetch_live_price(symbol: str) -> Any:
    """Return float or DATA_UNAVAILABLE. Never raises."""
    try:
        info  = yf.Ticker(f"{symbol}{NSE_SUFFIX}").fast_info
        price = float(info.get("last_price") or info.get("previous_close") or 0)
        return price if price > 0 else DATA_UNAVAILABLE
    except Exception as exc:
        logger.warning("[Source A] Live price failed for %s: %s", symbol, exc)
        return DATA_UNAVAILABLE


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE B — Investing.com (FIX 3: ScraperAPI / cloudscraper)
# ═══════════════════════════════════════════════════════════════════════════════

_INVESTING_URLS = {
    "us_10y_yield": "https://www.investing.com/rates-bonds/u.s.-10-year-bond-yield",
    "dxy_index":    "https://www.investing.com/indices/usdollar",
    "brent_crude":  "https://www.investing.com/commodities/brent-oil",
}


def _parse_investing_price(html: str, label: str) -> Any:
    """Extract price from Investing.com HTML. Returns float or DATA_UNAVAILABLE."""
    soup = BeautifulSoup(html, "html.parser")
    # Primary: data-test attribute (most reliable across page versions)
    for attr_val in ["instrument-price-last", "last-price-value"]:
        tag = soup.find(attrs={"data-test": attr_val})
        if tag:
            raw = tag.get_text(strip=True).replace(",", "")
            try:
                return float(raw)
            except ValueError:
                pass
    # Fallback 1: span id="last_last"
    tag = soup.find("span", {"id": "last_last"})
    if tag:
        raw = tag.get_text(strip=True).replace(",", "")
        try:
            return float(raw)
        except ValueError:
            pass
    # Fallback 2: regex scan for price patterns
    price_re = re.compile(r'(?:class|data-field)[^>]*>[^\d]*(\d{1,6}\.\d{1,4})')
    for m in price_re.finditer(html[:20000]):
        try:
            val = float(m.group(1))
            if 0 < val < 500:
                return val
        except ValueError:
            pass
    logger.warning("[Source B] Could not parse price for %s", label)
    return DATA_UNAVAILABLE


def _scrape_investing_item(key: str, url: str) -> Any:
    """Fetch one macro item from Investing.com. Returns float or DATA_UNAVAILABLE."""
    html = _scrape_url(url, f"investing.com/{key}")
    if html is None:
        return DATA_UNAVAILABLE
    return _parse_investing_price(html, key)


def fetch_macro_context() -> MacroContext:
    """
    Fetch DXY, US 10Y Yield, Brent Crude from Investing.com.
    FIX 3: Uses ScraperAPI proxy or cloudscraper to bypass Cloudflare.
    Each item fetched independently — one failure never blocks others.
    """
    results = {}
    flags   = {}
    for key, url in _INVESTING_URLS.items():
        val          = _scrape_investing_item(key, url)
        results[key] = val
        flags[key]   = "LIVE" if val != DATA_UNAVAILABLE else DATA_UNAVAILABLE
        if val != DATA_UNAVAILABLE:
            logger.info("[Source B] %s = %.4f", key, val)
        else:
            logger.warning("[Source B] %s = DATA_UNAVAILABLE", key)
        time.sleep(1.5)  # polite delay between Investing.com requests

    return MacroContext(
        us_10y_yield = results["us_10y_yield"],
        dxy_index    = results["dxy_index"],
        brent_crude  = results["brent_crude"],
        source_flags = flags,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE C — Moneycontrol (FIX 3: ScraperAPI / cloudscraper)
# ═══════════════════════════════════════════════════════════════════════════════

_MC_URLS = {
    "markets": "https://www.moneycontrol.com/news/business/markets/",
    "rbi":     "https://www.moneycontrol.com/news/tags/rbi.html",
}

_NEGATIVE_KW = frozenset({
    "crash", "fall", "drop", "plunge", "decline", "sell-off", "selloff",
    "recession", "default", "downgrade", "ban", "penalty", "fraud",
    "crisis", "collapse", "bearish", "warning", "hike", "rate hike",
    "rbi hike", "inflation", "stagflation",
})
_POSITIVE_KW = frozenset({
    "rally", "surge", "gain", "record", "bullish", "growth",
    "profit", "beat", "upgrade", "inflow", "gdp growth",
    "rate cut", "stimulus", "recovery", "positive",
})


def _mc_mood(headlines: list[str]) -> str:
    if not headlines:
        return DATA_UNAVAILABLE
    pos = neg = 0
    for h in headlines:
        lh = h.lower()
        pos += sum(1 for kw in _POSITIVE_KW if kw in lh)
        neg += sum(1 for kw in _NEGATIVE_KW if kw in lh)
    if neg >= pos * 1.5:
        return "BEARISH"
    if pos >= neg * 1.5:
        return "BULLISH"
    return "NEUTRAL"


def _parse_mc_headlines(html: str, max_items: int = 12) -> Any:
    """Extract headlines from Moneycontrol HTML."""
    soup      = BeautifulSoup(html, "html.parser")
    headlines = []
    for tag in (soup.find_all("h2") + soup.find_all("h3") +
                soup.find_all("a", class_=re.compile(r"title|heading", re.I))):
        txt = tag.get_text(strip=True)
        if len(txt) > 25 and txt not in headlines:
            headlines.append(txt)
        if len(headlines) >= max_items:
            break
    return headlines if headlines else DATA_UNAVAILABLE


def fetch_news_context() -> NewsContext:
    """
    Scrape Moneycontrol market news + RBI updates.
    FIX 3: Uses ScraperAPI or cloudscraper.
    Each endpoint fetched independently.
    """
    flags = {}

    # Markets headlines
    mkt_html = _scrape_url(_MC_URLS["markets"], "MC_Markets")
    market_headlines = _parse_mc_headlines(mkt_html, 12) if mkt_html else DATA_UNAVAILABLE
    flags["mc_markets"] = "LIVE" if market_headlines != DATA_UNAVAILABLE else DATA_UNAVAILABLE

    time.sleep(1.5)

    # RBI updates
    rbi_html    = _scrape_url(_MC_URLS["rbi"], "MC_RBI")
    rbi_updates = _parse_mc_headlines(rbi_html, 8) if rbi_html else DATA_UNAVAILABLE
    flags["mc_rbi"] = "LIVE" if rbi_updates != DATA_UNAVAILABLE else DATA_UNAVAILABLE

    mood = _mc_mood(market_headlines) if isinstance(market_headlines, list) else DATA_UNAVAILABLE
    logger.info("[Source C] mood=%s | headlines=%s | rbi=%s",
                mood,
                len(market_headlines) if isinstance(market_headlines, list) else "N/A",
                len(rbi_updates)      if isinstance(rbi_updates, list)      else "N/A")

    return NewsContext(
        headlines    = market_headlines,
        rbi_updates  = rbi_updates,
        market_mood  = mood,
        source_flags = flags,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE D — TradingView-TA
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_tv_consensus(symbol: str, exchange: str = "NSE") -> TVConsensus:
    """
    Fetch TradingView technical analysis via tradingview-ta.
    Returns DATA_UNAVAILABLE on ImportError or any failure.
    """
    try:
        from tradingview_ta import TA_Handler, Interval  # type: ignore
        handler  = TA_Handler(
            symbol   = symbol,
            exchange = exchange,
            screener = "india",
            interval = Interval.INTERVAL_1_DAY,
            timeout  = 10,
        )
        analysis = handler.get_analysis()
        rec      = analysis.summary.get("RECOMMENDATION", DATA_UNAVAILABLE)
        logger.info("[Source D] TradingView %s: %s", symbol, rec)
        return TVConsensus(
            summary         = rec,
            oscillators     = analysis.oscillators  or {},
            moving_averages = analysis.moving_avgs  or {},
            source_flags    = {"tradingview_ta": "LIVE"},
        )
    except ImportError:
        logger.warning("[Source D] tradingview-ta not installed. Install: pip install tradingview-ta")
        return TVConsensus(
            summary=DATA_UNAVAILABLE, oscillators=DATA_UNAVAILABLE,
            moving_averages=DATA_UNAVAILABLE,
            source_flags={"tradingview_ta": "NOT_INSTALLED"},
        )
    except Exception as exc:
        logger.warning("[Source D] TradingView-TA failed for %s: %s", symbol, exc)
        return TVConsensus(
            summary=DATA_UNAVAILABLE, oscillators=DATA_UNAVAILABLE,
            moving_averages=DATA_UNAVAILABLE,
            source_flags={"tradingview_ta": DATA_UNAVAILABLE},
        )


# ═══════════════════════════════════════════════════════════════════════════════
# DataManager — Unified hub
# ═══════════════════════════════════════════════════════════════════════════════

class DataManager:
    """
    Fail-safe ingestion hub. Each source runs independently.
    Partial data always returned — never a total failure.
    """

    def fetch_all(self, ticker: str) -> dict:
        logger.info("DataManager.fetch_all(%s)", ticker)
        ohlcv = fetch_ohlcv(ticker)
        macro = fetch_macro_context()
        news  = fetch_news_context()
        tv    = fetch_tv_consensus(ticker)

        all_flags = {
            **{f"A_{k}": v for k, v in ohlcv.source_flags.items()},
            **{f"B_{k}": v for k, v in macro.source_flags.items()},
            **{f"C_{k}": v for k, v in news.source_flags.items()},
            **{f"D_{k}": v for k, v in tv.source_flags.items()},
        }
        avail = sum(1 for v in all_flags.values() if v != DATA_UNAVAILABLE)
        logger.info("DataManager: %s — %d/%d sources live | quality=%s",
                    ticker, avail, len(all_flags), ohlcv.quality)

        return {
            "ohlcv":         ohlcv,
            "macro":         macro,
            "news":          news,
            "tv":            tv,
            "summary_flags": all_flags,
        }

    def fetch_macro_only(self) -> MacroContext:
        return fetch_macro_context()

    def fetch_news_only(self) -> NewsContext:
        return fetch_news_context()

    def batch_ohlcv(self, symbols: list[str]) -> dict[str, OHLCVResult]:
        return {sym: fetch_ohlcv(sym) for sym in symbols}


_data_manager: Optional[DataManager] = None


def get_data_manager() -> DataManager:
    global _data_manager
    if _data_manager is None:
        _data_manager = DataManager()
    return _data_manager
