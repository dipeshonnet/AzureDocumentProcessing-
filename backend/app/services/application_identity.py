from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from app.config import AppSettings
from app.models import Application, ApplicationDocument, ExtractedDocumentContent
from app.services.audit import AuditAction, record_audit_log
from app.services.azure_openai import chat_completion_kwargs, create_azure_openai_chat_client
from app.security import system_actor


logger = logging.getLogger(__name__)

PENDING_APPLICANT_FIRST_NAME = "Pending"
PENDING_APPLICANT_LAST_NAME = "extraction"
PENDING_PROGRAM_APPLIED = "Pending extraction"


class ParsedApplicationIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applicant_name: str | None = Field(...)
    program_applied: str | None = Field(...)
    evidence: list[str] = Field(...)
    uncertain: bool = Field(...)


@dataclass(frozen=True)
class IdentityDerivationPayload:
    text: str
    structured_extractions: list[dict[str, Any]] = field(default_factory=list)


def apply_derived_application_identity(
    *,
    db: Session,
    application: Application,
    documents: list[ApplicationDocument],
    settings: AppSettings,
) -> ParsedApplicationIdentity:
    payload = build_identity_payload(db=db, documents=documents)
    identity = derive_application_identity(payload=payload, settings=settings)
    applicant = application.applicant
    old_value = {
        "name": f"{applicant.first_name} {applicant.last_name}".strip(),
        "program_applied": application.program_applied or applicant.program_applied,
    }

    changed = False
    applicant_name = clean_identity_value(identity.applicant_name)
    if applicant_name and should_replace_name(old_value["name"]):
        applicant.first_name, applicant.last_name = split_applicant_name(applicant_name)
        changed = True

    program_applied = clean_identity_value(identity.program_applied)
    if program_applied and should_replace_program(application.program_applied or applicant.program_applied):
        application.program_applied = program_applied
        applicant.program_applied = program_applied
        changed = True

    if changed:
        record_audit_log(
            db=db,
            actor=system_actor(),
            action=AuditAction.APPLICATION_IDENTITY_DERIVED,
            application_id=application.application_id,
            old_value=old_value,
            new_value={
                "name": f"{applicant.first_name} {applicant.last_name}".strip(),
                "program_applied": application.program_applied or applicant.program_applied,
                "evidence_count": len(identity.evidence),
                "uncertain": identity.uncertain,
            },
        )
    return identity


def build_identity_payload(*, db: Session, documents: list[ApplicationDocument]) -> IdentityDerivationPayload:
    text_parts: list[str] = []
    structured_extractions: list[dict[str, Any]] = []
    for document in documents:
        extracted = db.get(ExtractedDocumentContent, document.document_id)
        if extracted is None:
            continue
        text = text_from_extracted_content(extracted)
        if text:
            text_parts.append(f"Document: {document.original_filename}\n{text}")
        if extracted.structured_extraction:
            structured_extractions.append(extracted.structured_extraction)
    return IdentityDerivationPayload(
        text="\n\n".join(text_parts)[:12000],
        structured_extractions=structured_extractions,
    )


def derive_application_identity(
    *,
    payload: IdentityDerivationPayload,
    settings: AppSettings,
) -> ParsedApplicationIdentity:
    heuristic_identity = derive_identity_with_rules(payload)
    if heuristic_identity.applicant_name and heuristic_identity.program_applied:
        return heuristic_identity

    azure_identity = derive_identity_with_azure_openai(payload=payload, settings=settings)
    if azure_identity is None:
        return heuristic_identity

    return ParsedApplicationIdentity(
        applicant_name=azure_identity.applicant_name or heuristic_identity.applicant_name,
        program_applied=azure_identity.program_applied or heuristic_identity.program_applied,
        evidence=azure_identity.evidence or heuristic_identity.evidence,
        uncertain=azure_identity.uncertain and heuristic_identity.uncertain,
    )


def derive_identity_with_azure_openai(
    *,
    payload: IdentityDerivationPayload,
    settings: AppSettings,
) -> ParsedApplicationIdentity | None:
    deployment = settings.azure_openai_review_model_deployment or settings.azure_openai_structured_extraction_model_deployment
    if not (
        payload.text.strip()
        and settings.azure_openai_endpoint
        and settings.azure_openai_api_key
        and settings.azure_openai_api_version
        and deployment
    ):
        return None

    try:
        client = create_azure_openai_chat_client(settings)
        response = client.chat.completions.create(
            **chat_completion_kwargs(
                model=deployment,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Extract only the applicant identity and intended/applied program from admissions document text. "
                            "Use null when the value is absent or ambiguous. Never invent values."
                        ),
                    },
                    {
                        "role": "user",
                        "content": build_identity_prompt(payload),
                    },
                ],
                response_format=identity_response_format(),
                settings=settings,
            )
        )
        return ParsedApplicationIdentity.model_validate_json(response.choices[0].message.content or "{}")
    except (ValidationError, IndexError, AttributeError) as exc:
        logger.warning("Azure OpenAI returned invalid applicant identity JSON: %s", exc.__class__.__name__)
        return None
    except Exception as exc:
        logger.warning("Azure OpenAI applicant identity extraction failed: %s", exc.__class__.__name__)
        return None


