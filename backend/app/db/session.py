from __future__ import annotations

from collections.abc import Generator

import re

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import AppSettings


def get_database_url(settings: AppSettings) -> str:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required before database access is initialized.")
    return settings.database_url


def create_db_engine(database_url: str, database_schema: str | None = None) -> Engine:
    if database_schema and not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", database_schema):
        raise ValueError("Invalid database schema name.")
    if database_schema and not database_url.startswith("postgresql"):
        raise ValueError("DATABASE_SCHEMA requires PostgreSQL.")
    if database_url.startswith("sqlite"):
        return create_engine(database_url, connect_args={"check_same_thread": False}, future=True)
    if database_url.startswith("mssql"):
        import pyodbc
        pyodbc.pooling = False
        return create_engine(database_url, future=True, poolclass=NullPool, connect_args={"timeout": 60})
    engine = create_engine(database_url, future=True, pool_pre_ping=True, pool_recycle=1800,
                           pool_size=2, max_overflow=2)
    if database_schema:
        # A session pooler preserves this setting. Never fall back to public.
        @event.listens_for(engine, "connect", insert=True)
        def select_private_schema(connection, _record):
            previous = connection.autocommit
            connection.autocommit = True
            try:
                with connection.cursor() as cursor:
                    cursor.execute(f'SET SESSION search_path TO "{database_schema}"')
            finally:
                connection.autocommit = previous
        engine.update_execution_options(admissions_schema=database_schema)
    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db_session(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
