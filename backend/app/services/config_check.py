from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import make_url

from app.config import REQUIRED_CONFIG_KEYS, SECRET_CONFIG_KEYS, AppSettings
from app.db.session import create_db_engine
from app.models import SavedRubric
from app.schemas.system import ConfigCheckResponse, ConfigRequirementStatus, DatabaseReadinessStatus


def build_config_check(settings: AppSettings) -> ConfigCheckResponse:
    required = [
        ConfigRequirementStatus(
            name=env_name,
            configured=settings.is_configured(env_name),
            secret=env_name in SECRET_CONFIG_KEYS,
        )
        for env_name in REQUIRED_CONFIG_KEYS
    ]
    missing = [item.name for item in required if not item.configured]
    database = build_database_readiness_status(settings)

    return ConfigCheckResponse(
        ready=not missing
        and settings.require_human_final_decision
        and settings.ai_decision_support_only
        and database_is_ready(database),
        environment=settings.app_env,
        required=required,
        missing=missing,
        safeguards={
            "require_human_final_decision": settings.require_human_final_decision,
            "ai_decision_support_only": settings.ai_decision_support_only,
        },
        database=database,
    )


def build_database_readiness_status(settings: AppSettings) -> DatabaseReadinessStatus:
    if not settings.database_url:
        return DatabaseReadinessStatus(
            configured=False,
            dialect=None,
            auto_create_schema=settings.auto_create_db_schema,
            checked=False,
            required_tables={SavedRubric.__tablename__: False},
        )

    dialect = make_url(settings.database_url).drivername
    if dialect.startswith("sqlite") and not settings.auto_create_db_schema:
        return DatabaseReadinessStatus(
            configured=True,
            dialect=dialect,
            auto_create_schema=settings.auto_create_db_schema,
            checked=False,
            required_tables={SavedRubric.__tablename__: False},
        )

    engine = create_db_engine(settings.database_url)
    try:
        inspector = inspect(engine)
        return DatabaseReadinessStatus(
            configured=True,
            dialect=dialect,
            auto_create_schema=settings.auto_create_db_schema,
            checked=True,
            required_tables={
                SavedRubric.__tablename__: inspector.has_table(SavedRubric.__tablename__),
            },
        )
    except Exception as exc:  # pragma: no cover - defensive around external database checks.
        return DatabaseReadinessStatus(
            configured=True,
            dialect=dialect,
            auto_create_schema=settings.auto_create_db_schema,
            checked=True,
            required_tables={SavedRubric.__tablename__: False},
            error_type=exc.__class__.__name__,
        )
    finally:
        engine.dispose()


def database_is_ready(database: DatabaseReadinessStatus) -> bool:
    if not database.configured:
        return False
    if database.error_type:
        return False
    if not database.checked:
        return True
    return all(database.required_tables.values())
