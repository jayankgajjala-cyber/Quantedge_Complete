# Quantedge — Complete Setup Guide

> **Version 9.2** | Zero-failure cloud deployment walkthrough  
> Estimated time: 45–90 minutes for first-time setup

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [API Keys — Where to Get Them](#2-api-keys)
3. [Database Setup (Supabase)](#3-database-setup)
4. [Local Development](#4-local-development)
5. [Backend Deployment (Railway)](#5-backend-deployment-railway)
6. [Frontend Deployment (Vercel)](#6-frontend-deployment-vercel)
7. [Environment Variables Reference](#7-environment-variables-reference)
8. [Verifying Everything Works](#8-verification)
9. [Troubleshooting](#9-troubleshooting)

---

## 1. Prerequisites

Install these tools before starting:

| Tool | Version | Download |
|------|---------|----------|
| Python | ≥ 3.11 | https://python.org/downloads |
| Node.js | ≥ 18.17 | https://nodejs.org |
| Git | Any | https://git-scm.com |

Verify installs:
```bash
python --version    # Python 3.11.x
node --version      # v18.x.x
git --version       # git version 2.x.x
```

---

## 2. API Keys

You need **6 API keys** for full functionality. Each section below tells you exactly where to sign up and where to find the key.

---

### 2.1 JWT Secret Key (Self-Generated)

This is not an external API — you generate it yourself once.

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Copy the output (64-character hex string). This goes into `JWT_SECRET_KEY`.  
**Critical**: Generate it once. Changing it logs out all active sessions.

---

### 2.2 Resend API (OTP Email Delivery)

**Purpose**: Sends the 6-digit OTP to `jayankgajjala@gmail.com` during login.

**Step-by-step**:
1. Go to **https://resend.com** → Click **Get Started** → Sign up with Google or email
2. After signup: Dashboard → **API Keys** → **Create API Key**
3. Name it `quantedge-otp`
4. Set Permissions: **Full Access** (needed to send emails)
5. Click **Add** — the key is shown **only once**. Copy it immediately.
6. Format: `re_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

**Domain Setup** (required for production):
1. Dashboard → **Domains** → **Add Domain**
2. Add your domain (e.g., `yourdomain.com`)
3. Follow the DNS verification steps (usually takes 5–10 minutes)
4. Set `OTP_SENDER_EMAIL=noreply@yourdomain.com`

> **Free tier**: 3,000 emails/month — more than enough for personal use.

---

### 2.3 NewsAPI (News Article Fetching)

**Purpose**: Fetches financial news articles for FinBERT sentiment analysis.

**Step-by-step**:
1. Go to **https://newsapi.org** → Click **Get API Key**
2. Fill in the registration form (email + name)
3. Your key is shown on the dashboard immediately
4. Format: `xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` (32 characters, no prefix)

> **Free tier**: 100 requests/day. The 60-minute cache in `news_service.py`  
> prevents hitting this limit for portfolios up to ~10 holdings.

---

### 2.4 HuggingFace API (FinBERT + BART)

**Purpose**: Runs ProsusAI/FinBERT for sentiment scoring and BART for 3-bullet summaries.

**Step-by-step**:
1. Go to **https://huggingface.co** → Sign up (free)
2. Top-right menu → **Settings** → **Access Tokens**
3. Click **New token** → Name: `quantedge` → Type: **Read**
4. Click **Generate a token** → Copy it
5. Format: `hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

> **Free tier**: Rate limited but usable. The system retries on 503 (model loading).  
> Keyword-based sentiment fallback activates automatically if this key is missing.

---

### 2.5 ScraperAPI (Cloudflare Bypass — Strongly Recommended)

**Purpose**: Routes Investing.com and Moneycontrol scraper requests through  
residential IPs, bypassing Cloudflare bot detection on cloud servers.

**Why you need this**: Render/Railway/AWS IP addresses are in data-center ranges.  
Cloudflare blocks these automatically. Without ScraperAPI, macro data  
(DXY, US10Y Yield, Brent Crude) will return `DATA_UNAVAILABLE` in production.

**Step-by-step**:
1. Go to **https://scraperapi.com** → Click **Start Free Trial**
2. Sign up with email — no credit card required
3. Dashboard shows your **API Key** immediately on first login
4. Format: `xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

> **Free tier**: 1,000 API credits on signup (1 credit = 1 request).  
> For personal use this lasts several weeks. Paid: $29/month for 100,000 credits.

**Alternative (no account needed)**:  
`cloudscraper` is already in `requirements.txt` as a fallback. It rotates  
browser fingerprints but may still fail on aggressive Cloudflare configurations.

---

### 2.6 Gmail App Password (Signal Alerts)

**Purpose**: Sends HTML signal alert emails when confidence ≥ 85%.

**Step-by-step**:
1. Go to **https://myaccount.google.com/security**
2. Under **"How you sign in to Google"** → Click **2-Step Verification** → Enable it
3. Go to **https://myaccount.google.com/apppasswords**
4. Select **App**: Mail → **Device**: Other (Custom name) → Type: `Quantedge`
5. Click **Generate** — a 16-character password appears (format: `xxxx xxxx xxxx xxxx`)
6. Copy it — it's only shown once

Set these in `.env`:
```
ALERT_EMAIL_FROM=your.gmail@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
```

> **Important**: Use your Gmail address for `ALERT_EMAIL_FROM`, not an alias.

---

## 3. Database Setup

### Option A: Supabase (Recommended — Free 500MB)

1. **Create project**
   - Go to **https://supabase.com** → Sign in with GitHub
   - Click **New Project**
   - Organization: your personal org
   - Name: `quantedge`
   - Database Password: Generate a strong password (save it!)
   - Region: **ap-south-1 (Mumbai)** — lowest latency to NSE
   - Click **Create new project** (takes ~2 minutes)

2. **Get the connection string**
   - Left sidebar → **Settings** → **Database**
   - Scroll to **Connection string** section
   - Select **URI** tab
   - Toggle: **Display connection pooler** → ON → Mode: **Transaction**
   - Copy the URI — it looks like:
     ```
     postgresql://postgres.PROJECTID:PASSWORD@aws-0-ap-south-1.pooler.supabase.com:6543/postgres
     ```
   - Replace `[YOUR-PASSWORD]` with your database password

3. **Paste into .env**
   ```
   DATABASE_URL=postgresql://postgres.PROJECTID:PASSWORD@aws-0-ap-south-1.pooler.supabase.com:6543/postgres
   ```

4. **Let Alembic create the tables**
   ```bash
   cd Quantedge_Complete/backend
   DATABASE_URL="your-supabase-url" python -c "from backend.core.database import init_db; init_db()"
   ```
   This creates all 15+ tables automatically.

5. **Verify in Supabase**
   - Left sidebar → **Table Editor**
   - You should see tables: `holdings`, `final_signals`, `strategy_performance`, etc.

---

### Option B: Neon (Alternative — Free 512MB)

1. Go to **https://neon.tech** → Sign in with GitHub
2. Click **New Project** → Name: `quantedge` → Region: **ap-southeast-1**
3. Dashboard → **Connection Details** → Connection String (Pooled)
4. Format: `postgresql://user:pass@ep-name.ap-southeast-1.aws.neon.tech/quantedge?sslmode=require`

---

## 4. Local Development

```bash
# 1. Clone and navigate
cd Quantedge_Complete

# 2. Create virtual environment
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create your .env file
cp .env.example .env
# Now edit .env and fill in your API keys

# 5. Start the backend
uvicorn backend.main:app --reload --port 8000

# 6. Verify it works
curl http://localhost:8000/health
# Expected: {"status":"ok","database":"ok","scheduler":"running",...}
```

**Frontend (separate terminal)**:
```bash
cd Quantedge_Complete/frontend
npm install
echo "NEXT_PUBLIC_API_URL=http://localhost:8000" > .env.local
npm run dev
# Open: http://localhost:3000
```

**Login credentials**:
- Username: `Jayank8294`
- Password: `Jayanju@9498`

---

## 5. Backend Deployment (Railway)

Railway keeps containers alive 24/7 — critical for the APScheduler heartbeat.

### Step 1: Push to GitHub

```bash
cd Quantedge_Complete
git init
git add .
git commit -m "Initial Quantedge deployment"
# Create a repo on github.com, then:
git remote add origin https://github.com/YOUR_USERNAME/quantedge.git
git push -u origin main
```

### Step 2: Create Railway project

1. Go to **https://railway.app** → Sign in with GitHub
2. Click **New Project** → **Deploy from GitHub repo**
3. Select your `quantedge` repository
4. Railway auto-detects the `Dockerfile` and starts building

### Step 3: Set environment variables

1. Railway dashboard → your service → **Variables** tab
2. Click **Raw Editor** and paste all variables from your `.env`  
   (or add them one-by-one using the form)

**Required variables for Railway**:
```
DATABASE_URL=postgresql://...
JWT_SECRET_KEY=your-64-char-hex
RESEND_API_KEY=re_xxx
OTP_SENDER_EMAIL=noreply@yourdomain.com
HF_API_KEY=hf_xxx
NEWS_API_KEY=xxx
SCRAPERAPI_KEY=xxx
ALERT_EMAIL_FROM=your@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
FRONTEND_URL=https://your-app.vercel.app
FRONTEND_BASE_URL=https://your-app.vercel.app
PARQUET_CACHE_DIR=/tmp/parquet
```

### Step 4: Configure start command

Railway → Settings → Deploy → **Start Command**:
```
uvicorn backend.main:app --host 0.0.0.0 --port $PORT --workers 1
```

### Step 5: Verify deployment

Open your Railway public URL → `/health`

Expected response:
```json
{
  "status": "ok",
  "database": "ok",
  "db_driver": "postgresql",
  "scheduler": "running",
  "active_jobs": 5,
  "scheduler_tz": "Asia/Kolkata"
}
```

---

## 6. Frontend Deployment (Vercel)

### Step 1: Import to Vercel

1. Go to **https://vercel.com** → Sign in with GitHub
2. Click **Add New** → **Project**
3. Import your `quantedge` repository
4. **Framework Preset**: Next.js (auto-detected)
5. **Root Directory**: `frontend`

### Step 2: Set environment variable

Vercel → Project → **Settings** → **Environment Variables**:

| Name | Value |
|------|-------|
| `NEXT_PUBLIC_API_URL` | `https://your-backend.railway.app` |

### Step 3: Deploy

Click **Deploy**. Vercel builds and deploys — usually takes 2–3 minutes.

### Step 4: Update CORS on backend

Once you have your Vercel URL (e.g., `https://quantedge.vercel.app`):

1. Go to Railway → Variables
2. Update `FRONTEND_URL` to `https://quantedge.vercel.app`
3. Railway auto-redeploys

---

## 7. Environment Variables Reference

```env
# ── Required in ALL environments ────────────────────────────────
JWT_SECRET_KEY=                     # 64-char hex, generated once
RESEND_API_KEY=                     # from resend.com/api-keys

# ── Required in production only ──────────────────────────────────
DATABASE_URL=                       # Supabase or Neon PostgreSQL URL
FRONTEND_URL=                       # Your exact Vercel URL (no trailing slash)
FRONTEND_BASE_URL=                  # Same as FRONTEND_URL

# ── Required for full AI functionality ───────────────────────────
HF_API_KEY=                         # from huggingface.co/settings/tokens
NEWS_API_KEY=                       # from newsapi.org

# ── Strongly recommended for production ──────────────────────────
SCRAPERAPI_KEY=                     # from scraperapi.com (Cloudflare bypass)
ALERT_EMAIL_FROM=                   # your Gmail address
GMAIL_APP_PASSWORD=                 # Gmail App Password (16 chars)

# ── Optional (system works without these) ────────────────────────
PARQUET_CACHE_DIR=/tmp/parquet     # default; writable on all platforms
DEBUG=false                         # set true for verbose SQL logs
AUTH_PASSWORD=Jayanju@9498          # override default password
```

---

## 8. Verification

Run this checklist after deployment:

```bash
# 1. Backend health
curl https://your-backend.railway.app/health

# 2. Database connection
# Look for "database": "ok" in health response

# 3. Scheduler running
# Look for "scheduler": "running", "active_jobs": 5

# 4. Login flow
curl -X POST https://your-backend.railway.app/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"Jayank8294","password":"Jayanju@9498"}'
# Expected: {"message":"Password verified. Check your email for the OTP.",...}

# 5. OTP email
# Check jayankgajjala@gmail.com for the 6-digit OTP

# 6. Frontend CORS
# Open browser devtools → Network tab
# Login at https://your-app.vercel.app
# OPTIONS preflight should return 200 (not CORS error)
```

---

## 9. Troubleshooting

### "JWT_SECRET_KEY is not set"
Generate and set it:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
# Add output to Railway/Render environment variables as JWT_SECRET_KEY
```

### "psycopg2 not found" / database connection error
```bash
pip install psycopg2-binary==2.9.9
```

### Scraper returns DATA_UNAVAILABLE in production
1. Set `SCRAPERAPI_KEY` in your hosting platform
2. Or set `DEBUG=true` temporarily to see the exact error
3. Check scraper logs: `GET /api/market/macro` will show `source_flags`

### yfinance HTTP 429 (rate limit)
The parquet cache (20-hour TTL) prevents most rate limits.  
If it persists: the weekly backtest adds a 3-second sleep between tickers.  
For immediate relief: delete `/tmp/parquet/*.parquet` and restart.

### Vercel CORS error ("blocked by CORS policy")
1. Confirm `FRONTEND_URL` in Railway exactly matches your Vercel URL
2. No trailing slash: `https://quantedge.vercel.app` ✓ not `https://quantedge.vercel.app/` ✗
3. Redeploy backend after updating `FRONTEND_URL`

### Render free tier sleeping
Add a free uptime monitor:
1. Go to **https://uptimerobot.com** → Free signup
2. Add Monitor → HTTP → URL: `https://your-app.render.com/health`
3. Monitoring interval: 5 minutes
4. This keeps Render awake during NSE market hours

### APScheduler jobs not running
Check: `GET /health` → `active_jobs` should be 5.  
If 0: Check Railway logs for `"Scheduler started"` message.  
The scheduler requires `--workers 1` — multiple workers each spawn a scheduler.

### "tradingview-ta build failed"
```bash
pip install tradingview-ta --no-build-isolation
# Or on Ubuntu/Debian:
apt-get install -y gcc g++ && pip install tradingview-ta
```
The system degrades gracefully to `DATA_UNAVAILABLE` without this package.
