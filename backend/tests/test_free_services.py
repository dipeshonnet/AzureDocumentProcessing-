from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from pypdf import PdfWriter

from app.config import AppSettings
from app.services.storage import (SupabaseStorageService, StorageConfigurationError,
                                  StorageOperationError, StoragePathError)
from app.services.document_extraction import (AzureDocumentIntelligenceExtractionService,
    AzureDocumentExtractionError, LlamaParseExtractionService)


def test_supabase_private_upload_and_download(monkeypatch):
    calls = []
    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return httpx.Response(200, content=b'private PDF')
    monkeypatch.setattr(httpx, 'request', request)
    storage = SupabaseStorageService(AppSettings.from_mapping({
        'SUPABASE_URL': 'https://example.supabase.co', 'SUPABASE_SERVICE_KEY': 'server-jwt'
    }))
    path = 'applications/123/raw/456/my file.pdf'
    assert storage.save_bytes(storage_path=path, content=b'private PDF') == path
    assert storage.read_bytes(storage_path=path) == b'private PDF'
    assert calls[0][0] == 'POST'
    assert calls[0][1].endswith('/admissions-raw/applications/123/raw/456/my%20file.pdf')
    assert '/object/authenticated/' in calls[1][1]
    assert calls[0][2]['headers']['Authorization'] == 'Bearer server-jwt'
    assert calls[0][2]['follow_redirects'] is False


def test_supabase_rejects_paths_and_does_not_leak_errors(monkeypatch):
    storage = SupabaseStorageService(AppSettings.from_mapping({
        'SUPABASE_URL': 'https://example.supabase.co', 'SUPABASE_SERVICE_KEY': 'sb_secret_hidden'
    }))
    assert 'Authorization' not in storage.headers
    with pytest.raises(StoragePathError):
        storage.read_bytes(storage_path='../other-tenant.pdf')
    monkeypatch.setattr(httpx, 'request', lambda *a, **k: httpx.Response(403, text='sb_secret_hidden'))
    with pytest.raises(StorageOperationError) as error:
        storage.save_bytes(storage_path='document.pdf', content=b'doc')
    assert 'sb_secret_hidden' not in str(error.value)
    with pytest.raises(StorageConfigurationError):
        SupabaseStorageService(AppSettings.from_mapping({'SUPABASE_URL': 'http://example.supabase.co',
                                                       'SUPABASE_SERVICE_KEY': 'secret'}))


def pdf_bytes(pages):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    stream = BytesIO()
    writer.write(stream)
    return stream.getvalue()


@pytest.mark.parametrize('content,mime', [(pdf_bytes(3), 'application/pdf'),
                                         (b'x' * (4 * 1024 * 1024 + 1), 'image/png'),
                                         (b'office', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')],
                         ids=['three-pages', 'oversized-image', 'unsupported-office'])
def test_azure_f0_rejects_truncation_or_oversized_input(content, mime):
    service = AzureDocumentIntelligenceExtractionService.__new__(AzureDocumentIntelligenceExtractionService)
    service.settings = AppSettings.from_mapping({'AZURE_DOCUMENT_INTELLIGENCE_FREE_TIER': 'true'})
    service.client = MagicMock()
    with pytest.raises(AzureDocumentExtractionError):
        service._analyze_stream(BytesIO(content), mime)
    service.client.begin_analyze_document.assert_not_called()


def test_llamaparse_preserves_every_page_and_uses_selected_tier(monkeypatch):
    import llama_cloud
    client = MagicMock()
    client.__enter__.return_value = client
    client.files.create.return_value = SimpleNamespace(id='file-1')
    client.parsing.parse.return_value = SimpleNamespace(markdown=SimpleNamespace(
        pages=[SimpleNamespace(markdown='First page'), SimpleNamespace(markdown='Second page')]))
    monkeypatch.setattr(llama_cloud, 'LlamaCloud', lambda **kwargs: client)
    service = LlamaParseExtractionService(db=None, settings=AppSettings.from_mapping({'LLAMA_CLOUD_API_KEY': 'test-key'}))
    result = service.extract_from_file(BytesIO(pdf_bytes(2)), 'application/pdf')
    assert result.raw_text == 'First page\n\nSecond page'
    assert len(result.pages) == 2
    assert client.parsing.parse.call_args.kwargs['tier'] == 'cost_effective'
    assert client.parsing.parse.call_args.kwargs['timeout'] == 120
    assert client.files.create.call_args.kwargs['purpose'] == 'parse'
    client.files.delete.assert_called_once_with('file-1')


@pytest.mark.parametrize('parse_failure,cleanup_failure', [(True, False), (False, True)])
def test_llamaparse_cleanup_on_failure_and_safe_errors(monkeypatch, caplog, parse_failure, cleanup_failure):
    import llama_cloud
    client = MagicMock()
    client.__enter__.return_value = client
    client.files.create.return_value = SimpleNamespace(id='file-1')
    client.parsing.parse.return_value = SimpleNamespace(markdown=SimpleNamespace(
        pages=[SimpleNamespace(markdown='Parsed text')]))
    if parse_failure:
        client.parsing.parse.side_effect = RuntimeError('provider-secret')
    if cleanup_failure:
        client.files.delete.side_effect = RuntimeError('provider-secret')
    monkeypatch.setattr(llama_cloud, 'LlamaCloud', lambda **kwargs: client)
    service = LlamaParseExtractionService(db=None, settings=AppSettings.from_mapping({'LLAMA_CLOUD_API_KEY': 'test-key'}))
    if parse_failure:
        with pytest.raises(AzureDocumentExtractionError) as error:
            service.extract_from_file(BytesIO(pdf_bytes(3)), 'application/pdf')
        assert 'provider-secret' not in str(error.value)
    else:
        assert service.extract_from_file(BytesIO(pdf_bytes(3)), 'application/pdf').raw_text == 'Parsed text'
    client.files.delete.assert_called_once_with('file-1')
    assert 'provider-secret' not in caplog.text
