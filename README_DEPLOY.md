# Quantedge — OCI Deployment Guide
### Backend on Oracle Cloud ARM · Frontend on Vercel

> **Who is this for?**  
> This guide assumes you are comfortable copying and pasting commands but have no prior server or DevOps experience. Every step is explained in plain English.

---

## What You Will End Up With

```
Internet
  │
  ├── Vercel (Frontend / Next.js)   ← already deployed, no changes needed
  │
  └── Oracle Cloud (Backend / FastAPI)
        ├── Supabase PostgreSQL  ← user accounts + portfolio holdings
        ├── Neon PostgreSQL      ← sentiment scores + (future) backtests
        └── MongoDB Atlas        ← 7-day raw news archive
```

---

## Part 1 — Create an Oracle Cloud ARM Instance

1. Go to **https://cloud.oracle.com** and sign in (or create a free account).

2. In the top menu click **☰ → Compute → Instances → Create Instance**.

3. Fill in the form:
   - **Name**: `quantedge-backend` (or anything you like)
   - **Image**: Click *Change Image* → choose **Ubuntu 22.04**
   - **Shape**: Click *Change Shape* → select **Ampere** (ARM) → pick  
     `VM.Standard.A1.Flex`

   > **⚠️ Free Tier Reality Check**  
   > Oracle's Always Free Ampere allowance is **4 OCPUs and 24 GB RAM shared across your entire tenancy**.  
   > If this is your only instance you can use all 4 OCPUs and 24 GB. However, the OCI console slider will show a **maximum of 1 OCPU and 6 GB RAM** when the tenancy pool is already used up or when capacity is constrained in your region — this is normal and expected.  
   > **Use whatever the slider allows — even 1 OCPU / 6 GB is enough to run the app** (see the memory note in Part 4).

   - Set OCPUs and RAM to the **maximum the slider allows** (1 OCPU / 6 GB at minimum)
   - **Networking**: Leave defaults (a VCN will be created automatically)
   - **SSH keys**: Click *Generate a key pair* → **Download both keys**  
     ⚠️ Save the private key file — you cannot re-download it.

4. Click **Create**. Wait ~2 minutes until the Status shows **Running**.

5. Copy the **Public IP address** shown on the instance page. You will need it everywhere below.

---

## Part 2 — Open Port 8000 in the OCI Firewall

Oracle Cloud blocks all ports by default. You must open port 8000 for the API.

1. On the instance page click the **VCN name** link (looks like `vcn-quantedge`).
2. In the left sidebar click **Security Lists**.
3. Click the default security list (usually named `Default Security List for vcn-...`).
4. Click **Add Ingress Rules** and fill in:
   - **Source CIDR**: `0.0.0.0/0`
   - **IP Protocol**: TCP
   - **Destination Port Range**: `8000`
   - Description: `FastAPI`
5. Click **Add Ingress Rules** to save.

> **Also open port 22** (SSH) if it is not already open — use the same steps with port `22`.

---

## Part 3 — Connect to Your Instance

