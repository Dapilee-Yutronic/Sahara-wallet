import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Local dev: default file in project root. Production: set SQLITE_PATH e.g. /data/global_wallet.db + volume mount.
_sqlite_path = os.environ.get("SQLITE_PATH", "global_wallet.db")
_sqlite_path = str(Path(_sqlite_path).resolve())
Path(_sqlite_path).parent.mkdir(parents=True, exist_ok=True)
SQLALCHEMY_DATABASE_URL = f"sqlite:///{Path(_sqlite_path).as_posix()}"

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
