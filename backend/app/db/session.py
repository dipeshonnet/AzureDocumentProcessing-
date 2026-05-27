from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppSettings


def get_database_url(settings: AppSettings) -> str:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required before database access is initialized.")
    return settings.database_url


def create_db_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite"):
        return create_engine(database_url, connect_args={"check_same_thread": False}, future=True)
    return create_engine(database_url, future=True, pool_pre_ping=True, pool_recycle=1800)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db_session(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