On **macOS / Linux** open Terminal. On **Windows** use PowerShell or install [PuTTY](https://putty.org).

```bash
# Replace YOUR_KEY_FILE and YOUR_IP with your actual values
chmod 400 ~/Downloads/your-private-key.key
ssh -i ~/Downloads/your-private-key.key ubuntu@YOUR_IP
```

You should see a prompt like `ubuntu@quantedge-backend:~$`. You are now inside the server.

---

## Part 4 — Install Python, Pip, and Clone the Repo

Run each block of commands one at a time. Wait for each to finish before moving to the next.

```bash
# Update the system
sudo apt update && sudo apt upgrade -y

# Install Python 3.11 and tools
sudo apt install -y python3.11 python3.11-venv python3-pip git curl

# Confirm Python works
python3.11 --version
# Expected output: Python 3.11.x
```

```bash
# Clone your repository (replace with your actual GitHub URL)
git clone https://github.com/YOUR_USERNAME/Personal_Trading_Dashboard.git
cd Personal_Trading_Dashboard/backend
```

```bash
# Create and activate a virtual environment
python3.11 -m venv venv
source venv/bin/activate

# ── Memory note for 1 OCPU / 6 GB instances ──────────────────────────────────
# FinBERT (ProsusAI/finbert) needs ~2.5 GB RAM to load. On a 6 GB instance this
# is tight once the OS and uvicorn workers are counted. Add a 4 GB swap file as
# a safety buffer — it costs nothing and prevents OOM kills.
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
# Make swap permanent across reboots
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
# Verify swap is active
free -h
# You should now see ~4 GB in the "Swap" row.
# ─────────────────────────────────────────────────────────────────────────────

# Install all dependencies
# torch for ARM CPU is large (~700 MB) — this will take several minutes
pip install --upgrade pip
pip install -r requirements.txt
```

---

## Part 5 — Add Environment Variables

All secrets are stored in a `.env` file that never gets committed to Git.

```bash
# Make sure you are in the backend folder
cd ~/Personal_Trading_Dashboard/backend

# Create the .env file and open it
nano .env
```

Paste the following into the editor, **replacing every placeholder** with your real values:

```env
# ── JWT ───────────────────────────────────────────────────────────────
SECRET_KEY=your_very_long_random_secret_key_here
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60

# ── Email (Resend) ────────────────────────────────────────────────────
RESEND_API_KEY=re_xxxxxxxxxxxxxxxxxxxx
OTP_FROM_EMAIL=noreply@yourdomain.com

# ── Supabase (Auth + Holdings) ────────────────────────────────────────
# Get this from Supabase Dashboard → Settings → Database → Connection String
# Choose "Transaction" mode and the asyncpg format
DATABASE_URL=postgresql+asyncpg://postgres.xxxx:password@aws-0-ap-south-1.pooler.supabase.com:6543/postgres
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

# ── Neon (Sentiment Results) ──────────────────────────────────────────
# Get this from Neon Dashboard → Connection Details → Connection String
NEON_DATABASE_URL=postgresql+asyncpg://user:password@ep-xxx.ap-southeast-1.aws.neon.tech/neondb?sslmode=require

# ── MongoDB (News Archive) ────────────────────────────────────────────
# Get this from MongoDB Atlas → Connect → Drivers → copy the URI
MONGODB_URI=mongodb+srv://username:password@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority

# ── FinBERT / HuggingFace ─────────────────────────────────────────────
# These two lines prevent memory crashes on OCI ARM Free Tier
TORCH_DEVICE=cpu
HF_HOME=/tmp/huggingface

# ── Market data APIs ──────────────────────────────────────────────────
FINNHUB_API_KEY=your_finnhub_key
GNEWS_API_KEY=your_gnews_key

# ── CORS — allow your Vercel frontend ────────────────────────────────
# Add your Vercel URL here (no trailing slash)
ALLOWED_ORIGINS=https://your-app.vercel.app,http://localhost:3000
```

Save and exit: press `Ctrl+O`, then `Enter`, then `Ctrl+X`.

---

## Part 6 — Run the FastAPI App

### Option A — Quick test (stops when you close the terminal)

```bash
cd ~/Personal_Trading_Dashboard/backend
source venv/bin/activate

uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open `http://YOUR_IP:8000/health` in your browser. You should see a JSON response with `"status": "ok"`.  
Press `Ctrl+C` to stop.

---

### Option B — Keep it running with `nohup` (simple, no extra installs)

```bash
cd ~/Personal_Trading_Dashboard/backend
source venv/bin/activate

nohup uvicorn app.main:app --host 0.0.0.0 --port 8000 > ~/uvicorn.log 2>&1 &

# Check that it started
echo "PID: $!"
curl http://localhost:8000/health
```

- Logs go to `~/uvicorn.log` — view them with `tail -f ~/uvicorn.log`
- To stop: find the process with `ps aux | grep uvicorn` then `kill PID`

---

### Option C — Keep it running with PM2 (recommended — auto-restarts on crash)

```bash
# Install Node.js (needed for PM2 only)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs

# Install PM2 globally
sudo npm install -g pm2

# Start the FastAPI app via PM2
cd ~/Personal_Trading_Dashboard/backend
source venv/bin/activate

pm2 start \
  "venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000" \
  --name quantedge-api \
  --interpreter none

# Save the process list so it survives a server reboot
pm2 save
pm2 startup   # follow the printed instruction (one sudo command)
```

**Useful PM2 commands:**

| Command | What it does |
|---|---|
| `pm2 status` | Show if the app is running |
| `pm2 logs quantedge-api` | Stream live logs |
| `pm2 restart quantedge-api` | Restart after a code change |
| `pm2 stop quantedge-api` | Stop the app |

---

## Part 7 — Update Your Vercel Frontend

In your Vercel project settings, add one environment variable:

| Name | Value |
|---|---|
| `NEXT_PUBLIC_API_URL` | `http://YOUR_OCI_IP:8000` |

Redeploy the frontend for the change to take effect.

---

## Part 8 — Updating the Code

Whenever you push new code to GitHub:

```bash
ssh -i ~/Downloads/your-private-key.key ubuntu@YOUR_IP

cd ~/Personal_Trading_Dashboard
git pull

cd backend
source venv/bin/activate
pip install -r requirements.txt   # only needed if requirements changed

# If using PM2:
pm2 restart quantedge-api

# If using nohup: kill the old process and run the nohup command again
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Connection refused` on port 8000 | Re-check Part 2 — the OCI Security List ingress rule |
| `ModuleNotFoundError` | Run `source venv/bin/activate` before any Python command |
| FinBERT download hangs / OOM killed | (1) Confirm `TORCH_DEVICE=cpu` and `HF_HOME=/tmp/huggingface` in `.env`. (2) Check swap is active: `free -h` — if the Swap row shows 0, re-run the `fallocate` block in Part 4. (3) On 6 GB RAM: OS ~1 GB + uvicorn ~0.3 GB + FinBERT load ~2.5 GB + VADER/other ~0.5 GB ≈ 4.3 GB peak — swap covers the remainder. |
| Supabase `prepared statement` error | Ensure `DATABASE_URL` uses the **Transaction** (port 6543) pooler, not Direct |
| Neon SSL error | Confirm `NEON_DATABASE_URL` ends with `?sslmode=require` |
| MongoDB `ServerSelectionTimeoutError` | Whitelist your OCI public IP in MongoDB Atlas → Network Access |

---

*Last updated for Quantedge v2.0 — OCI ARM (1 OCPU / 6 GB Free Tier) / Supabase / Neon / MongoDB architecture.*
