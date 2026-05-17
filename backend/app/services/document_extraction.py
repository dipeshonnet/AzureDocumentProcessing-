from __future__ import annotations

import logging
from io import BytesIO
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Protocol

from sqlalchemy.orm import Session

from app.config import AppSettings
from app.models import ApplicationDocument, ExtractedDocumentContent
from app.services.storage import StorageError, StorageNotFoundError, get_storage_service
from app.services.storage import file_extension


logger = logging.getLogger(__name__)

SUPPORTED_EXTRACTION_EXTENSIONS: frozenset[str] = frozenset(
    {"pdf", "docx", "jpg", "jpeg", "png"}
)

CONTENT_TYPES_BY_EXTENSION: dict[str, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
}


class DocumentExtractionError(Exception):
    """Base error for document extraction failures."""


class DocumentNotFoundError(DocumentExtractionError):
    pass


class UnsupportedDocumentTypeError(DocumentExtractionError):
    pass


class AzureDocumentExtractionError(DocumentExtractionError):
    pass


class ExtractionTimeoutError(DocumentExtractionError):
    pass


class EmptyOcrResultError(DocumentExtractionError):
    pass


@dataclass(frozen=True)
class ExtractionResult:
    raw_text: str
    pages: list[dict] = field(default_factory=list)
    tables: list[dict] = field(default_factory=list)
    key_value_pairs: dict = field(default_factory=dict)
    extraction_confidence: float | None = None
    extraction_metadata: dict = field(default_factory=dict)


class DocumentExtractionService(Protocol):
    def extract_document(self, document_id: str) -> ExtractedDocumentContent:
        raise NotImplementedError

    def extract_from_file(
        self,
        path_or_stream: str | Path | BinaryIO,
        content_type: str,
    ) -> ExtractionResult:
        raise NotImplementedError

    def extract_from_blob(self, blob_path: str) -> ExtractionResult:
        raise NotImplementedError


class BaseDocumentExtractionService:
    def __init__(self, *, db: Session, settings: AppSettings) -> None:
        self.db = db
        self.settings = settings

    def extract_document(self, document_id: str) -> ExtractedDocumentContent:
        document = self.db.get(ApplicationDocument, document_id)
        if document is None:
            raise DocumentNotFoundError("Document not found.")

        extension = file_extension(document.original_filename)
        if extension not in SUPPORTED_EXTRACTION_EXTENSIONS:
            raise UnsupportedDocumentTypeError(f"Unsupported document type: {extension or 'unknown'}.")

        content_type = CONTENT_TYPES_BY_EXTENSION[extension]
        try:
            content = get_storage_service(self.settings).read_bytes(
                storage_path=document.blob_url_or_path,
            )
        except StorageNotFoundError as exc:
            raise DocumentNotFoundError("Stored document file was not found.") from exc
        except StorageError as exc:
            raise AzureDocumentExtractionError("Stored document file could not be read.") from exc

        result = self.extract_from_file(BytesIO(content), content_type)

        return _result_to_model(document_id=document_id, result=result)


class MockDocumentExtractionService(BaseDocumentExtractionService):
    def __init__(
        self,
        *,
        db: Session,
        settings: AppSettings,
        result: ExtractionResult | None = None,
        error: DocumentExtractionError | None = None,
    ) -> None:
        super().__init__(db=db, settings=settings)
        self.result = result or ExtractionResult(
            raw_text="Mock extracted document text.",
            pages=[
                {
                    "page_number": 1,
                    "text": "Mock extracted document text.",
                    "lines": [{"content": "Mock extracted document text."}],
                    "words": [],
                }
            ],
            tables=[],
            key_value_pairs={"values": {}, "pairs": []},
            extraction_confidence=0.99,
            extraction_metadata={
                "provider": "mock",
                "model_id": "mock-prebuilt-layout",
                "page_count": 1,
            },
        )
        self.error = error

    def extract_from_file(
        self,
        path_or_stream: str | Path | BinaryIO,
        content_type: str,
    ) -> ExtractionResult:
        if self.error:
            raise self.error
        return self.result

    def extract_from_blob(self, blob_path: str) -> ExtractionResult:
        if self.error:
            raise self.error
        return self.result


