# Develop locally, deploy to Railway when ready

## Day-to-day (no Railway)

1. Open a terminal in this folder (`global-wallet-mvp`).
2. Run:

   ```powershell
   .\scripts\dev.ps1
   ```

   Or manually:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
   ```

3. Open **http://127.0.0.1:8000** — edit code; `--reload` restarts the server when you save files.

4. Optional: copy `.env.example` to `.env` and adjust. With no `DATABASE_URL`, the app uses **SQLite** (`global_wallet.db` in this folder).

You can change the app as much as you want **without** touching Railway until you choose to ship.

## When you want a “good” Railway deploy

1. Commit and push to GitHub (Railway builds from your repo).
2. On Railway, attach **PostgreSQL** so **`DATABASE_URL`** is set. User data then **survives redeploys** when you release new code.
3. Redeploying because you **pushed new code** is normal and expected. What you avoid is **losing accounts** on each deploy — that’s the database persistence, not “never redeploy.”

## Summary

| Where        | Purpose                                      |
|-------------|-----------------------------------------------|
| Your PC     | Build and test everything (`dev.ps1`)        |
| Railway     | Public URL + Postgres for stable production  |
