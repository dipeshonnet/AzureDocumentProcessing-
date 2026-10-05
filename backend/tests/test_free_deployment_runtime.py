import asyncio
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.config import AppSettings, get_settings
from app.main import create_app
from app.services import intake_jobs


def test_idle_worker_never_queries_sql_and_wakes_on_upload(monkeypatch):
    async def run():
        queue = asyncio.Queue()
        recovered = MagicMock()
        processed = []
        async def process(**kwargs):
            processed.append(kwargs['job_id'])
        monkeypatch.setattr(intake_jobs, 'enqueue_queued_jobs', recovered)
        monkeypatch.setattr(intake_jobs, 'process_intake_job', process)
        factory = MagicMock()
        task = asyncio.create_task(intake_jobs.intake_worker_loop(
            queue=queue, session_factory=factory,
            settings=AppSettings.from_mapping({'INTAKE_QUEUE_WATCHDOG_SECONDS': '0'})))
        try:
            await asyncio.sleep(0.02)
            recovered.assert_not_called()
            factory.assert_not_called()
            queue.put_nowait('uploaded-job')
            await asyncio.wait_for(queue.join(), timeout=1)
            assert processed == ['uploaded-job']
            recovered.assert_not_called()
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    asyncio.run(run())


def test_watchdog_recovery_remains_available_when_explicitly_enabled(monkeypatch):
    async def run():
        recovered = MagicMock()
        monkeypatch.setattr(intake_jobs, 'enqueue_queued_jobs', recovered)
        task = asyncio.create_task(intake_jobs.intake_worker_loop(
            queue=asyncio.Queue(), session_factory=MagicMock(),
            settings=AppSettings.from_mapping({'INTAKE_QUEUE_WATCHDOG_SECONDS': '0.005'})))
        try:
            await asyncio.sleep(0.03)
            assert recovered.called
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    asyncio.run(run())


def test_frontend_cors_allows_workspace_preflight_only_on_configured_domains(monkeypatch):
    monkeypatch.setenv('BACKEND_CORS_ORIGINS', 'https://admissions.everydayai.work,https://preview.azurestaticapps.net')
    monkeypatch.setenv('INTAKE_WORKER_ENABLED', 'false')
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            for origin in ['https://admissions.everydayai.work', 'https://preview.azurestaticapps.net']:
                response = client.options('/api/ops/dashboard', headers={
                    'Origin': origin, 'Access-Control-Request-Method': 'POST',
                    'Access-Control-Request-Headers': 'authorization,x-workspace-id,content-type'})
                assert response.status_code == 200
                assert response.headers['access-control-allow-origin'] == origin
            response = client.options('/api/ops/dashboard', headers={
                'Origin': 'https://untrusted.example', 'Access-Control-Request-Method': 'POST'})
            assert response.status_code == 400
            assert 'access-control-allow-origin' not in response.headers
    finally:
        get_settings.cache_clear()
