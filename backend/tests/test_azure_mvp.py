"""Cloud mode verification against local durable storage; no Azure billing."""
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import mssql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.config import get_settings
from app.db.base import Base
from app.main import create_app
from app.models import Applicant


@pytest.fixture
def cloud_app(monkeypatch, tmp_path):
    frontend = tmp_path / 'frontend'
    frontend.mkdir()
    (frontend / 'index.html').write_text('<html>MVP frontend</html>', encoding='utf-8')
    values = {
        'APP_ENV': 'azure-mvp', 'DATABASE_URL': f'sqlite:///{tmp_path / "mvp.db"}',
        'LOCAL_STORAGE_ROOT': str(tmp_path / 'uploads'), 'STORAGE_BACKEND': 'local',
        'DEV_AUTH_ENABLED': 'false', 'EXPOSE_LEGACY_API': 'false',
        'BOOTSTRAP_ADMIN_EMAIL': 'owner@example.edu',
        'BOOTSTRAP_ADMIN_PASSWORD': 'Cloud-Test-Password-123!',
        'AUTO_CREATE_DB_SCHEMA': 'true', 'INTAKE_WORKER_ENABLED': 'true',
        'PARSER_MODE': 'mock', 'INTAKE_MOCK_PROCESSING_DELAY_SECONDS': '0',
        'FRONTEND_DIST_PATH': str(frontend),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


def login_headers(client):
    response = client.post('/api/auth/login', json={
        'email': 'owner@example.edu', 'password': 'Cloud-Test-Password-123!'
    })
    assert response.status_code == 200
    return {'Authorization': 'Bearer ' + response.json()['token']}


def test_cloud_login_upload_completion_restart_and_logout(cloud_app):
    with TestClient(cloud_app) as client:
        assert client.get('/health').status_code == 200
        assert 'MVP frontend' in client.get('/').text
        assert client.get('/api/runtime').json() == {'demo_mode': True}
        assert client.get('/api/jobs/status', headers={'x-dev-role': 'superadmin'}).status_code == 401
        assert client.get('/api/v1/applications/arbitrary').status_code == 404
        assert client.post('/api/auth/login', json={'email': 'superadmin', 'password': 'EverydayAI'}).status_code == 401
        headers = login_headers(client)
        response = client.post('/api/upload', headers=headers, data={},
                               files={'file': ('sample.pdf', b'sample demo document', 'application/pdf')})
        assert response.status_code == 201
        job_id = response.json()['job_id']
        for _ in range(50):
            job = client.get(f'/api/jobs/{job_id}', headers=headers).json()
            if job['status'] in {'completed', 'failed'}:
                break
            time.sleep(0.05)
        assert job['status'] == 'completed'
        assert job['parser_mode'] == 'mock'
        assert client.get(f'/api/jobs/{job_id}/record', headers=headers).status_code == 200
    # Recreate app to mimic a new replica, with SQL/session and upload persistence.
    with TestClient(create_app()) as client:
        assert client.get(f'/api/jobs/{job_id}', headers=headers).json()['status'] == 'completed'
        assert client.post('/api/auth/logout', headers=headers).status_code == 200
        assert client.get('/api/jobs/status', headers=headers).status_code == 401


def test_cloud_requires_unique_admin_password_and_atomic_creation(cloud_app):
    with TestClient(cloud_app) as client:
        headers = login_headers(client)
        payload = {'name': 'Test University', 'admin_email': 'admin@test.edu'}
        assert client.post('/api/auth/universities', headers=headers, json=payload).status_code == 400
        payload['admin_password'] = 'Initial-Test-Password-123!'
        assert client.post('/api/auth/universities', headers=headers, json=payload).status_code == 201
        duplicate = {**payload, 'name': 'Duplicate Admin University'}
        assert client.post('/api/auth/universities', headers=headers, json=duplicate).status_code == 400
        assert 'Duplicate Admin University' not in {u['name'] for u in client.get('/api/auth/universities', headers=headers).json()}


def test_schema_compiles_for_sql_server_and_allows_optional_student_ids():
    dialect = mssql.dialect()
    for table in Base.metadata.sorted_tables:
        str(CreateTable(table).compile(dialect=dialect))
    index = next(i for i in Applicant.__table__.indexes if i.name == 'ix_applicants_student_unique_id')
    ddl = str(CreateIndex(index).compile(dialect=dialect))
    assert 'WHERE student_unique_id IS NOT NULL' in ddl
