from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

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
        if not connection_string:
            raise StorageConfigurationError("Azure Blob Storage connection string is not configured.")
        try:
            from azure.storage.blob import BlobServiceClient
        except ImportError as exc:
            raise StorageConfigurationError("azure-storage-blob is required for Azure Blob Storage.") from exc
        return BlobServiceClient.from_connection_string(connection_string)


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
    raise ValueError(f"Unsupported storage backend: {settings.storage_backend}")
