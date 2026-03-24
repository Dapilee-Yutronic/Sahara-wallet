"""
Run the demo API so other devices can connect (binds to all interfaces by default).

Usage (from this project folder):
  python run.py

Environment:
  HOST   default 0.0.0.0  (required for friends on your LAN or a tunnel to reach you)
  PORT   default 8000
  RELOAD set to 1 for auto-reload during development
"""

import os

import uvicorn

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    reload = os.environ.get("RELOAD", "").lower() in ("1", "true", "yes")
    uvicorn.run("app.main:app", host=host, port=port, reload=reload)
