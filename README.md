# Quantedge — Institutional AI Trading Dashboard

> **Modules 1–8 | Fully Integrated | Production-Grade**

A full-stack, institutional-quality algorithmic trading platform built with **FastAPI** (Python) and **Next.js 14** (TypeScript). It combines quantitative strategy selection, AI-powered news sentiment analysis, real-time signal generation, and autonomous scheduling into a single cohesive system.

---

## Table of Contents

1. [Project Structure](#project-structure)
2. [Architecture Overview](#architecture-overview)
3. [The Handshake — How Modules Connect](#the-handshake)
4. [Module Summary](#module-summary)
5. [Quick Start](#quick-start)
6. [API Reference](#api-reference)
7. [Environment Variables](#environment-variables)
8. [Technology Stack](#technology-stack)
9. [Security Design](#security-design)
10. [Data Quality Rules](#data-quality-rules)

---

## Project Structure

```
Quantedge_Complete/
├── backend/                        ← FastAPI Python application
│   ├── main.py                     ← Application entry point, scheduler boot
│   ├── requirements.txt            ← All Python dependencies
│   ├── .env.example                ← Environment variable template
│   ├── core/
│   │   ├── config.py               ← Single-source Settings (Pydantic BaseSettings)
│   │   ├── database.py             ← DB engine singleton, get_db(), get_db_context()
│   │   ├── auth.py                 ← bcrypt + OTP + JWT + brute-force limiter
│   │   └── logging_config.py       ← Centralized structured logging
│   ├── models/
│   │   ├── portfolio.py            ← Holding, HistoricalData tables
│   │   ├── backtest.py             ← StrategyPerformance table
│   │   ├── regime.py               ← MarketRegime table
│   │   ├── signals.py              ← LiveSignal, FinalSignal, SignalAgreementLog
│   │   ├── news.py                 ← NewsArticle, NewsAnalysis, SentimentOverride
│   │   ├── paper.py                ← PaperTrade, VirtualLedger, BudgetCycle, etc.
│   │   └── alerts.py               ← AlertDispatchLog
│   ├── services/                   ← "The Brain"
│   │   ├── regime_service.py       ← Nifty 50 regime detection + parquet cache
│   │   ├── quant_service.py        ← 10-year backtest + strategy selection
│   │   ├── news_service.py         ← FinBERT sentiment + BART summary + forecast
│   │   └── signal_engine.py        ← Unified handshake orchestrator
│   ├── api/routers/
│   │   ├── auth.py                 ← Login (2-step) + OTP verify + JWT
│   │   ├── dashboard.py            ← Regime, signals, research, leaderboard
│   │   └── trading.py              ← Portfolio upload, paper trading, allocation
│   ├── engine/                     ← Quantitative engine (Modules 3-4)
│   │   ├── indicators/technical.py ← 25+ NumPy/Pandas indicators
│   │   ├── regime_detector.py      ← Full regime detector implementation
│   │   ├── strategies/library.py   ← 8 strategy classes
│   │   ├── backtest_engine.py      ← Full equity-curve backtest
│   │   ├── metrics.py              ← CAGR, Sharpe, MaxDD, Sortino, etc.
│   │   └── signals/                ← Signal engine sub-components
│   ├── scheduler/                  ← APScheduler jobs (Module 8)
│   │   ├── master_scheduler.py     ← All 6 jobs in one place
│   │   ├── heartbeat.py            ← 5-min market-hours pipeline
│   │   ├── market_hours.py         ← NSE IST hours + holiday calendar
│   │   ├── alert_dispatcher.py     ← High-confidence email alerts
│   │   ├── alert_rate_limiter.py   ← 3/day cap + 60-min dedup
│   │   ├── signal_alert_email.py   ← Professional HTML email builder
│   │   └── weekly_backtest.py      ← Saturday strategy refresh
│   └── tests/                      ← Test suites (Modules 4, 5, 7, 8)
│
└── frontend/                       ← Next.js 14 TypeScript application
    ├── package.json
    ├── tailwind.config.ts
    ├── src/
    │   ├── app/
    │   │   ├── (auth)/login/       ← Two-step login page
    │   │   └── (dashboard)/        ← All protected pages
    │   │       ├── portfolio/       ← Holdings + P&L + CSV upload
    │   │       ├── signals/         ← Live signals + TradingView chart
    │   │       ├── research/        ← News feed + Executive Insight
    │   │       ├── paper-trading/   ← Paper trade management
    │   │       └── settings/        ← System config + manual controls
    │   ├── components/
    │   │   ├── charts/CandlestickChart.tsx  ← TradingView LW Charts
    │   │   ├── layout/Sidebar.tsx           ← Collapsible nav
    │   │   ├── layout/Header.tsx            ← Quick Search + regime badge
    │   │   ├── signals/SignalCard.tsx        ← Expandable signal card
    │   │   └── ui/index.tsx                 ← Design system components
    │   ├── hooks/
    │   │   ├── useData.ts          ← All SWR hooks (5-min refresh)
    │   │   └── useAuthGuard.ts     ← Client-side JWT protection
    │   ├── lib/
    │   │   ├── api.ts              ← Axios + 401 interceptor
    │   │   ├── store.ts            ← Zustand persisted auth
    │   │   └── utils.ts            ← cn(), formatters, color helpers
    │   └── types/index.ts          ← All TypeScript interfaces
```

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                   NEXT.JS FRONTEND (Port 3000)              │
│  Portfolio │ Signals │ Research │ Paper Trading │ Settings  │
│       SWR auto-refresh every 5 minutes                      │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP REST (JWT Bearer)
┌────────────────────────▼────────────────────────────────────┐
│                FASTAPI BACKEND (Port 8000)                  │
│                                                             │
│  /api/auth        /api/dashboard        /api/trading        │
│       │                │                     │             │
│  ┌────▼────┐   ┌───────▼──────┐   ┌──────────▼──────────┐  │
│  │  Auth   │   │  Dashboard   │   │      Trading        │  │
│  │  Router │   │  Router      │   │      Router         │  │
│  └────┬────┘   └───────┬──────┘   └──────────┬──────────┘  │
│       │                │                     │             │
│  ┌────▼────────────────▼─────────────────────▼──────────┐  │
│  │                   SERVICES LAYER                      │  │
│  │  RegimeService  QuantService  NewsService  SignalEngine│  │
│  └────────────────────────┬──────────────────────────────┘  │
│                           │                                 │
│  ┌────────────────────────▼──────────────────────────────┐  │
│  │              SQLite Database (WAL mode)               │  │
│  │  15 tables: holdings, regime, signals, news, paper    │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  APScheduler (6 jobs)                               │    │
│  │  heartbeat_5min │ regime │ research │ backtest │ ...│    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
         │ yfinance │ NewsAPI │ HuggingFace │ Resend │ Gmail
```

---

## The Handshake

The signal pipeline flows through 7 stages:

```
1. RegimeService
   └─ Fetches ^NSEI from parquet cache / yfinance
   └─ Computes ATR percentile, ADX, EMA-200, slope
   └─ Classifies: STRONG_TREND | SIDEWAYS | VOLATILE | BEAR

2. QuantService.get_best_strategy(ticker, regime)
   └─ STRONG_TREND → highest Sharpe in Trend/Momentum group
   └─ SIDEWAYS     → highest Win Rate in Reversion/Swing group
   └─ VOLATILE     → Reversion only if Win Rate > 65%, else CASH
   └─ BEAR         → Fundamental first, Reversion fallback

3. _generate_signals(df, strategy)
   └─ Pure vectorised NumPy/Pandas — no ML black-box
   └─ Returns +1 / -1 / 0 for BUY / SELL / HOLD

4. Validation Gates
   └─ Volume ratio ≥ 1.5×average  (else HOLD)
   └─ R:R ratio ≥ 1.5             (else HOLD)
   └─ VOLATILE regime             (BUY → CASH)

5. Agreement Factor
   └─ ≥ 3 strategies agree → +20 confidence points
   └─ ≥ 80% signals are HOLD → bias warning, -10 penalty

6. *** NEWS SENTIMENT GATE (The Handshake) ***
   └─ NewsService.analyse(ticker) → FinBERT scores
   └─ sentiment < -0.6 AND BUY  → HOLD / CAUTION: High Negative News
   └─ sentiment > +0.6 AND HOLD → WATCH: Improving Sentiment

7. FinalSignal persisted + returned as unified JSON
```

---

## Module Summary

| Module | Description | Key Files |
|--------|-------------|-----------|
| **1** | Core Architecture & Database | `core/database.py`, `models/portfolio.py` |
| **2** | Auth: bcrypt + OTP + JWT | `core/auth.py`, `api/routers/auth.py` |
| **3** | Regime Detection + 8 Strategies | `services/regime_service.py`, `engine/` |
| **4** | Regime-Aware Signal Engine | `services/signal_engine.py`, `engine/signals/` |
| **5** | News AI: FinBERT + BART + Forecast | `services/news_service.py` |
| **6** | Next.js Dashboard (Frontend) | `frontend/src/` |
| **7** | Paper Trading + Budget Allocation | `models/paper.py`, `api/routers/trading.py` |
| **8** | Scheduler + Alert Dispatcher | `scheduler/`, inline in `main.py` |

---

## Quick Start

See **`docs/INSTRUCTIONS.md`** for the complete setup walkthrough.

```bash
# Backend
cd backend
pip install -r requirements.txt
cp .env.example .env          # fill in your API keys
uvicorn backend.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
npm install
echo "NEXT_PUBLIC_API_URL=http://localhost:8000" > .env.local
npm run dev
```

Open `http://localhost:3000` → Login with `Jayank8294` / `Jayanju@9498`

---

## API Reference

All endpoints (except `/health`, `/`, `/api/auth/*`) require `Authorization: Bearer <token>`.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/auth/login` | Step 1: password → OTP dispatch |
| `POST` | `/api/auth/verify-otp` | Step 2: OTP → JWT token |
| `GET`  | `/api/auth/me` | Verify token |
| `GET`  | `/api/dashboard/` | Aggregated dashboard payload |
| `GET`  | `/api/dashboard/regime` | Latest Nifty 50 regime |
| `GET`  | `/api/dashboard/signals` | Latest signals (all holdings) |
| `POST` | `/api/dashboard/scan-now` | Trigger immediate scan |
| `GET`  | `/api/dashboard/research/{ticker}` | AI news + sentiment |
| `GET`  | `/api/dashboard/leaderboard` | Strategy Sharpe ranking |
| `POST` | `/api/trading/portfolio/upload` | Zerodha CSV upload |
| `GET`  | `/api/trading/portfolio/holdings` | All holdings |
| `POST` | `/api/trading/paper/open` | Open paper trade |
| `POST` | `/api/trading/paper/{id}/close` | Close paper trade |
| `GET`  | `/api/trading/paper/trades` | List trades (live MTM) |
| `GET`  | `/api/trading/paper/budget` | Monthly ₹15,000 budget |
| `POST` | `/api/trading/paper/allocate` | Calculate allocation |
| `GET`  | `/api/trading/backtest/run/{ticker}` | Run 10-yr backtest |
| `GET`  | `/health` | System health check |

Full interactive docs at `http://localhost:8000/docs`

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `JWT_SECRET_KEY` | ✅ | 64-char hex — `python -c "import secrets; print(secrets.token_hex(32))"` |
| `RESEND_API_KEY` | ✅ | Resend.com API key for OTP email |
| `NEWS_API_KEY` | ✅ | newsapi.org free tier (100 req/day) |
| `HF_API_KEY` | ✅ | HuggingFace Inference API for FinBERT/BART |
| `ALERT_EMAIL_FROM` | ✅ | Your Gmail address |
| `GMAIL_APP_PASSWORD` | ✅ | Gmail App Password (not your main password) |
| `OTP_SENDER_EMAIL` | ✅ | From address in Resend dashboard |
| `FRONTEND_BASE_URL` | ❌ | Default: `http://localhost:3000` |

Without `RESEND_API_KEY`: OTP is printed to server console (dev mode).
Without `GMAIL_APP_PASSWORD`: Alert emails are logged but not sent.
Without `HF_API_KEY`: Keyword-based sentiment fallback is used.

---

## Technology Stack

### Backend
- **FastAPI 0.111** — async REST framework
- **SQLAlchemy 2.0** — ORM with SQLite WAL mode
- **APScheduler 3.10** — 6 autonomous jobs
- **yfinance 0.2.40** — OHLCV data with parquet caching
- **passlib / bcrypt** — password hashing (rounds=12)
- **PyJWT** — HS256 token signing
- **ProsusAI/FinBERT** — financial sentiment (via HF API)
- **facebook/BART-large-CNN** — 3-bullet executive summaries
- **scikit-learn** — LinearRegression price forecasting
- **scipy** — ATR percentile, statistical functions
- **pandas / numpy** — vectorised indicator calculations

### Frontend
- **Next.js 14** — App Router with TypeScript
- **Tailwind CSS** — dark design system
- **TradingView Lightweight Charts** — candlestick + EMA + BB
- **SWR** — data fetching with 5-min auto-refresh
- **Zustand** — persisted auth state
- **Axios** — HTTP client with 401 interceptor
- **Sonner** — toast notifications

---

## Security Design

1. **Credentials** — `Jayank8294` / `Jayanju@9498` hashed with bcrypt (rounds=12) at startup
2. **OTP** — 6-digit, bcrypt-hashed, single-use, 5-minute TTL, delivered via Resend API
3. **JWT** — HS256, 60-minute expiry, all data routes protected by `get_current_user` dependency
4. **Brute-force** — 30-second lockout after 3 consecutive failures (per-username asyncio.Lock)
5. **Rate limiting** — Max 3 alert emails/day with 60-minute dedup per (ticker, signal) pair

---

## Data Quality Rules

| Data Available | Flag | Signal Behaviour |
|----------------|------|-----------------|
| ≥ 10 years | `SUFFICIENT` | Full signals generated |
| 5–9 years | `INSUFFICIENT DATA` | Signals generated with warning |
| < 5 years | `LOW CONFIDENCE` | `None` returned for all metrics — no fake values |
| 0 articles | Coverage flag | `"Insufficient news coverage..."` — no fake sentiment |
