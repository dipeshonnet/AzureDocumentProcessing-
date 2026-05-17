from __future__ import annotations

import pytest

from app.config import AppSettings
from app.services.storage import AzureBlobStorageService, StorageNotFoundError, StoragePathError


class FakeDownload:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def readall(self) -> bytes:
        return self.content


class FakeBlobClient:
    def __init__(self, blobs: dict[str, bytes], blob_name: str) -> None:
        self.blobs = blobs
        self.blob_name = blob_name

    def upload_blob(self, content: bytes, *, overwrite: bool) -> None:
        assert overwrite is True
        self.blobs[self.blob_name] = content

    def download_blob(self) -> FakeDownload:
        if self.blob_name not in self.blobs:
            raise ResourceNotFoundError("missing")
        return FakeDownload(self.blobs[self.blob_name])


class FakeBlobServiceClient:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.requests: list[tuple[str, str]] = []

    def get_blob_client(self, *, container: str, blob: str) -> FakeBlobClient:
        self.requests.append((container, blob))
        return FakeBlobClient(self.blobs, blob)


class ResourceNotFoundError(Exception):
    pass


def blob_settings() -> AppSettings:
    return AppSettings.from_mapping(
        {
            "AZURE_STORAGE_ACCOUNT_NAME": "admissiondocstorage1803",
            "AZURE_STORAGE_CONTAINER_DOCUMENTS": "admissions-raw",
            "AZURE_STORAGE_CONNECTION_STRING": "UseDevelopmentStorage=true",
        }
    )


def test_azure_blob_storage_saves_and_reads_private_blob_bytes() -> None:
    fake_client = FakeBlobServiceClient()
    storage = AzureBlobStorageService(blob_settings(), blob_service_client=fake_client)

    storage_path = "applications/app/raw/doc/transcript.pdf"
    saved_path = storage.save_bytes(storage_path=storage_path, content=b"private document")

    assert saved_path == storage_path
    assert storage.read_bytes(storage_path=storage_path) == b"private document"
    assert fake_client.requests == [
        ("admissions-raw", storage_path),
        ("admissions-raw", storage_path),
    ]


@pytest.mark.parametrize("storage_path", ["", "/absolute.pdf", "folder//file.pdf", "../secret.pdf"])
def test_azure_blob_storage_rejects_unsafe_blob_paths(storage_path: str) -> None:
    storage = AzureBlobStorageService(blob_settings(), blob_service_client=FakeBlobServiceClient())

    with pytest.raises(StoragePathError):
        storage.save_bytes(storage_path=storage_path, content=b"private document")


def test_azure_blob_storage_maps_missing_blob() -> None:
    storage = AzureBlobStorageService(blob_settings(), blob_service_client=FakeBlobServiceClient())

    with pytest.raises(StorageNotFoundError):
        storage.read_bytes(storage_path="applications/app/raw/missing/transcript.pdf")
