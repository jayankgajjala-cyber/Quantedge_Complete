# Quantedge — AI-Powered Trading Dashboard

A production-ready FastAPI backend for systematic equity trading on Indian markets (NSE/BSE).  
Features include multi-strategy backtesting, regime detection, FinBERT news sentiment, paper trading, APScheduler automation, and a Next.js frontend.

---

## Project Overview

| Layer | Technology |
|---|---|
| API | FastAPI + Uvicorn |
| Database | PostgreSQL (Supabase/Neon) · SQLite for local dev |
| Auth | JWT (PyJWT) + bcrypt OTP via email |
| Data | yfinance · TradingView-TA · BeautifulSoup4 |
| Sentiment | HuggingFace FinBERT API (keyword fallback) |
| Scheduling | APScheduler 3.x (multi-worker DB lock) |
| Frontend | Next.js 14 · Tailwind CSS |

---

## Installation

### Prerequisites

- Python 3.11+
- Node.js 18+ (for frontend)
- A PostgreSQL database (Supabase free tier recommended) **or** SQLite for local dev

### Backend setup

```bash
# 1. Clone and enter the project
git clone <your-repo-url>
cd Quantedge_Complete

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy and fill in environment variables
cp backend/.env.example backend/.env
# Edit backend/.env — set DATABASE_URL, JWT_SECRET, etc.

# 5. Initialise the database
cd backend
python -c "from core.database import init_db; init_db()"

# 6. Start the development server
uvicorn backend.main:app --reload --port 8000
```

### Frontend setup

```bash
cd frontend
npm install
cp .env.local.example .env.local
# Set NEXT_PUBLIC_API_URL=http://localhost:8000
npm run dev
```

---

## Environment Variables

Create `backend/.env` (or set these in your hosting platform):

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | **Yes** | PostgreSQL connection string — `postgresql://user:pass@host:5432/db` |
| `JWT_SECRET` | **Yes** | Random 32+ char secret for JWT signing |
| `OTP_EMAIL_FROM` | Yes (prod) | Sender email address for OTP dispatch |
| `RESEND_API_KEY` | Yes (prod) | Resend.com API key for transactional email |
| `HF_API_KEY` | Optional | HuggingFace API key for FinBERT/BART sentiment |
| `NEWSAPI_KEY` | Optional | NewsAPI.org key for news fetching |
| `ADMIN_USERNAME` | **Yes** | Initial admin login username |
| `ADMIN_PASSWORD_HASH` | **Yes** | bcrypt hash of admin password |
| `ALLOWED_ORIGINS` | **Yes** | Comma-separated CORS origins (e.g. `https://yourdomain.com`) |
| `DEBUG` | No | `true` enables SQLAlchemy echo and verbose logging |
| `DB_PATH` | No | SQLite path for local dev (ignored when `DATABASE_URL` is set) |

---

## Deployment — Free Tier (Render or Railway)

### Render

1. Connect your GitHub repository at [render.com](https://render.com).
2. Create a **Web Service** with the following settings:

   | Setting | Value |
   |---|---|
   | Environment | Python 3 |
   | Build Command | `pip install -r requirements.txt` |
   | Start Command | `uvicorn backend.main:app --host 0.0.0.0 --port $PORT` |

3. Add all environment variables from the table above under **Environment → Secret Files or Env Vars**.
4. Attach a **Render PostgreSQL** free instance and copy the `Internal Database URL` into `DATABASE_URL`.
5. Deploy. Render streams logs at the dashboard.

### Railway

1. Push your code to GitHub, then import the repo at [railway.app](https://railway.app).
2. Add a **PostgreSQL** plugin — Railway auto-injects `DATABASE_URL`.
3. Set all remaining environment variables under **Variables**.
4. Railway reads `railway.toml` (already included) for the start command.
5. Deploy via **Deploy Now**.

### Supabase Database (recommended for both platforms)

1. Create a free project at [supabase.com](https://supabase.com).
2. Go to **Settings → Database → Connection string → URI**.
3. Copy the URI and set it as `DATABASE_URL` (replace `postgres://` with `postgresql://` — the app does this automatically, but some shells need it explicit).
4. The app uses `NullPool` for pgbouncer compatibility — no extra configuration needed.

---

## Running Tests

```bash
# From the project root
pytest backend/tests/ -v --tb=short

# Run a single test module
pytest backend/tests/test_module5.py -v
```

---

## Project Structure

```
Quantedge_Complete/
├── backend/
│   ├── main.py                  # FastAPI app, lifespan, routers
│   ├── core/
│   │   ├── database.py          # Engine, SessionLocal, init_db
│   │   ├── auth.py              # JWT, OTP, bcrypt helpers
│   │   └── config.py            # Pydantic settings
│   ├── models/                  # SQLAlchemy ORM models
│   ├── api/routers/             # FastAPI route handlers
│   ├── engine/                  # Backtest engine, indicators, strategies
│   ├── services/                # Data manager, news service, signal engine
│   ├── scheduler/               # APScheduler jobs, heartbeat, alerts
│   └── tests/                   # pytest test suite
├── frontend/                    # Next.js 14 app
├── requirements.txt             # Python dependencies
├── Dockerfile
├── render.yaml
└── railway.toml
```

---

## License

Private — all rights reserved.
