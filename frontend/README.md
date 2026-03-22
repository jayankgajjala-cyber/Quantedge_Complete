# Quantedge — Frontend Dashboard

Production-grade Next.js 14 trading dashboard with institutional-quality design.

## Stack

- **Next.js 14** (App Router) + **TypeScript**
- **Tailwind CSS** + custom dark theme
- **SWR** — auto-refetching every 5 minutes (matches backend scan cadence)
- **Zustand** — persisted auth state
- **TradingView Lightweight Charts** — candlestick + EMA 50/200 + Bollinger Bands
- **Sonner** — toast notifications
- **Lucide React** — icons

## Setup

```bash
cd trading_frontend
npm install

# Create .env.local
echo "NEXT_PUBLIC_API_URL=http://localhost:8000" > .env.local

npm run dev   # http://localhost:3000
```

## Pages

| Route | Description |
|---|---|
| `/login` | Two-step auth: password → OTP |
| `/portfolio` | Holdings table, P&L summary, Zerodha CSV upload |
| `/signals` | Live regime-aware signals, candlestick chart panel |
| `/research` | AI news feed, Executive Insight, sentiment analysis, forecast |
| `/paper-trading` | Open/close simulated trades with P&L tracking |
| `/settings` | System info, manual controls, env vars guide |

## Features

- **Auth guard** — every dashboard route redirects to `/login` if no JWT token
- **5-min auto-refresh** — SWR `refreshInterval` matches backend scheduler
- **Collapsible sidebar** — full labels ↔ icon-only mode
- **Quick Search** — Nifty 500 ticker search in header with dropdown
- **TradingView chart** — EMA 50 (cyan), EMA 200 (gold), Bollinger Bands (purple), Volume histogram
- **Signal cards** — expandable with entry/SL/target levels, agreement factor, bias warnings
- **News feed** — latest-first, per-article FinBERT sentiment dots, conflict warnings
- **Executive Insight box** — 3-bullet BART AI summary with forecast outlook
- **Responsive** — grid layout adapts to screen width

## Authentication Flow

1. POST `/api/auth/login` with username + password
2. On success, OTP sent to `jayankgajjala@gmail.com`
3. POST `/api/auth/verify-otp` with 6-digit OTP
4. JWT stored in Zustand persist (localStorage)
5. All subsequent API calls include `Authorization: Bearer <token>`
6. 401 response → auto-redirect to `/login`

## Environment Variables

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
```