class AzureDocumentIntelligenceExtractionService(BaseDocumentExtractionService):
    def __init__(self, *, db: Session, settings: AppSettings) -> None:
        super().__init__(db=db, settings=settings)
        if not settings.azure_document_intelligence_endpoint:
            raise AzureDocumentExtractionError("Azure Document Intelligence endpoint is not configured.")
        if not settings.azure_document_intelligence_key:
            raise AzureDocumentExtractionError("Azure Document Intelligence key is not configured.")

        from azure.ai.documentintelligence import DocumentIntelligenceClient
        from azure.core.credentials import AzureKeyCredential

        self.client = DocumentIntelligenceClient(
            endpoint=settings.azure_document_intelligence_endpoint,
            credential=AzureKeyCredential(settings.azure_document_intelligence_key),
        )

    def extract_from_file(
        self,
        path_or_stream: str | Path | BinaryIO,
        content_type: str,
    ) -> ExtractionResult:
        try:
            if isinstance(path_or_stream, str | Path):
                with Path(path_or_stream).open("rb") as stream:
                    return self._analyze_stream(stream, content_type)
            return self._analyze_stream(path_or_stream, content_type)
        except FileNotFoundError as exc:
            raise DocumentNotFoundError("Stored document file was not found.") from exc

    def extract_from_blob(self, blob_path: str) -> ExtractionResult:
        if not blob_path.lower().startswith(("http://", "https://")):
            raise AzureDocumentExtractionError(
                "Azure blob extraction requires a service-accessible blob URL."
            )
        try:
            poller = self.client.begin_analyze_document(
                "prebuilt-layout",
                body={"urlSource": blob_path},
                features=self._document_features(),
            )
            result = poller.result(timeout=self.settings.document_extraction_timeout_seconds)
            return _parse_analyze_result(result)
        except TimeoutError as exc:
            raise ExtractionTimeoutError("Document extraction timed out.") from exc
        except Exception as exc:
            if _is_azure_exception(exc):
                raise AzureDocumentExtractionError("Azure Document Intelligence extraction failed.") from exc
            raise

    def _analyze_stream(self, stream: BinaryIO, content_type: str) -> ExtractionResult:
        try:
            poller = self.client.begin_analyze_document(
                "prebuilt-layout",
                body=stream,
                content_type=content_type,
                features=self._document_features(),
            )
            result = poller.result(timeout=self.settings.document_extraction_timeout_seconds)
            return _parse_analyze_result(result)
        except TimeoutError as exc:
            raise ExtractionTimeoutError("Document extraction timed out.") from exc
        except Exception as exc:
            if _is_azure_exception(exc):
                raise AzureDocumentExtractionError("Azure Document Intelligence extraction failed.") from exc
            raise

    @staticmethod
    def _document_features() -> list:
        try:
            from azure.ai.documentintelligence.models import DocumentAnalysisFeature

            return [DocumentAnalysisFeature.KEY_VALUE_PAIRS]
        except Exception:
            return []


def get_document_extraction_service(
    *,
    db: Session,
    settings: AppSettings,
) -> DocumentExtractionService:
    backend = settings.document_extraction_backend.strip().lower()
    if backend == "mock":
        return MockDocumentExtractionService(db=db, settings=settings)
    if backend == "azure":
        return AzureDocumentIntelligenceExtractionService(db=db, settings=settings)
    raise AzureDocumentExtractionError(f"Unsupported document extraction backend: {backend}.")


def _is_azure_exception(exc: Exception) -> bool:
    try:
        from azure.core.exceptions import AzureError

        return isinstance(exc, AzureError)
    except Exception:
        return False


def _result_to_model(document_id: str, result: ExtractionResult) -> ExtractedDocumentContent:
    _validate_non_empty_result(result)
    return ExtractedDocumentContent(
        document_id=document_id,
        raw_text=result.raw_text,
        pages=result.pages,
        tables=result.tables,
        key_value_pairs=result.key_value_pairs,
        extraction_confidence=result.extraction_confidence,
        extraction_metadata=result.extraction_metadata,
    )


