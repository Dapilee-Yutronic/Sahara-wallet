# Global Wallet MVP

An MVP cross-border wallet platform for:
- receiving funds into USD wallets,
- converting USD to GHS with transparent FX quotes,
- withdrawing to Ghana bank/mobile money rails (simulated),
- reviewing users and payouts from admin endpoints.

## Stack
- Backend: FastAPI + SQLAlchemy + SQLite
- Frontend: Static HTML/JS dashboard (served by FastAPI)

## Features in this MVP
- User registration and login (JWT)
- KYC status tracking
- Wallet balances in USD and GHS
- Double-entry style ledger postings for key wallet operations
- FX quote + conversion flow
- Withdrawal request flow
- Admin list/review endpoints

## Quick Start
1. Create and activate a virtual environment:
   - Windows PowerShell:
     - `python -m venv .venv`
     - `.venv\Scripts\Activate.ps1`
2. Install dependencies:
   - `pip install -r requirements.txt`
3. Run the API:
   - **Local only (your PC):** `uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`
   - **Recommended default:** `python run.py` — listens on **`0.0.0.0:8000`** so the app is reachable from other devices and tunnels (see below).
4. Open:
   - Health check: `http://127.0.0.1:8000/health` (should return `{"status":"ok",...}`)
   - API docs: `http://127.0.0.1:8000/docs`
   - Web app: `http://127.0.0.1:8000/`
   - Admin panel: `http://127.0.0.1:8000/admin-panel`

## Sharing with friends (why “it doesn’t work” for them)

### Friends in other states (Boston, Pennsylvania, etc.) — not in your house

They **cannot** use your Wi‑Fi IP (`192.168.x.x`). That only works on **your** home network. For people across the country you need a **public internet URL** that reaches your computer:

1. On your PC, run the app: **`python run.py`** (listens on `0.0.0.0:8000` so tunnels can forward to it).
2. Start a **tunnel** on the same PC so the internet gets a real `https://…` address:
   - [ngrok](https://ngrok.com/): install, then `ngrok http 8000`
   - [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/): e.g. `cloudflared tunnel --url http://localhost:8000`
3. Copy the **https://…** URL the tool shows (random subdomain). That is what you text to friends in Boston, PA, etc.
4. They open that link in **Safari or Chrome on iPhone**, or **Chrome / Edge / Firefox on desktop** — **same URL for everyone**. The UI is responsive (mobile layout + bottom nav) and works as a simple installable PWA (“Add to Home Screen” on phones).
5. **Test first:** open `https://YOUR-TUNNEL-URL/health` on your own phone (cellular, not Wi‑Fi) — you should see `{"status":"ok",...}`. Then share the same base URL without `/health` for the app.

**Long-term / always-on:** put the app on a small cloud host (Railway, Render, Fly.io, a VPS) so you don’t need your PC running. Same codebase; you’d set `PORT` from the host and deploy with their instructions.

### Same Wi‑Fi only (friends visiting your home)

- Use your PC’s LAN IPv4, e.g. `http://192.168.x.x:8000/` — still requires `python run.py` (not `127.0.0.1`-only binding).

### General rules

1. **Never send `localhost` or `127.0.0.1` links** — those open *their* machine, not yours.
2. **Bind the server for sharing:** plain `uvicorn app.main:app` without `--host 0.0.0.0` only accepts **your** PC. Use **`python run.py`** or `uvicorn app.main:app --host 0.0.0.0 --port 8000`.
3. **Firewall:** if you expose a port directly (rare for home users), allow inbound TCP on that port. Tunnels (ngrok / Cloudflare) usually **avoid** opening your router — that’s why they’re easiest.
4. **Phone + desktop:** no separate app store build — the web app runs in the browser everywhere; install prompts are optional PWA shortcuts.

## Deploy to the cloud (stable HTTPS link for testers)

This repo includes a **`Dockerfile`**. Your friends use the **same URL** on phone and desktop — no tunnel needed once it’s live.

### Checklist (do this in order)

1. **Put the code on GitHub** (see commands below if you don’t have a repo yet).
2. Pick **Railway** *or* **Render** (both work with the Dockerfile).
3. **Add a volume** mounted at **`/data`** so sign-ups and wallets survive redeploys (optional on first try; without it, data resets when the host restarts).
4. Copy the **HTTPS URL** the platform gives you and send that to friends — **not** `localhost`.
5. Test: `https://YOUR-URL/health` → should show `{"status":"ok",...}`.

**Push this folder to GitHub (first time):**

```bash
cd global-wallet-mvp
git init
git add .
git commit -m "Sahara wallet MVP"
# Create an empty repo on github.com, then:
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git branch -M main
git push -u origin main
```

### Option A — Railway (often fastest)

1. Go to [railway.app](https://railway.app) → sign in with GitHub.
2. **New Project** → **Deploy from GitHub repo** → select this repository.
3. Railway builds from the **Dockerfile** automatically.
4. **Settings** → **Volumes** → **Add volume** → mount path **`/data`** (matches `SQLITE_PATH` in the Dockerfile).
5. **Settings** → **Networking** → **Generate domain** (HTTPS).
6. Share that URL. Test `https://…/health` first.

### Option B — Render

1. Push to GitHub (checklist above).
2. [render.com](https://render.com) → **New** → **Web Service** → connect the repo → environment **Docker**.
3. **Advanced** → add a **persistent disk**, mount path **`/data`**, size **1 GB** (if your plan allows; free tier may limit disks — you can deploy without a disk for a quick demo).
4. Deploy; use the **`onrender.com`** URL.

Optional: this repo includes **`render.yaml`** for a Blueprint-style deploy if your Render account supports it.

### Option C — Docker on your own server

```bash
docker build -t sahara-wallet .
docker run -p 8000:8000 -v sahara-data:/data -e SQLITE_PATH=/data/global_wallet.db sahara-wallet
```

Put HTTPS in front (Caddy, nginx, or a host’s load balancer).

### While you keep developing

- **Redeploy** = push to GitHub; Railway/Render rebuild from the Dockerfile.
- **Data:** with a volume on `/data`, user accounts and wallets persist between deploys.
- **Security (later):** change `SECRET_KEY` in `app/auth.py` or via env before a real launch; rotate default admin password.

## Default Admin
- Email: `admin@globalwallet.app`
- Password: `Admin123!`

Created automatically on first run.