def build_identity_prompt(payload: IdentityDerivationPayload) -> str:
    structured = json.dumps(payload.structured_extractions, ensure_ascii=True)[:5000]
    return (
        "Return the applicant_name and program_applied found in the supplied OCR text and structured extraction. "
        "Prefer explicit labels such as Applicant Name, Student Name, Program, Major, Degree, Intended Program, or Program Applied. "
        "Return null for missing or inferred-only values.\n\n"
        f"Structured extraction JSON:\n{structured}\n\n"
        f"OCR text:\n{payload.text[:12000]}"
    )


def identity_response_format() -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "ParsedApplicationIdentity",
            "strict": True,
            "schema": ParsedApplicationIdentity.model_json_schema(),
        },
    }


def derive_identity_with_rules(payload: IdentityDerivationPayload) -> ParsedApplicationIdentity:
    applicant_name = find_labeled_value(
        payload.text,
        labels=[
            "Applicant Name",
            "Student Name",
            "Candidate Name",
            "Full Name",
            "Name",
        ],
        max_length=100,
    )
    program_applied = find_labeled_value(
        payload.text,
        labels=[
            "Program Applied",
            "Applied Program",
            "Intended Program",
            "Program",
            "Degree Program",
            "Major",
        ],
        max_length=140,
    ) or program_from_structured_extractions(payload.structured_extractions)

    evidence = []
    if applicant_name:
        evidence.append("Applicant name found in parsed document text.")
    if program_applied:
        evidence.append("Program found in parsed document text or structured extraction.")
    return ParsedApplicationIdentity(
        applicant_name=applicant_name,
        program_applied=program_applied,
        evidence=evidence,
        uncertain=not (applicant_name and program_applied),
    )


def find_labeled_value(text: str, *, labels: list[str], max_length: int) -> str | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for label in labels:
        escaped = re.escape(label)
        pattern = re.compile(rf"^{escaped}\s*[:\-]\s*(?P<value>.+)$", re.IGNORECASE)
        for line in lines:
            match = pattern.match(line)
            if not match:
                continue
            value = clean_identity_value(match.group("value"))
            if value and len(value) <= max_length:
                return value
    return None


def program_from_structured_extractions(structured_extractions: list[dict[str, Any]]) -> str | None:
    for structured in structured_extractions:
        extraction = structured.get("extraction")
        if not isinstance(extraction, dict):
            continue
        degree = field_value(extraction.get("degree"))
        major = field_value(extraction.get("major"))
        if degree and major and major.lower() not in degree.lower():
            return f"{degree} {major}"
        if major:
            return major
        if degree:
            return degree
    return None


def field_value(value: Any) -> str | None:
    if isinstance(value, dict):
        return clean_identity_value(value.get("value"))
    return clean_identity_value(value)


def text_from_extracted_content(extracted: ExtractedDocumentContent) -> str:
    page_text = "\n".join(
        str(page.get("text") or "").strip()
        for page in (extracted.pages or [])[:5]
        if str(page.get("text") or "").strip()
    )
    return page_text or (extracted.raw_text or "").strip()


def clean_identity_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = re.sub(r"\s+", " ", value).strip(" \t\r\n:;-")
    if not cleaned:
        return None
    return cleaned[:160]


def split_applicant_name(name: str) -> tuple[str, str]:
    parts = [part for part in name.split() if part]
    if not parts:
        return PENDING_APPLICANT_FIRST_NAME, PENDING_APPLICANT_LAST_NAME
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def should_replace_name(name: str) -> bool:
    return name.strip().lower() in {
        "",
        PENDING_APPLICANT_FIRST_NAME.lower(),
        f"{PENDING_APPLICANT_FIRST_NAME} {PENDING_APPLICANT_LAST_NAME}".lower(),
    }


def should_replace_program(program_applied: str) -> bool:
    return program_applied.strip().lower() in {"", PENDING_PROGRAM_APPLIED.lower()}
