import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Production (Railway, etc.): add the Postgres plugin and let Railway set DATABASE_URL.
# That database lives on Railway's servers and survives redeploys.
#
# Alternative (SQLite only): mount a volume at /data and keep SQLITE_PATH=/data/global_wallet.db
# Without a volume, the SQLite file is inside the container and is wiped on each deploy.

Base = declarative_base()


def _normalize_postgres_url(url: str) -> str:
    url = url.strip()
    if url.startswith("postgres://"):
        return "postgresql+psycopg2://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg2://" + url[len("postgresql://") :]
    return url


def _sqlite_url() -> str:
    _sqlite_path = os.environ.get("SQLITE_PATH", "global_wallet.db")
    _sqlite_path = str(Path(_sqlite_path).resolve())
    Path(_sqlite_path).parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{Path(_sqlite_path).as_posix()}"


_db_url = os.environ.get("DATABASE_URL", "").strip()
if _db_url:
    SQLALCHEMY_DATABASE_URL = _normalize_postgres_url(_db_url)
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )
else:
    SQLALCHEMY_DATABASE_URL = _sqlite_url()
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