def _validate_non_empty_result(result: ExtractionResult) -> None:
    if not result.raw_text.strip() and not result.pages:
        raise EmptyOcrResultError("Document extraction returned no OCR content.")


def _parse_analyze_result(result: object) -> ExtractionResult:
    raw_text = str(getattr(result, "content", "") or "")
    pages = _parse_pages(getattr(result, "pages", None) or [])
    tables = _parse_tables(getattr(result, "tables", None) or [])
    key_value_pairs = _parse_key_value_pairs(getattr(result, "key_value_pairs", None) or [])
    word_confidences = [
        word["confidence"]
        for page in pages
        for word in page.get("words", [])
        if isinstance(word.get("confidence"), int | float)
    ]
    extraction_confidence = (
        sum(word_confidences) / len(word_confidences) if word_confidences else None
    )
    metadata = {
        "provider": "azure_document_intelligence",
        "model_id": getattr(result, "model_id", None),
        "api_version": getattr(result, "api_version", None),
        "page_count": len(pages),
        "confidence": {
            "average_word_confidence": extraction_confidence,
            "word_confidence_count": len(word_confidences),
        },
    }

    parsed = ExtractionResult(
        raw_text=raw_text,
        pages=pages,
        tables=tables,
        key_value_pairs=key_value_pairs,
        extraction_confidence=extraction_confidence,
        extraction_metadata=metadata,
    )
    _validate_non_empty_result(parsed)
    return parsed


def _parse_pages(pages: list) -> list[dict]:
    parsed_pages = []
    for page in pages:
        lines = [
            {
                "content": getattr(line, "content", ""),
                "polygon": _jsonable(getattr(line, "polygon", None)),
                "spans": _jsonable(getattr(line, "spans", None)),
            }
            for line in (getattr(page, "lines", None) or [])
        ]
        words = [
            {
                "content": getattr(word, "content", ""),
                "confidence": getattr(word, "confidence", None),
                "polygon": _jsonable(getattr(word, "polygon", None)),
                "span": _jsonable(getattr(word, "span", None)),
            }
            for word in (getattr(page, "words", None) or [])
        ]
        parsed_pages.append(
            {
                "page_number": getattr(page, "page_number", None),
                "width": getattr(page, "width", None),
                "height": getattr(page, "height", None),
                "unit": getattr(page, "unit", None),
                "text": "\n".join(line["content"] for line in lines if line["content"]),
                "lines": lines,
                "words": words,
            }
        )
    return parsed_pages


def _parse_tables(tables: list) -> list[dict]:
    parsed_tables = []
    for table in tables:
        parsed_tables.append(
            {
                "row_count": getattr(table, "row_count", None),
                "column_count": getattr(table, "column_count", None),
                "bounding_regions": _jsonable(getattr(table, "bounding_regions", None)),
                "cells": [
                    {
                        "content": getattr(cell, "content", ""),
                        "row_index": getattr(cell, "row_index", None),
                        "column_index": getattr(cell, "column_index", None),
                        "row_span": getattr(cell, "row_span", None),
                        "column_span": getattr(cell, "column_span", None),
                        "kind": getattr(cell, "kind", None),
                        "bounding_regions": _jsonable(getattr(cell, "bounding_regions", None)),
                    }
                    for cell in (getattr(table, "cells", None) or [])
                ],
            }
        )
    return parsed_tables


def _parse_key_value_pairs(pairs: list) -> dict:
    values = {}
    parsed_pairs = []
    for pair in pairs:
        key = getattr(getattr(pair, "key", None), "content", None)
        value = getattr(getattr(pair, "value", None), "content", None)
        if key:
            values[key] = value
        parsed_pairs.append(
            {
                "key": key,
                "value": value,
                "confidence": getattr(pair, "confidence", None),
                "key_bounding_regions": _jsonable(
                    getattr(getattr(pair, "key", None), "bounding_regions", None)
                ),
                "value_bounding_regions": _jsonable(
                    getattr(getattr(pair, "value", None), "bounding_regions", None)
                ),
            }
        )
    return {"values": values, "pairs": parsed_pairs}


def _jsonable(value: object) -> object:
    if value is None:
        return None
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value
