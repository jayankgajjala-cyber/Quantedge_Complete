"""
test_module5.py  (v2 — rewritten for backend.services.news_service)
=====================================================================
All tests now target the consolidated NewsService in
backend.services.news_service, replacing the deleted
services.research.* subpackage.

Run:
    pytest backend/tests/test_module5.py -v --tb=short
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

from backend.services.news_service import (
    Article,
    _keyword_score,
    score_sentiment,
    detect_conflict,
    generate_summary,
    get_news_service,
)


# ─── helpers ──────────────────────────────────────────────────────────────────

def _make_article(ticker="TCS", url_suffix="1", title="Title", published_at=None):
    return Article(
        ticker=ticker,
        source="NEWSAPI",
        title=title,
        description="Some description.",
        url=f"https://example.com/{url_suffix}",
        published_at=published_at or datetime.now(timezone.utc),
    )


def _make_scored(scores: list) -> list:
    results = []
    for i, s in enumerate(scores):
        art   = _make_article(ticker="TEST", url_suffix=str(i), title=f"Title {i}")
        label = "POSITIVE" if s > 0 else ("NEGATIVE" if s < 0 else "NEUTRAL")
        results.append((art, label, s))
    return results


# ─── 1. Article & Deduplication ───────────────────────────────────────────────

class TestArticle:

    def test_url_hash_generated(self):
        art = _make_article()
        assert len(art.url_hash) == 64
        assert art.url_hash.isalnum()

    def test_deduplication_removes_same_url(self):
        now = datetime.now(timezone.utc)
        a1  = Article("TCS", "NEWSAPI", "Title 1",      "Desc", "https://ex.com/1", now)
        a2  = Article("TCS", "GOOGLE",  "Title 1 copy", "Desc", "https://ex.com/1", now)
        a3  = Article("TCS", "NEWSAPI", "Title 2",      "Desc", "https://ex.com/2", now)

        seen, unique = set(), []
        for art in [a1, a2, a3]:
            if art.url_hash not in seen:
                seen.add(art.url_hash)
                unique.append(art)
        assert len(unique) == 2  # a2 is a duplicate of a1

    def test_latest_first_sort(self):
        now      = datetime.now(timezone.utc)
        old      = now - timedelta(hours=5)
        very_old = now - timedelta(days=2)

        articles = [
            _make_article(url_suffix="1", title="Old news", published_at=old),
            _make_article(url_suffix="2", title="Very old", published_at=very_old),
            _make_article(url_suffix="3", title="Latest",   published_at=now),
        ]
        articles.sort(key=lambda a: a.published_at, reverse=True)
        assert articles[0].title  == "Latest"
        assert articles[-1].title == "Very old"

    def test_naive_datetime_gets_utc_tzinfo(self):
        naive_dt = datetime(2024, 1, 15, 9, 30)
        art = Article("INFY", "NEWSAPI", "Test", "", "https://ex.com/naive", naive_dt)
        assert art.published_at.tzinfo is not None
        assert art.published_at.tzinfo == timezone.utc


# ─── 2. Keyword Sentiment Fallback ───────────────────────────────────────────

class TestKeywordSentiment:

    def test_positive_keywords_return_positive(self):
        label, score = _keyword_score("Record profit and strong revenue growth")
        assert label == "POSITIVE"
        assert 0.0 < score <= 1.0

    def test_negative_keywords_return_negative(self):
        label, score = _keyword_score("Company reports fraud and accounting loss")
        assert label == "NEGATIVE"
        assert -1.0 <= score < 0.0

    def test_neutral_text_returns_neutral(self):
        label, score = _keyword_score("The company held its annual general meeting today")
        assert label == "NEUTRAL"
        assert score == 0.0

    def test_empty_text_returns_neutral(self):
        with patch("backend.services.news_service._hf_request", return_value=None):
            label, score = score_sentiment("")
        assert label == "NEUTRAL"
        assert score == 0.0

    def test_score_in_valid_range(self):
        for text in [
            "Massive loss and debt default crisis",
            "Record earnings and dividend growth",
            "Company merged with competitor today",
        ]:
            _, score = _keyword_score(text)
            assert -1.0 <= score <= 1.0, f"Score out of range for: {text}"


# ─── 3. FinBERT API Integration ───────────────────────────────────────────────

class TestFinBERT:

    def test_finbert_positive_response_parsed(self):
        mock_response = [[
            {"label": "positive", "score": 0.92},
            {"label": "negative", "score": 0.05},
            {"label": "neutral",  "score": 0.03},
        ]]
        with patch("backend.services.news_service._hf_request", return_value=mock_response):
            label, score = score_sentiment("Company posts record profits")
        assert label == "POSITIVE"
        assert score == pytest.approx(0.92, abs=0.01)

    def test_finbert_negative_response_gives_negative_score(self):
        mock_response = [[
            {"label": "negative", "score": 0.87},
            {"label": "neutral",  "score": 0.10},
            {"label": "positive", "score": 0.03},
        ]]
        with patch("backend.services.news_service._hf_request", return_value=mock_response):
            label, score = score_sentiment("Massive accounting fraud uncovered")
        assert label == "NEGATIVE"
        assert score == pytest.approx(-0.87, abs=0.01)

    def test_hf_unavailable_uses_fallback(self):
        with patch("backend.services.news_service._hf_request", return_value=None):
            label, score = score_sentiment("Strong profit growth and revenue surge")
        assert label in ("POSITIVE", "NEGATIVE", "NEUTRAL")
        assert -1.0 <= score <= 1.0


# ─── 4. Conflict Detection ────────────────────────────────────────────────────

class TestConflictDetection:

    def test_conflict_detected_when_std_over_threshold(self):
        scored = _make_scored([1.0, -1.0, 1.0, -1.0, 0.9, -0.9])
        conflict, detail = detect_conflict(scored, "SBIN")
        assert conflict is True
        assert "SBIN" in detail

    def test_no_conflict_uniform_sentiment(self):
        scored = _make_scored([0.8, 0.75, 0.82, 0.78])
        conflict, detail = detect_conflict(scored, "TCS")
        assert conflict is False

    def test_no_conflict_with_fewer_than_3_articles(self):
        scored = _make_scored([0.9, -0.9])
        conflict, _ = detect_conflict(scored, "TCS")
        assert conflict is False

    def test_conflict_detail_contains_news_conflict_marker(self):
        scored = _make_scored([1.0, -1.0, 1.0, -1.0])
        conflict, detail = detect_conflict(scored, "WIPRO")
        if conflict:
            assert "[NEWS CONFLICT DETECTED]" in detail


# ─── 5. Executive Summary ─────────────────────────────────────────────────────

class TestExecutiveSummary:

    def _make_articles(self, n=5):
        return [
            Article(
                "RELIANCE", "NEWSAPI",
                f"Reliance posts {10+i}% revenue growth in Q{i+1}",
                f"Strong operational performance in Q{i+1}.",
                f"https://ex.com/{i}",
                datetime.now(timezone.utc),
            )
            for i in range(n)
        ]

    def test_bart_response_produces_3_bullets(self):
        mock_bart = [{"summary_text": "Revenue grew strongly. Margins improved. Outlook positive."}]
        articles  = self._make_articles(5)
        with patch("backend.services.news_service._hf_request", return_value=mock_bart):
            bullets = generate_summary(articles, "RELIANCE")
        assert len(bullets) == 3
        for b in bullets:
            assert b.startswith("•")

    def test_fallback_when_bart_unavailable_still_3_bullets(self):
        articles = self._make_articles(5)
        with patch("backend.services.news_service._hf_request", return_value=None):
            bullets = generate_summary(articles, "RELIANCE")
        assert len(bullets) == 3

    def test_no_articles_returns_insufficient_message(self):
        bullets = generate_summary([], "NODATA")
        assert len(bullets) == 1
        assert "Insufficient" in bullets[0]


# ─── 6. Sentiment Override Logic ─────────────────────────────────────────────

class TestSentimentOverride:

    def _mock_analysis(self, score, label, conflict=False):
        row = MagicMock()
        row.avg_sentiment_score = score
        row.sentiment_label     = MagicMock(value=label)
        row.conflict_detected   = conflict
        row.conflict_detail     = "[NEWS CONFLICT DETECTED]" if conflict else None
        row.executive_summary   = json.dumps(["• Bullet 1", "• Bullet 2", "• Bullet 3"])
        row.forecast_outlook    = "Bullish — 15% Revenue CAGR"
        row.forecast_direction  = "BULLISH"
        row.coverage_message    = None
        return row

    def test_buy_demoted_on_high_negative_sentiment(self):
        svc      = get_news_service()
        analysis = self._mock_analysis(-0.75, "NEGATIVE")
        db       = MagicMock()
        signal   = {"signal": "BUY", "confidence": 82.0, "regime": "STRONG_TREND",
                    "selected_strategy": "Trend_EMA_Cross"}
        result   = svc.apply_sentiment_override("SBIN", signal, analysis, db)
        assert "HOLD" in result["signal"] or "CAUTION" in result["signal"]
        assert result["confidence"] < 82.0
        assert result["sentiment_override"] is True

    def test_hold_upgraded_on_high_positive_sentiment(self):
        svc      = get_news_service()
        analysis = self._mock_analysis(0.75, "POSITIVE")
        db       = MagicMock()
        signal   = {"signal": "HOLD", "confidence": 55.0, "regime": "SIDEWAYS",
                    "selected_strategy": "Bollinger_Reversion"}
        result   = svc.apply_sentiment_override("TCS", signal, analysis, db)
        assert "WATCH" in result["signal"]
        assert result["confidence"] > 55.0
        assert result["sentiment_override"] is True

    def test_no_override_on_neutral_sentiment(self):
        svc      = get_news_service()
        analysis = self._mock_analysis(0.10, "NEUTRAL")
        db       = MagicMock()
        signal   = {"signal": "BUY", "confidence": 70.0, "regime": "STRONG_TREND",
                    "selected_strategy": "Trend_EMA_Cross"}
        result   = svc.apply_sentiment_override("INFY", signal, analysis, db)
        assert result["signal"]             == "BUY"
        assert result["sentiment_override"] is False

    def test_conflict_warning_passed_through(self):
        svc      = get_news_service()
        analysis = self._mock_analysis(-0.2, "NEUTRAL", conflict=True)
        db       = MagicMock()
        signal   = {"signal": "HOLD", "confidence": 60.0, "regime": "SIDEWAYS",
                    "selected_strategy": "Mean_Reversion_ZScore"}
        result   = svc.apply_sentiment_override("WIPRO", signal, analysis, db)
        assert result["conflict_warning"] is not None
