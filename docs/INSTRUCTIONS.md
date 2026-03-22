# Quantedge — Complete Setup & Deployment Instructions

This document covers everything from a fresh machine to a running system.

---

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | ≥ 3.11 | https://python.org |
| Node.js | ≥ 18.17 | https://nodejs.org |
| npm | ≥ 9 | Included with Node |
| Git | any | https://git-scm.com |

---

## Part 1 — Backend Setup

### Step 1: Create a virtual environment

```bash
cd Quantedge_Complete/backend
python -m venv .venv

# Activate
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate
```

### Step 2: Install Python dependencies

```bash
pip install -r requirements.txt
```

> **Note on PyArrow**: If `pyarrow` install fails, try `pip install pyarrow --pre`.
> It is only needed for the parquet OHLCV cache feature.

### Step 3: Create your .env file

```bash
cp .env.example .env
```

Open `.env` and fill in each value:

```env
# REQUIRED for full functionality:
JWT_SECRET_KEY=<run: python -c "import secrets; print(secrets.token_hex(32))">
RESEND_API_KEY=re_xxxxxxxxxxxx        # from resend.com/api-keys
OTP_SENDER_EMAIL=noreply@yourdomain.com
NEWS_API_KEY=xxxxxxxxxxxxxxxxxxxx     # from newsapi.org (free)
HF_API_KEY=hf_xxxxxxxxxxxxxxxxxxxx    # from huggingface.co/settings/tokens
ALERT_EMAIL_FROM=your@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx  # from myaccount.google.com/apppasswords

# OPTIONAL (defaults work for local dev):
DEBUG=false
FRONTEND_BASE_URL=http://localhost:3000
DB_PATH=data/db/quantedge.db
PARQUET_CACHE_DIR=data/cache/parquet
```

> **Dev mode**: If `RESEND_API_KEY` is left blank, the OTP will be printed
> to the terminal instead of emailed. This is safe for local development.

### Step 4: Create data directories

```bash
mkdir -p data/db data/cache/parquet logs
```

### Step 5: Start the backend

```bash
# Development (with auto-reload)
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

# Production
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers 1
```

> ⚠️ Use `--workers 1` with SQLite. For multi-worker setups, migrate to PostgreSQL.

### Step 6: Verify the backend

Open your browser to:
- **API docs**: http://localhost:8000/docs
- **Health check**: http://localhost:8000/health

Expected health response:
```json
{
  "status": "ok",
  "version": "9.0.0",
  "database": "ok",
  "scheduler": "running",
  "active_jobs": 5
}
```

---

## Part 2 — Frontend Setup

### Step 1: Install Node dependencies

```bash
cd Quantedge_Complete/frontend
npm install
```

> This installs ~200MB of packages. Takes 1-3 minutes on first run.

### Step 2: Create the frontend .env file

```bash
echo "NEXT_PUBLIC_API_URL=http://localhost:8000" > .env.local
```

### Step 3: Start the frontend

```bash
# Development
npm run dev

# Production build
npm run build
npm run start
```

### Step 4: Open the dashboard

Navigate to **http://localhost:3000**

You will be redirected to `/login`.

---

## Part 3 — First Login

The system uses a two-step authentication flow:

### Step 1 — Password screen
- **Username**: `Jayank8294`
- **Password**: `Jayanju@9498`
- Click **Continue**

### Step 2 — OTP screen
- If `RESEND_API_KEY` is set: check `jayankgajjala@gmail.com` for the 6-digit OTP
- If not set (dev mode): look in the terminal running the backend — you will see a log line like:
  ```
  [DEV MODE] OTP for 'Jayank8294': 482913 (email not sent)
  ```
- Enter the OTP and click **Authenticate**

You will receive a JWT token valid for 60 minutes.

---

## Part 4 — Uploading Your Portfolio

1. Go to **Portfolio** in the sidebar
2. Click **Upload Zerodha CSV**
3. Upload your Zerodha holdings export (`.csv` format)

**Required CSV columns** (case-insensitive):
- `Instrument` — NSE ticker symbol
- `Qty.` — number of shares
- `Avg. cost` — your average purchase price

The system will:
- Import all valid rows
- Validate each ticker against NSE
- Mark data quality as SUFFICIENT / INSUFFICIENT DATA / LOW CONFIDENCE based on available history

---

## Part 5 — Running Your First Signal Scan

1. Go to **Market Signals** in the sidebar
2. Click **Force Scan** button (top right)
3. The system will:
   - Detect the current Nifty 50 regime
   - Select the best strategy per holding based on 10-year backtests
   - Generate BUY / SELL / HOLD signals
   - Apply FinBERT sentiment override
   - Display results within ~30 seconds

> **Automatic scans** run every 5 minutes during NSE market hours (09:15–15:30 IST, Mon–Fri).

---

## Part 6 — Running the 10-Year Backtest

**Via Settings page:**
1. Go to **Settings** → Backtest section
2. Optionally enter comma-separated tickers (or leave blank for all holdings)
3. Click **Start Backtest**

**Via API:**
```bash
curl -H "Authorization: Bearer <token>" \
     http://localhost:8000/api/trading/backtest/run/RELIANCE
```

