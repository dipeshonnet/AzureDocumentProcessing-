from pathlib import Path

import pytest
import yaml
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.config import AppSettings
from app.db.base import Base
from app.db.session import create_db_engine
from app.models import admissions, workflow  # register all hosted tables
from app.services.free_hosting import validate_free_hosting


def free_settings(**overrides):
    values = {
        'APP_ENV': 'render-free', 'DEV_AUTH_ENABLED': 'false', 'EXPOSE_LEGACY_API': 'false',
        'STORAGE_BACKEND': 'supabase', 'DOCUMENT_EXTRACTION_BACKEND': 'llamaparse',
        'DATABASE_SCHEMA': 'admissions_app',
        'DATABASE_URL': 'postgresql+psycopg://postgres:test-password@localhost/postgres?sslmode=require',
        'MAX_UPLOAD_BYTES': '4194304', 'PARSER_MODE': 'azure', 'INTAKE_WORKER_ENABLED': 'true',
        'SUPABASE_URL': 'https://example.supabase.co', 'SUPABASE_SERVICE_KEY': 'test-server-key',
        'LLAMA_CLOUD_API_KEY': 'test-key', 'BOOTSTRAP_ADMIN_EMAIL': 'admin@example.org',
        'BOOTSTRAP_ADMIN_PASSWORD': 'test-password-with-16-chars',
        'PUBLIC_APP_URL': 'https://admissions.example.org',
        'BACKEND_CORS_ORIGINS': 'https://admissions.example.org',
    }
    values.update(overrides)
    return AppSettings.from_mapping(values)


def test_free_profile_accepts_private_postgres_and_free_backends():
    validate_free_hosting(free_settings())


@pytest.mark.parametrize('overrides', [
    {'DEV_AUTH_ENABLED': 'true'}, {'EXPOSE_LEGACY_API': 'true'},
    {'STORAGE_BACKEND': 'local'}, {'DOCUMENT_EXTRACTION_BACKEND': 'azure'},
    {'DATABASE_SCHEMA': 'public'}, {'DATABASE_URL': 'sqlite://'},
    {'DATABASE_URL': 'postgresql+psycopg://postgres:secret@localhost/postgres'},
    {'MAX_UPLOAD_BYTES': '4194305'}, {'PARSER_MODE': 'mock'},
    {'INTAKE_WORKER_ENABLED': 'false'}, {'SUPABASE_SERVICE_KEY': ''},
    {'LLAMA_CLOUD_API_KEY': ''}, {'BOOTSTRAP_ADMIN_PASSWORD': 'short'},
    {'PUBLIC_APP_URL': 'http://admissions.example.org'},
    {'BACKEND_CORS_ORIGINS': 'https://other.example.org'},
    *[{key: 'azure'} for key in ('DOCUMENT_CLASSIFICATION_BACKEND', 'STRUCTURED_EXTRACTION_BACKEND',
                                'DOCUMENT_SUMMARIZATION_BACKEND', 'RUBRIC_SCORING_BACKEND')],
])
def test_free_profile_rejects_unsafe_or_paid_configuration(overrides):
    with pytest.raises(RuntimeError) as error:
        validate_free_hosting(free_settings(**overrides))
    assert 'secret@' not in str(error.value)


def test_schema_names_cannot_escape_search_path():
    for schema in ['public,admissions_app', 'x";DROP SCHEMA public;--', 'MixedCase']:
        with pytest.raises(ValueError):
            create_db_engine('postgresql+psycopg://localhost/postgres', schema)
    with pytest.raises(ValueError, match='requires PostgreSQL'):
        create_db_engine('sqlite://', 'admissions_app')


def test_all_application_tables_compile_for_postgresql():
    assert {'universities', 'admissions_workflow_records', 'intake_jobs'} <= set(Base.metadata.tables)
    for table in Base.metadata.sorted_tables:
        assert str(CreateTable(table).compile(dialect=postgresql.dialect()))


def test_blueprint_has_only_one_explicit_free_service():
    blueprint = yaml.safe_load((Path(__file__).resolve().parents[2] / 'render.yaml').read_text())
    assert set(blueprint) == {'services'}
    assert len(blueprint['services']) == 1
    service = blueprint['services'][0]
    assert service['type'] == 'web' and service['plan'] == 'free'
    assert service['autoDeployTrigger'] == 'commit'
    assert 'disk' not in service and 'scaling' not in service
