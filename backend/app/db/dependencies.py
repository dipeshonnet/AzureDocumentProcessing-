from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache

from fastapi import Depends
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppSettings, get_settings
from app.db.init_db import init_db
from app.db.session import create_db_engine, create_session_factory, get_database_url


@lru_cache
def _engine_for_url(database_url: str, auto_create_schema: bool = False) -> Engine:
    engine = create_db_engine(database_url)
    if database_url.startswith("sqlite") or auto_create_schema:
        init_db(engine)
    return engine


@lru_cache
def _session_factory_for_url(database_url: str, auto_create_schema: bool = False) -> sessionmaker[Session]:
    return create_session_factory(_engine_for_url(database_url, auto_create_schema))


def get_db(settings: AppSettings = Depends(get_settings)) -> Generator[Session, None, None]:
    database_url = get_database_url(settings)
    session_factory = _session_factory_for_url(database_url, settings.auto_create_db_schema)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
