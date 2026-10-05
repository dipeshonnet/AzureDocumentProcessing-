from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, urlsplit

import httpx

from app.config import AppSettings


ALLOWED_UPLOAD_EXTENSIONS: frozenset[str] = frozenset(
    {"pdf", "docx", "txt", "jpg", "jpeg", "png"}
)


class StorageService(Protocol):
    def save_bytes(self, *, storage_path: str, content: bytes) -> str:
        raise NotImplementedError

    def read_bytes(self, *, storage_path: str) -> bytes:
        raise NotImplementedError


class StorageError(Exception):
    """Base error for configured document storage failures."""


class StorageConfigurationError(StorageError, ValueError):
    pass


class StoragePathError(StorageError, ValueError):
    pass


class StorageNotFoundError(StorageError):
    pass


class StorageOperationError(StorageError):
    pass


def safe_filename(filename: str) -> str:
    raw_name = Path(filename.replace("\\", "/")).name.strip()
    if not raw_name:
        raw_name = "document"

    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", raw_name)
    sanitized = sanitized.strip("._")
    if not sanitized:
        sanitized = "document"

    stem = Path(sanitized).stem[:100] or "document"
    suffix = Path(sanitized).suffix.lower()
    return f"{stem}{suffix}"


def file_extension(filename: str) -> str:
    return Path(filename).suffix.lower().lstrip(".")


def build_document_storage_path(
    *,
    application_id: str,
    document_id: str,
    filename: str,
) -> str:
    return f"applications/{application_id}/raw/{document_id}/{safe_filename(filename)}"


class LocalStorageService:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def save_bytes(self, *, storage_path: str, content: bytes) -> str:
        destination = self._resolved_path(storage_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        return storage_path

    def read_bytes(self, *, storage_path: str) -> bytes:
        destination = self._resolved_path(storage_path)
        try:
            return destination.read_bytes()
        except FileNotFoundError as exc:
            raise StorageNotFoundError("Stored document file was not found.") from exc

    def _resolved_path(self, storage_path: str) -> Path:
        root = self.root.resolve()
        destination = (root / storage_path).resolve()
        if not destination.is_relative_to(root):
            raise StoragePathError("Storage path resolves outside the configured local storage root.")
        return destination


class AzureBlobStorageService:
    """Azure Blob Storage adapter for private admissions document uploads."""

    def __init__(self, settings: AppSettings, blob_service_client: object | None = None) -> None:
        self.account_name = settings.azure_storage_account_name
        self.container_name = settings.azure_storage_container_documents
        if not self.container_name:
            raise StorageConfigurationError("Azure Blob Storage container is not configured.")
        self.client = blob_service_client or self._create_blob_service_client(settings)

    def save_bytes(self, *, storage_path: str, content: bytes) -> str:
        blob_name = validate_blob_path(storage_path)
        try:
            blob_client = self.client.get_blob_client(container=self.container_name, blob=blob_name)
            blob_client.upload_blob(content, overwrite=True)
        except Exception as exc:
            if isinstance(exc, StorageError):
                raise
            raise StorageOperationError("Azure Blob Storage upload failed.") from exc
        return blob_name

    def read_bytes(self, *, storage_path: str) -> bytes:
        blob_name = validate_blob_path(storage_path)
        try:
            blob_client = self.client.get_blob_client(container=self.container_name, blob=blob_name)
            return bytes(blob_client.download_blob().readall())
        except Exception as exc:
            if _looks_like_resource_not_found(exc):
                raise StorageNotFoundError("Stored Azure Blob document was not found.") from exc
            if isinstance(exc, StorageError):
                raise
            raise StorageOperationError("Azure Blob Storage download failed.") from exc

    @staticmethod
    def _create_blob_service_client(settings: AppSettings) -> object:
        connection_string = settings.azure_storage_connection_string
        if not connection_string and not settings.azure_storage_account_name:
            raise StorageConfigurationError("Azure Blob Storage connection string is not configured.")
        try:
            from azure.storage.blob import BlobServiceClient
        except ImportError as exc:
            raise StorageConfigurationError("azure-storage-blob is required for Azure Blob Storage.") from exc
        if connection_string:
            return BlobServiceClient.from_connection_string(connection_string)
        from azure.identity import DefaultAzureCredential
        return BlobServiceClient(
            account_url=f"https://{settings.azure_storage_account_name}.blob.core.windows.net",
            credential=DefaultAzureCredential(),
        )


def validate_blob_path(storage_path: str) -> str:
    value = storage_path.replace("\\", "/").strip()
    parts = value.split("/")
    if (
        not value
        or value.startswith("/")
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise StoragePathError("Storage path is not a valid private blob name.")
    return value


def _looks_like_resource_not_found(exc: Exception) -> bool:
    return exc.__class__.__name__ in {"ResourceNotFoundError", "ResourceNotFound"}


def get_storage_service(settings: AppSettings) -> StorageService:
    backend = settings.storage_backend.strip().lower()
    if backend == "local":
        return LocalStorageService(settings.local_storage_root)
    if backend == "azure":
        return AzureBlobStorageService(settings)
    if backend == "supabase":
        return SupabaseStorageService(settings)
    raise ValueError(f"Unsupported storage backend: {settings.storage_backend}")


class SupabaseStorageService:
    """Server-only access to a private bucket. Keys never reach the browser."""

    def __init__(self, settings: AppSettings):
        url = (settings.supabase_url or '').rstrip('/')
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
                or parsed.path or parsed.query or parsed.fragment or not settings.supabase_service_key):
            raise StorageConfigurationError('Configure an HTTPS SUPABASE_URL and server-only SUPABASE_SERVICE_KEY.')
        self.url = url + '/storage/v1/object'
        self.bucket = quote(validate_blob_path(settings.supabase_storage_bucket), safe='')
        self.headers = {'apikey': settings.supabase_service_key}
        if not settings.supabase_service_key.startswith('sb_secret_'):
            self.headers['Authorization'] = 'Bearer ' + settings.supabase_service_key

    def save_bytes(self, *, storage_path: str, content: bytes) -> str:
        path = validate_blob_path(storage_path)
        self._request('POST', f'{self.url}/{self.bucket}/{quote(path, safe="/")}',
                      content=content, headers={**self.headers, 'Content-Type': 'application/octet-stream', 'x-upsert': 'true'})
        return path

    def read_bytes(self, *, storage_path: str) -> bytes:
        path = validate_blob_path(storage_path)
        return self._request('GET', f'{self.url}/authenticated/{self.bucket}/{quote(path, safe="/")}',
                             headers=self.headers).content

    @staticmethod
    def _request(method: str, url: str, **kwargs) -> httpx.Response:
        try:
            response = httpx.request(method, url, timeout=60, follow_redirects=False, **kwargs)
            if response.status_code == 404:
                raise StorageNotFoundError('Stored Supabase document was not found.')
            if not response.is_success:
                raise StorageOperationError('Supabase storage request failed. Check the bucket, credentials and free quota.')
            return response
        except httpx.HTTPError as exc:
            raise StorageOperationError('Supabase storage could not be reached.') from exc
