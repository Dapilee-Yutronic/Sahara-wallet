# Sahara / Global Wallet MVP — deploy anywhere that runs containers (Railway, Render, Fly.io, etc.)
FROM python:3.12-slim-bookworm

WORKDIR /app
ENV PYTHONUNBUFFERED=1
# Persist DB on a mounted volume (set in your host UI) so data survives redeploys
ENV SQLITE_PATH=/data/global_wallet.db

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static

RUN mkdir -p /data static/uploads

EXPOSE 8000

# Railway, Render, Fly, etc. set PORT
CMD sh -c "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"