Backtest results are cached as `.parquet` files in `data/cache/parquet/`.
Re-runs on the same day use the cache automatically.

---

## Part 7 — Budget Allocation

The system enforces a **₹15,000/month** budget rule:

1. When a BUY signal has confidence ≥ 75%:
   - Go to **Paper Trading** → click **New Trade**
   - Or call `POST /api/trading/paper/allocate` with the ticker and confidence

2. The allocator calculates:
   - Max trade size = `min(remaining_budget, ₹15,000 × 40%)` = max ₹6,000
   - Shares = `floor(max_alloc / current_price)`
   - Commission = `0.1% × 2 sides`

3. Trades are auto-closed when:
   - Price ≤ Stop Loss → `SL_HIT`
   - Price ≥ Target → `TARGET_HIT`
   - A Gmail alert is sent for each auto-close event

---

## Part 8 — Scheduler Jobs Reference

All jobs run automatically after backend startup:

| Job | Trigger | What it does |
|-----|---------|--------------|
| `heartbeat_5min` | Every 5 min (market hours only) | Scan → alerts → SL monitor |
| `regime_detector` | Every 5 min | Update Nifty 50 regime classification |
| `research_refresh` | Every 60 min | Refresh FinBERT news cache for all holdings |
| `weekly_backtest` | Saturday 01:00 UTC | Re-run 10-yr backtest, detect strategy changes |
| `weekly_report` | Saturday 18:00 UTC | Generate P&L report, email to user |

**Manual triggers** (from Settings page or API):
```bash
# Force heartbeat
POST /api/dashboard/scan-now

# Force backtest
GET /api/trading/backtest/run/{ticker}
```

---

## Part 9 — Gmail App Password Setup

Gmail SMTP requires an App Password, not your main Gmail password:

1. Go to https://myaccount.google.com/security
2. Enable **2-Step Verification** (required)
3. Go to https://myaccount.google.com/apppasswords
4. Select app: **Mail**, device: **Other** → type "Quantedge"
5. Copy the 16-character password (format: `xxxx xxxx xxxx xxxx`)
6. Add to `.env`:
   ```env
   ALERT_EMAIL_FROM=your.gmail@gmail.com
   GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
   ```

---

## Part 10 — Production Deployment

### Backend (Linux VPS / cloud VM)

```bash
# Install as systemd service
sudo nano /etc/systemd/system/quantedge.service
```

```ini
[Unit]
Description=Quantedge Trading System
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/Quantedge_Complete
ExecStart=/home/ubuntu/Quantedge_Complete/backend/.venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers 1
Restart=always
RestartSec=10
EnvironmentFile=/home/ubuntu/Quantedge_Complete/backend/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable quantedge
sudo systemctl start quantedge
sudo systemctl status quantedge
```

### Frontend (Vercel — recommended)

```bash
# Install Vercel CLI
npm install -g vercel

cd Quantedge_Complete/frontend
vercel

# Set environment variable in Vercel dashboard:
# NEXT_PUBLIC_API_URL = https://your-backend-domain.com
```

### Nginx reverse proxy (optional)

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location /api/ {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location / {
        proxy_pass http://localhost:3000;
    }
}
```

---

## Troubleshooting

### "No regime data yet"
The regime detector runs every 5 minutes. Wait or trigger manually:
```bash
# In Python console
from backend.services.regime_service import get_regime_service
get_regime_service().detect_and_persist()
```

### "Insufficient news coverage"
Normal when `NEWS_API_KEY` is unset or free tier is exhausted (100 req/day).
The keyword sentiment fallback will activate automatically.

### "HF model loading, waiting Xs"
HuggingFace free tier cold-starts models. The system retries automatically up to 3 times.
After that, keyword-based sentiment fallback runs.

### OTP not received
1. Check terminal for `[DEV MODE] OTP for 'Jayank8294': XXXXXX`
2. Verify `RESEND_API_KEY` is set correctly
3. Check spam folder
4. OTP expires in 5 minutes — request a new one

### "Volume not confirmed" on all signals
Normal outside market hours or on low-liquidity tickers.
Signals with volume_ratio < 1.5× are automatically held.

### `ModuleNotFoundError: No module named 'backend'`
Run uvicorn from the `Quantedge_Complete` directory (not inside `backend/`):
```bash
cd Quantedge_Complete
uvicorn backend.main:app --reload
```

---

## Running Tests

```bash
cd Quantedge_Complete/backend
pip install pytest pytest-asyncio

# All test suites
pytest tests/ -v --tb=short

# Individual modules
pytest tests/test_auth.py -v
pytest tests/test_module4.py -v
pytest tests/test_module5.py -v
pytest tests/test_module7.py -v
pytest tests/test_module8.py -v
```

---

## Resetting the Database

```bash
rm -f data/db/quantedge.db
# Restart the server — tables are recreated automatically by init_db()
```

To also clear the parquet cache:
```bash
rm -rf data/cache/parquet/*.parquet
```

---

## Support

- API documentation: http://localhost:8000/docs
- Redoc: http://localhost:8000/redoc
- Health check: http://localhost:8000/health
