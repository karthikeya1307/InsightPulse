"""Database engine and session management (SQLAlchemy 2.x)."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


@lru_cache
def get_engine(url: str | None = None) -> Engine:
    """Create (once per URL) a pooled engine."""
    # prepare_threshold=None: no server-side prepared statements. Queries over the "current prediction"
    # views get a much worse generic plan once prepared (seconds -> minutes at full scale).
    return create_engine(url or get_settings().database_url, pool_pre_ping=True, future=True,
                         connect_args={"prepare_threshold": None})


def get_sessionmaker(url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(url), expire_on_commit=False, future=True)


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    """Transactional scope: commit on success, rollback on any exception."""
    session = get_sessionmaker(url)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a session bound to the configured database."""
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


def check_connection(engine: Engine | None = None) -> dict:
    """Return basic connectivity information; raises on failure."""
    engine = engine or get_engine()
    with engine.connect() as conn:
        version = conn.execute(text("select version()")).scalar_one()
        db = conn.execute(text("select current_database()")).scalar_one()
    return {"database": db, "server_version": version.split(",")[0]}
