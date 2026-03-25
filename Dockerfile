# Sahara / Global Wallet MVP — deploy anywhere that runs containers (Railway, Render, Fly.io, etc.)
FROM python:3.12-slim-bookworm

WORKDIR /app
ENV PYTHONUNBUFFERED=1
# SQLite: mount a Railway volume on /data so this file survives redeploys.
# Better: use Railway Postgres — add the Postgres service; DATABASE_URL is injected automatically.
ENV SQLITE_PATH=/data/global_wallet.db

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static

RUN mkdir -p /data/uploads static/uploads

EXPOSE 8000

# Railway, Render, Fly, etc. set PORT
CMD sh -c "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"
