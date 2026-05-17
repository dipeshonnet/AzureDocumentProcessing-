from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.config import AppSettings


class PiiRedactionError(Exception):
    """Base error for PII redaction failures."""


class PiiRedactionServiceNotConfiguredError(PiiRedactionError):
    pass


@dataclass(frozen=True)
class PiiRedactionResult:
    redacted_text: str
    entities: list[dict] = field(default_factory=list)
    provider: str = "none"


class PiiRedactionService(Protocol):
    def redact_text(self, text: str) -> PiiRedactionResult:
        raise NotImplementedError


class NoopPiiRedactionService:
    def redact_text(self, text: str) -> PiiRedactionResult:
        return PiiRedactionResult(redacted_text=text, entities=[], provider="none")


class AzureLanguagePiiRedactionService:
    """Placeholder for future Azure AI Language PII integration."""

    def __init__(self, *, settings: AppSettings) -> None:
        if not settings.azure_language_endpoint or not settings.azure_language_key:
            raise PiiRedactionServiceNotConfiguredError("Azure Language PII redaction is not configured.")
        self.settings = settings

    def redact_text(self, text: str) -> PiiRedactionResult:
        raise NotImplementedError("Azure Language PII redaction will be implemented in a later integration step.")


def get_pii_redaction_service(settings: AppSettings) -> PiiRedactionService:
    backend = settings.pii_redaction_backend.strip().lower()
    if backend in {"", "none", "noop", "mock"}:
        return NoopPiiRedactionService()
    if backend == "azure_language":
        return AzureLanguagePiiRedactionService(settings=settings)
    raise PiiRedactionServiceNotConfiguredError(f"Unsupported PII redaction backend: {backend}.")
