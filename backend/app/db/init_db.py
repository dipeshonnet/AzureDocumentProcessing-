from __future__ import annotations

from sqlalchemy import Engine, inspect, text, select

from app.db.base import Base
from app.models import admissions as _admissions_models


def init_db(engine: Engine) -> None:
    """Create local database tables.

    This is intentionally simple until Alembic migrations are introduced.
    """

    Base.metadata.create_all(bind=engine)
    _ensure_existing_schema_columns(engine)
    _seed_default_university_and_migrate(engine)


def _ensure_existing_schema_columns(engine: Engine) -> None:
    """Add columns introduced before Alembic is available."""

    required_columns = _required_columns_for_dialect(engine.dialect.name)
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


def _seed_default_university_and_migrate(engine: Engine) -> None:
    from app.models.admissions import University, LocalUser, SavedRubric, Application
    from sqlalchemy.orm import Session
    
    with Session(bind=engine) as session:
        default_uni = session.scalar(select(University).where(University.name == "Default University"))
        if not default_uni:
            default_uni = University(
                name="Default University",
                logo_data_url=None,
                pages_per_billable_unit=1,
            )
            session.add(default_uni)
            session.flush()
        
        # Migrate users
        users_to_migrate = session.scalars(
            select(LocalUser).where(LocalUser.university_id.is_(None))
        ).all()
        for user in users_to_migrate:
            user.university_id = default_uni.university_id
            
        # Migrate rubrics
        rubrics_to_migrate = session.scalars(
            select(SavedRubric).where(SavedRubric.university_id.is_(None))
        ).all()
        for rubric in rubrics_to_migrate:
            rubric.university_id = default_uni.university_id
            
        # Migrate applications
        apps_to_migrate = session.scalars(
            select(Application).where(Application.university_id.is_(None))
        ).all()
        for app in apps_to_migrate:
            app.university_id = default_uni.university_id
            
        session.commit()


def _required_columns_for_dialect(dialect_name: str) -> dict[str, dict[str, str]]:
    if dialect_name == "postgresql":
        return {
            "applicants": {
                "student_unique_id": "VARCHAR(120)",
            },
            "applications": {
                "program_applied": "VARCHAR(200)",
                "intake_term": "VARCHAR(80)",
                "processing_errors": "JSONB NOT NULL DEFAULT '[]'::jsonb",
                "reviewer_audit_metadata": "JSONB NOT NULL DEFAULT '[]'::jsonb",
                "university_id": "VARCHAR(36)",
            },
            "document_summaries": {
                "summary_metadata": "JSONB NOT NULL DEFAULT '{}'::jsonb",
            },
            "rubric_criterion_scores": {
                "reviewer_override": "JSONB",
                "scoring_metadata": "JSONB NOT NULL DEFAULT '{}'::jsonb",
            },
            "local_users": {
                "billing_rate_per_unit": "FLOAT NOT NULL DEFAULT 1.0",
                "university_logo_data_url": "TEXT",
                "last_login_at": "TIMESTAMP WITH TIME ZONE",
                "university_id": "VARCHAR(36)",
            },
            "saved_rubrics": {
                "university_id": "VARCHAR(36)",
            },
        }
    return {
        "applicants": {
            "student_unique_id": "VARCHAR(120)",
        },
        "applications": {
            "program_applied": "VARCHAR(200)",
            "intake_term": "VARCHAR(80)",
            "processing_errors": "JSON NOT NULL DEFAULT '[]'",
            "reviewer_audit_metadata": "JSON NOT NULL DEFAULT '[]'",
            "university_id": "VARCHAR(36)",
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
            "university_id": "VARCHAR(36)",
        },
        "saved_rubrics": {
            "university_id": "VARCHAR(36)",
        },
    }
