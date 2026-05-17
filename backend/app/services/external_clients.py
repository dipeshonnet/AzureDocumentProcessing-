from __future__ import annotations

from typing import Protocol


class BlobStorageClient(Protocol):
    """Boundary for future Azure Blob Storage integration."""

    async def upload_document(self, *, filename: str, content: bytes) -> str:
        raise NotImplementedError


class DocumentIntelligenceClient(Protocol):
    """Boundary for future Azure AI Document Intelligence integration."""

    async def analyze_document(self, *, blob_uri: str) -> dict:
        raise NotImplementedError


class AdmissionsAiClient(Protocol):
    """Boundary for future Azure OpenAI or Azure AI Foundry integration."""

    async def summarize_for_review(self, *, extracted_payload: dict) -> dict:
        raise NotImplementedError
