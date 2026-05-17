from __future__ import annotations

from sqlalchemy import Engine, inspect, text

from app.db.base import Base
from app.models import admissions as _admissions_models


def init_db(engine: Engine) -> None:
    """Create local database tables.

    This is intentionally simple until Alembic migrations are introduced.
    """

    Base.metadata.create_all(bind=engine)
    if engine.dialect.name == "sqlite":
        _ensure_sqlite_json_columns(engine)


def _ensure_sqlite_json_columns(engine: Engine) -> None:
    """Add local SQLite JSON columns introduced before Alembic is available."""

    required_columns = {
        "applications": {
            "processing_errors": "JSON NOT NULL DEFAULT '[]'",
            "reviewer_audit_metadata": "JSON NOT NULL DEFAULT '[]'",
        },
        "document_summaries": {
            "summary_metadata": "JSON NOT NULL DEFAULT '{}'",
        },
        "rubric_criterion_scores": {
            "reviewer_override": "JSON",
            "scoring_metadata": "JSON NOT NULL DEFAULT '{}'",
        },
        "local_users": {
            "billing_rate_per_unit": "FLOAT NOT NULL DEFAULT 1.0",
            "university_logo_data_url": "TEXT",
            "last_login_at": "DATETIME",
        },
    }
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as connection:
        for table_name, columns in required_columns.items():
            if table_name not in existing_tables:
                continue
            existing_columns = {
                column["name"]
                for column in inspector.get_columns(table_name)
            }
            for column_name, definition in columns.items():
                if column_name not in existing_columns:
                    connection.execute(
                        text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")
                    )
