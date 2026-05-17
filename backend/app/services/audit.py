from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog
from app.security import AuthenticatedActor, system_actor


SECRET_KEY_PARTS = ("secret", "key", "token", "password", "credential", "connection_string")
DOCUMENT_TEXT_KEYS = {
    "raw_text",
    "text",
    "content",
    "pages",
    "tables",
    "key_value_pairs",
    "evidence_snippets",
    "supporting_evidence",
}


class AuditAction:
    DOCUMENT_UPLOAD = "document_upload"
    DOCUMENT_EXTRACTION = "document_extraction"
    DOCUMENT_CLASSIFICATION = "document_classification"
    DOCUMENT_STRUCTURED_EXTRACTION = "document_structured_extraction"
    DOCUMENT_SUMMARIZATION = "document_summarization"
    APPLICATION_SCORING = "application_scoring"
    SCORE_OVERRIDE = "score_override"
    FINAL_DECISION = "final_decision"
    INTAKE_PARSE = "intake_parse"
    EXTRACTED_RECORD_DOWNLOAD = "extracted_record_download"
    RUBRIC_SAVE = "rubric_save"


def record_audit_log(
    *,
    db: Session,
    actor: AuthenticatedActor | None,
    action: str,
    application_id: str | None = None,
    document_id: str | None = None,
    old_value: Mapping[str, Any] | None = None,
    new_value: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> AuditLog:
    safe_actor = actor or system_actor()
    log = AuditLog(
        actor=safe_actor.actor_id,
        actor_role=safe_actor.role.value,
        action=action,
        application_id=application_id,
        document_id=document_id,
        old_value=sanitize_audit_value(old_value) if old_value is not None else None,
        new_value=sanitize_audit_value(new_value) if new_value is not None else None,
        metadata_=sanitize_audit_value(metadata or {}),
    )
    db.add(log)
    return log


def sanitize_audit_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if any(part in lowered for part in SECRET_KEY_PARTS):
                sanitized[key_text] = "[redacted_secret]"
            elif lowered in DOCUMENT_TEXT_KEYS:
                sanitized[key_text] = "[redacted_document_content]"
            else:
                sanitized[key_text] = sanitize_audit_value(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_audit_value(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_audit_value(item) for item in value]
    return value
