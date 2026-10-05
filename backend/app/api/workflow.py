from __future__ import annotations

import csv
import hashlib
import io
import math
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.applications import storage_service_dependency
from app.config import AppSettings, get_settings
from app.db.dependencies import get_db
from app.models import Applicant, Application, ApplicationDocument, AuditLog, ExtractedDocumentContent, IntakeJob, LocalUser, University
from app.models.workflow import WorkflowRecord
from app.schemas.workflow import (
    AliasInput, AssignmentInput, CaseInput, ContractInput, CoverageInput, EvidenceInput, InvitationAccept,
    InvitationInput, InvoiceStatusInput, LinkInput, NamedInput, ProgramInput, ReasonInput, ReviewInput,
    SettingsInput, TemplateInput, ValueInput,
)
from app.services import workflow as w
from app.services import workflow_evidence as evidence
from app.services import workflow_billing as billing
from app.services.intake_jobs import ensure_intake_worker_started, enqueue_intake_job
from app.services.local_auth import AuthenticatedUser, create_session, get_authenticated_user, hash_password, verify_password
from app.services.storage import StorageError, StorageService, build_document_storage_path, safe_filename
from app.services.upload_validation import read_upload_with_limit, validate_upload_filename

router = APIRouter(prefix="/api/ops", tags=["admissions-operations"])


def workspace_session(authorization: str | None = Header(default=None), auth: AuthenticatedUser = Depends(get_authenticated_user)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "The admissions workspace requires a staff bearer session.")
    return auth


Auth = Depends(workspace_session)
Db = Depends(get_db)


@router.get("/context")
def context(db: Session = Db, auth: AuthenticatedUser = Auth):
    role = w.role_for(db, auth)
    memberships = []
    for university in db.scalars(select(University).order_by(University.name)):
        member = w.membership(db, university.university_id, auth.user)
        if auth.user.role == "superadmin" or (member and member.payload["status"] == "active"):
            memberships.append({"id": university.university_id, "name": university.name, "role": member.payload["role"] if member else "university_owner"})
    result = {"university_id": auth.university_id, "role": role, "memberships": memberships,
              "settings": w.settings_for(db, auth.university_id), "platform_admin": auth.user.role == "superadmin"}
    w.commit(db)
    return result


@router.get("/programs")
def programs(db: Session = Db, auth: AuthenticatedUser = Auth):
    w.allow(db, auth, w.READERS)
    return [w.view(r) for r in w.rows(db, auth.university_id, "program")]


@router.post("/programs", status_code=201)
def create_program(payload: ProgramInput, db: Session = Db, auth: AuthenticatedUser = Auth):
    w.allow(db, auth, w.MANAGERS)
    if any(p.payload["code"].casefold() == payload.code.casefold() for p in w.rows(db, auth.university_id, "program")):
        raise HTTPException(409, "Program code already exists.")
    # Use the normalized code as the unique resource key to enforce concurrency.
    program = w.add(db, auth.university_id, "program", {**payload.model_dump(), "active_version_id": None}, key=payload.code.casefold())
    w.audit(db, auth, "program_created", program_id=program.resource_key)
    w.commit(db)
    return w.view(program)


@router.get("/programs/{program_id}/versions")
def template_versions(program_id: str, db: Session = Db, auth: AuthenticatedUser = Auth):
    w.allow(db, auth, w.READERS)
    w.find(db, auth.university_id, "program", program_id)
    return [w.view(r) for r in w.rows(db, auth.university_id, "template") if r.payload["program_id"] == program_id]


@router.post("/programs/{program_id}/draft")
def save_draft(program_id: str, payload: TemplateInput, db: Session = Db, auth: AuthenticatedUser = Auth):
    w.allow(db, auth, w.MANAGERS)
    template = w.save_template(db, auth, program_id, payload)
    w.commit(db)
    return w.view(template)


@router.post("/versions/{version_id}/publish")
def publish(version_id: str, payload: ReasonInput, db: Session = Db, auth: AuthenticatedUser = Auth):
    w.allow(db, auth, w.MANAGERS)
    template = w.publish_template(db, auth, version_id, payload.revision)
    w.commit(db)
    return w.view(template)


def case_list(db, auth):
    role = w.allow(db, auth, w.READERS)
    w.migrate_legacy(db)
    assignments = w.rows(db, auth.university_id, "assignment")
    cases = w.rows(db, auth.university_id, "case")
    if role == "reviewer":
        owned = {a.application_id for a in assignments if a.payload["reviewer_id"] == auth.user.user_id}
        cases = [case for case in cases if case.resource_key in owned]
    result = []
    for case in cases:
        current_assignments = [w.view(a) for a in assignments if a.application_id == case.resource_key]
        result.append({**w.view(case), **w.readiness(db, auth.university_id, case), "assignments": current_assignments})
    w.commit(db)
    return result


@router.get("/cases")
def cases(q: str = "", program_id: str = "", status: str = "", completeness: str = "", db: Session = Db, auth: AuthenticatedUser = Auth):
    result = case_list(db, auth)
    if q:
        query = q.casefold()
        result = [r for r in result if any(query in str(r.get(k, "")).casefold() for k in ("applicant_name", "candidate_id", "external_candidate_id", "external_application_id"))]
    if program_id:
        result = [r for r in result if r["program_id"] == program_id]
    if status:
        result = [r for r in result if r["status"] == status]
    else:
        result = [r for r in result if r["status"] != "withdrawn"]
    if completeness in {"complete", "incomplete"}:
        result = [r for r in result if (r["completeness"] == 100) == (completeness == "complete")]
    return result


@router.get("/dashboard")
def dashboard(db: Session = Db, auth: AuthenticatedUser = Auth):
    result = case_list(db, auth)
    active = [r for r in result if r["status"] not in {"withdrawn", "review_complete"}]
    active.sort(key=lambda r: (0 if r["status"] == "evidence_changed" else 1 if r["status"] in {"in_review", "ready_for_review"} else 2,
                               min((a["due_at"] or "9999" for a in r["assignments"]), default="9999")))
    published = sum(bool(p.payload["active_version_id"]) for p in w.rows(db, auth.university_id, "program"))
    reviewers = sum(m.payload["role"] == "reviewer" and m.payload["status"] == "active" for m in w.rows(db, auth.university_id, "membership"))
    return {"cases": active, "action_count": sum(r["status"] in {"ready_for_review", "in_review", "evidence_changed"} for r in active),
            "blocked_count": sum(r["completeness"] < 100 for r in active), "setup": {"published_program": published > 0, "reviewer": reviewers > 0, "application": bool(result)}}


@router.post("/cases", status_code=201)
def create_application(payload: CaseInput, db: Session = Db, auth: AuthenticatedUser = Auth):
    w.allow(db, auth, w.MANAGERS)
    _, case = w.create_case(db, auth, payload)
    w.commit(db)
    return w.view(case)


@router.get("/cases/{case_id}")
def case_detail(case_id: str, db: Session = Db, auth: AuthenticatedUser = Auth):
    application, case = w.case_access(db, auth, case_id)
    documents = w.rows(db, auth.university_id, "document", case_id)
    registered = {d.resource_key for d in documents}
    # Legacy originals remain visible without inventing requirement mappings.
    for job in db.scalars(select(IntakeJob).where(IntakeJob.application_id == case_id)):
        if job.document_id not in registered:
            documents.append(w.add(db, auth.university_id, "document", {"requirement_id": None, "slot": None, "version": 1, "current": True,
                "job_id": job.job_id, "filename": job.document.original_filename, "channel": "legacy", "received_at": job.received_at.isoformat(), "late": False},
                key=job.document_id, application_id=case_id))
    document_views = []
    for document in documents:
        job = db.get(IntakeJob, document.payload["job_id"])
        document_views.append({**w.view(document), "status": job.status, "page_count": job.document.page_count, "error": job.status_message if job.status == "failed" else None})
    health = w.readiness(db, auth.university_id, case)
    result = {"case": {**w.view(case), **health}, "aliases": w.aliases_for(db, auth.university_id, application.applicant_id),
        "requirements": [{**w.view(r), "values": w.requirement_values(r)} for r in w.rows(db, auth.university_id, "requirement", case_id)], "documents": document_views,
        "template": w.view(w.find(db, auth.university_id, "template", case.payload["template_id"])) if case.payload["template_id"] else None,
        "evidence": evidence.summary(db, auth.university_id, case_id), "assignments": [w.view(r) for r in w.rows(db, auth.university_id, "assignment", case_id)],
        "reviews": [w.view(r) for r in w.rows(db, auth.university_id, "review", case_id)],
        "own_review_signed": any(r.payload["reviewer_id"] == auth.user.user_id and r.payload["case_revision"] == case.payload["case_revision"] for r in w.rows(db, auth.university_id, "review", case_id)),
        "draft": next((w.view(r) for r in w.rows(db, auth.university_id, "draft", case_id) if r.payload["reviewer_id"] == auth.user.user_id), None),
        "activity": [{"id": a.audit_log_id, "actor": a.actor, "action": a.action, "timestamp": a.timestamp.isoformat()} for a in db.scalars(select(AuditLog).where(AuditLog.application_id == case_id).order_by(AuditLog.timestamp.desc()))],
        "links": [{"id": r.resource_key, "requirement_id": r.payload["requirement_id"], "expires_at": r.payload["expires_at"], "revoked": r.payload["revoked"], "uploads": r.payload["uploads"], "max_uploads": r.payload["max_uploads"]} for r in w.rows(db, auth.university_id, "link", case_id)] if w.role_for(db, auth) in w.MANAGERS else []}
    w.commit(db)
    return result


@router.post("/cases/{case_id}/aliases")
def add_alias(case_id: str, payload: AliasInput, db: Session = Db, auth: AuthenticatedUser = Auth):
    application, _ = w.case_access(db, auth, case_id, manage=True)
    alias = w.link_alias(db, auth.university_id, application.applicant_id, payload.source, payload.value)
    w.audit(db, auth, "candidate_alias_added", case_id, source=payload.source)
    w.commit(db)
    return w.view(alias)


@router.post("/cases/{case_id}/pin-program")
def pin_legacy(case_id: str, payload: AliasInput, db: Session = Db, auth: AuthenticatedUser = Auth):
    _, case = w.case_access(db, auth, case_id, manage=True)
    if case.payload["template_id"]:
        raise HTTPException(409, "This application already has pinned criteria.")
    # value identifies the selected program; existing documents must subsequently
    # be uploaded into requirements, so no evidence mapping is guessed.
    program, template = w.program_template(db, auth.university_id, payload.value)
    change_data = {"program_id": program.resource_key, "program_name": program.payload["name"], "template_id": template.resource_key, "template_version": template.payload["version"]}
    w.change(case, **change_data)
    for definition in template.payload["requirements"]:
        due = (datetime.now(timezone.utc) + timedelta(days=definition["due_days"])).isoformat() if definition["due_days"] is not None else None
        w.add(db, auth.university_id, "requirement", {**definition, "status": "missing", "value": None, "waiver_reason": "", "due_at": due}, application_id=case_id)
    w.evidence_changed(db, auth.university_id, case_id)
    w.audit(db, auth, "legacy_case_criteria_pinned", case_id, template_id=template.resource_key)
    w.commit(db)
    return w.view(case)


def requirement_access(db, auth, case_id, requirement_id):
    application, case = w.case_access(db, auth, case_id, manage=True)
    requirement = w.find(db, auth.university_id, "requirement", requirement_id)
    if requirement.application_id != case_id:
        raise HTTPException(404, "Requirement not found on this application.")
    if case.payload.get("external_status") == "withdrawn":
        raise HTTPException(409, "Withdrawn applications cannot receive new evidence.")
    return application, case, requirement


@router.put("/cases/{case_id}/requirements/{requirement_id}/value")
def save_requirement_value(case_id: str, requirement_id: str, payload: ValueInput, db: Session = Db, auth: AuthenticatedUser = Auth):
    _, _, requirement = requirement_access(db, auth, case_id, requirement_id)
    w.check_revision(requirement, payload.revision)
    if requirement.payload["capture_type"] == "document":
        raise HTTPException(400, "Upload a document for this requirement.")
    value = payload.value
    if requirement.payload["capture_type"] == "number":
        try:
            number = float(value)
        except ValueError:
            raise HTTPException(400, "Enter a valid number.")
        if not math.isfinite(number):
            raise HTTPException(400, "Enter a finite number.")
        value = number
    values = w.requirement_values(requirement)
    slot = payload.slot
    if slot is None:
        slot = 1 if requirement.payload["max_count"] == 1 else next((i for i in range(1, requirement.payload["max_count"] + 1) if not any(v["slot"] == i for v in values)), None)
    if slot is None or slot > requirement.payload["max_count"]:
        raise HTTPException(409, "All value slots are filled. Choose a slot explicitly to replace its value.")
    previous = next((entry for entry in values if entry["slot"] == slot), None)
    entry = {"slot": slot, "value": value, "verified_by": auth.user.email, "verified_at": w.now()}
    history = [*requirement.payload.get("value_history", []), {"before": previous, "after": entry}]
    w.change(requirement, value=value, values=sorted([v for v in values if v["slot"] != slot] + [entry], key=lambda v: v["slot"]),
             value_history=history, verified_by=auth.user.email, verified_at=entry["verified_at"], waiver_reason="")
    w.evidence_changed(db, auth.university_id, case_id)
    w.audit(db, auth, "requirement_value_verified", case_id, requirement_id=requirement_id)
    w.commit(db)
    return w.view(requirement)


@router.post("/cases/{case_id}/requirements/{requirement_id}/waive")
def waive_requirement(case_id: str, requirement_id: str, payload: ReasonInput, db: Session = Db, auth: AuthenticatedUser = Auth):
    _, _, requirement = requirement_access(db, auth, case_id, requirement_id)
    w.check_revision(requirement, payload.revision)
    if not requirement.payload["required"]:
        raise HTTPException(400, "Only required evidence needs a waiver.")
    w.change(requirement, waiver_reason=payload.reason, waived_by=auth.user.email, waived_at=w.now())
    w.evidence_changed(db, auth.university_id, case_id)
    w.audit(db, auth, "requirement_waived", case_id, requirement_id=requirement_id)
    w.commit(db)
    return w.view(requirement)


async def store_upload(request, db, settings, storage, application, case, requirement, file, slot, channel, actor):
    if requirement.payload["capture_type"] != "document" or channel not in requirement.payload["upload_channels"]:
        raise HTTPException(400, "This requirement does not accept this upload channel.")
    if case.payload.get("external_status") == "withdrawn":
        raise HTTPException(409, "This application has been withdrawn.")
    filename = validate_upload_filename(file.filename)
    extension = "." + filename.rsplit(".", 1)[-1].lower()
    if extension not in requirement.payload["allowed_extensions"]:
        raise HTTPException(400, "This file format is not allowed for the requirement.")
    if not settings.intake_worker_enabled:
        raise HTTPException(503, "Document processing is currently disabled.")
    if settings.parser_mode.lower() != "mock" and not w.settings_for(db, application.university_id).get("external_processing_approved"):
        raise HTTPException(409, "An institution administrator must approve external document processing in Institution setup.")
    versions = [d for d in w.rows(db, application.university_id, "document", application.application_id) if d.payload["requirement_id"] == requirement.resource_key]
    occupied = {d.payload["slot"] for d in versions if d.payload["current"]}
    if slot is None:
        slot = next((n for n in range(1, requirement.payload["max_count"] + 1) if n not in occupied), None)
    if slot is None or slot < 1 or slot > requirement.payload["max_count"]:
        raise HTTPException(409, "No document slot is available. Select an existing slot to replace its document.")
    content = await read_upload_with_limit(file, max_bytes=settings.max_upload_bytes)
    if not content:
        raise HTTPException(400, "The uploaded document is empty.")
    # Verify signatures for binary formats, as the reference intake does.
    signatures = {".pdf": b"%PDF-", ".docx": b"PK", ".png": b"\x89PNG\r\n\x1a\n", ".jpg": b"\xff\xd8", ".jpeg": b"\xff\xd8"}
    if extension in signatures and not content.startswith(signatures[extension]):
        raise HTTPException(400, "The file contents do not match its extension.")
    from app.models import SavedRubric
    template = w.find(db, application.university_id, "template", case.payload["template_id"])
    rubric_id = "workflow_" + template.resource_key
    rubric = db.get(SavedRubric, rubric_id)
    if rubric is None:
        sections = [{"section_id": c["key"], "name": c["label"], "description": c["instructions"], "max_points": c["weight"], "components": []} for c in template.payload["criteria"]]
        rubric = SavedRubric(rubric_id=rubric_id, university_id=application.university_id, name=template.payload["name"], total_points=100,
            sections=sections or [{"section_id": "checklist", "name": "Checklist review", "max_points": 0, "components": []}], version=template.payload["version"])
        db.add(rubric)
        db.flush()
    from uuid import uuid4
    document_id = str(uuid4())
    path = build_document_storage_path(application_id=application.application_id, document_id=document_id, filename=filename)
    try:
        stored = storage.save_bytes(storage_path=path, content=content)
    except StorageError:
        raise HTTPException(503, "Document storage is unavailable. Try again.")
    document = ApplicationDocument(document_id=document_id, application_id=application.application_id, original_filename=safe_filename(filename), blob_url_or_path=stored,
        processing_status="uploaded", classification_metadata={"file_size": len(content), "upload_channel": channel})
    db.add(document)
    db.flush()
    job = IntakeJob(application_id=application.application_id, document_id=document_id, rubric_id=rubric.rubric_id, parser_mode=settings.parser_mode)
    db.add(job)
    db.flush()
    previous = [d for d in versions if d.payload["slot"] == slot]
    for old in previous:
        if old.payload["current"]:
            w.change(old, current=False)
    received_at = w.now()
    version = w.add(db, application.university_id, "document", {"requirement_id": requirement.resource_key, "slot": slot,
        "version": max((d.payload["version"] for d in previous), default=0) + 1, "current": True, "job_id": job.job_id,
        "filename": document.original_filename, "channel": channel, "received_at": received_at,
        "late": bool(requirement.payload["due_at"] and received_at > requirement.payload["due_at"]), "uploaded_by": actor}, key=document_id, application_id=application.application_id)
    w.change(requirement, waiver_reason="")
    w.evidence_changed(db, application.university_id, application.application_id)
    from app.services.audit import record_audit_log
    from app.security import AuthenticatedActor, UserRole
    record_audit_log(db=db, actor=AuthenticatedActor(actor_id=actor, role=UserRole.ADMIN), action="requirement_document_upload", application_id=application.application_id, document_id=document_id,
        metadata={"university_id": application.university_id, "requirement_id": requirement.resource_key, "slot": slot, "version": version.payload["version"], "channel": channel})
    w.commit(db)
    await ensure_intake_worker_started(request.app, settings)
    await enqueue_intake_job(request.app, job.job_id)
    return {**w.view(version), "status": "queued"}


@router.post("/cases/{case_id}/requirements/{requirement_id}/upload", status_code=201)
async def upload_requirement(case_id: str, requirement_id: str, request: Request, file: UploadFile = File(...), slot: int | None = Form(None),
    db: Session = Db, auth: AuthenticatedUser = Auth, settings: AppSettings = Depends(get_settings), storage: StorageService = Depends(storage_service_dependency)):
    application, case, requirement = requirement_access(db, auth, case_id, requirement_id)
    return await store_upload(request, db, settings, storage, application, case, requirement, file, slot, "staff", auth.user.email)


@router.post("/cases/{case_id}/requirements/{requirement_id}/links", status_code=201)
def create_link(case_id: str, requirement_id: str, payload: LinkInput, db: Session = Db, auth: AuthenticatedUser = Auth):
    _, _, requirement = requirement_access(db, auth, case_id, requirement_id)
    if requirement.payload["capture_type"] != "document" or "secure_link" not in requirement.payload["upload_channels"]:
        raise HTTPException(400, "Secure links are not enabled for this requirement.")
    token = secrets.token_urlsafe(40)
    expiry = datetime.now(timezone.utc) + timedelta(days=payload.expiry_days or w.settings_for(db, auth.university_id)["secure_link_expiry_days"])
    link = w.add(db, auth.university_id, "link", {"requirement_id": requirement_id, "expires_at": expiry.isoformat(), "max_uploads": payload.max_uploads,
        "uploads": 0, "revoked": False}, key=w.key_hash(token), application_id=case_id)
    w.audit(db, auth, "secure_upload_link_created", case_id, requirement_id=requirement_id)
    w.commit(db)
    return {"id": link.resource_key, "token": token, "path": f"#/upload/{token}", "expires_at": expiry.isoformat()}


@router.post("/links/{link_id}/revoke")
def revoke_link(link_id: str, db: Session = Db, auth: AuthenticatedUser = Auth):
    w.allow(db, auth, w.MANAGERS)
    link = w.find(db, auth.university_id, "link", link_id)
    w.change(link, revoked=True)
    w.audit(db, auth, "secure_upload_link_revoked", link.application_id)
    w.commit(db)
    return {"ok": True}


def token_record(db, token, kind):
    record = db.scalar(select(WorkflowRecord).where(WorkflowRecord.kind == kind, WorkflowRecord.resource_key == w.key_hash(token)).with_for_update())
    if not record or record.payload.get("revoked") or datetime.fromisoformat(record.payload["expires_at"]) <= datetime.now(timezone.utc):
        raise HTTPException(410, "This link has expired or cannot be used.")
    return record


@router.get("/public/uploads/{token}")
def public_upload_info(token: str, db: Session = Db):
    link = token_record(db, token, "link")
    if link.payload["uploads"] >= link.payload["max_uploads"]:
        raise HTTPException(410, "This upload link has already been used.")
    requirement = w.find(db, link.university_id, "requirement", link.payload["requirement_id"])
    return {"requirement_label": requirement.payload["label"], "instructions": requirement.payload["instructions"], "allowed_extensions": requirement.payload["allowed_extensions"],
            "expires_at": link.payload["expires_at"], "branding": w.settings_for(db, link.university_id)}


@router.post("/public/uploads/{token}", status_code=201)
async def public_upload(token: str, request: Request, file: UploadFile = File(...), db: Session = Db,
                        settings: AppSettings = Depends(get_settings), storage: StorageService = Depends(storage_service_dependency)):
    link = token_record(db, token, "link")
    if link.payload["uploads"] >= link.payload["max_uploads"]:
        raise HTTPException(410, "This upload link has already been used.")
    requirement = w.find(db, link.university_id, "requirement", link.payload["requirement_id"])
    case = w.find(db, link.university_id, "case", link.application_id)
    application = db.get(Application, link.application_id)
    w.change(link, uploads=link.payload["uploads"] + 1)
    await store_upload(request, db, settings, storage, application, case, requirement, file, None, "secure_link", "secure-link uploader")
    return {"status": "queued", "message": "Your document was received and is processing."}


@router.get("/cases/{case_id}/documents/{document_id}/evidence")
def document_evidence(case_id: str, document_id: str, db: Session = Db, auth: AuthenticatedUser = Auth):
    w.case_access(db, auth, case_id)
    document = w.find(db, auth.university_id, "document", document_id)
    if document.application_id != case_id:
        raise HTTPException(404, "Document not found on this case.")
    analysis = evidence.ensure_analysis(db, auth.university_id, document)
    if analysis is None:
        raise HTTPException(409, "Evidence is available after processing completes.")
    result = {**w.view(analysis), "current": document.payload["current"], "document_types": evidence.DOCUMENT_TYPES}
    w.commit(db)
    return result


@router.put("/cases/{case_id}/documents/{document_id}/evidence")
def correct_document(case_id: str, document_id: str, payload: EvidenceInput, db: Session = Db, auth: AuthenticatedUser = Auth):
    w.allow(db, auth, w.MANAGERS | {"reviewer"})
    w.case_access(db, auth, case_id)
    document = w.find(db, auth.university_id, "document", document_id)
    if document.application_id != case_id:
        raise HTTPException(404, "Document not found on this case.")
    analysis = evidence.correct(db, auth, document, payload)
    w.commit(db)
    return w.view(analysis)


@router.get("/cases/{case_id}/documents/{document_id}/source")
def document_source(case_id: str, document_id: str, db: Session = Db, auth: AuthenticatedUser = Auth, storage: StorageService = Depends(storage_service_dependency)):
    w.case_access(db, auth, case_id)
    document = db.get(ApplicationDocument, document_id)
    if not document or document.application_id != case_id:
        raise HTTPException(404, "Document not found on this case.")
    try:
        content = storage.read_bytes(storage_path=document.blob_url_or_path)
    except StorageError:
        raise HTTPException(404, "Original document is unavailable.")
    extension = document.original_filename.rsplit(".", 1)[-1].lower()
    media = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "txt": "text/plain; charset=utf-8"}.get(extension, "application/octet-stream")
    w.audit(db, auth, "original_document_viewed", case_id, document_id=document_id)
    w.commit(db)
    return Response(content, media_type=media, headers={"Content-Disposition": f'inline; filename="{safe_filename(document.original_filename)}"', "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/people")
def people(db: Session = Db, auth: AuthenticatedUser = Auth):
    w.allow(db, auth, w.MANAGERS)
    for user in db.scalars(select(LocalUser).where(LocalUser.university_id == auth.university_id)):
        w.membership(db, auth.university_id, user)
    assignments = w.rows(db, auth.university_id, "assignment")
    reviews = w.rows(db, auth.university_id, "review")
    result = []
    for member in w.rows(db, auth.university_id, "membership"):
        user = db.get(LocalUser, member.payload["user_id"])
        open_count = 0
        for assignment in assignments:
            if assignment.payload["reviewer_id"] != member.payload["user_id"]:
                continue
            case = w.find(db, auth.university_id, "case", assignment.application_id)
            if case.payload.get("external_status") != "withdrawn" and not any(r.application_id == assignment.application_id and r.payload["reviewer_id"] == member.payload["user_id"] and r.payload["case_revision"] == case.payload["case_revision"] for r in reviews):
                open_count += 1
        result.append({**w.view(member), "email": user.email, "last_login_at": user.last_login_at, "open_reviews": open_count})
    w.commit(db)
    return {"people": result, "invitations": [{"id": r.resource_key, "email": r.payload["email"], "role": r.payload["role"], "expires_at": r.payload["expires_at"], "accepted": r.payload["accepted"], "revoked": r.payload["revoked"]} for r in w.rows(db, auth.university_id, "invitation")],
            "departments": [w.view(r) for r in w.rows(db, auth.university_id, "department")]}


@router.post("/invitations", status_code=201)
def invite_staff(payload: InvitationInput, db: Session = Db, auth: AuthenticatedUser = Auth, settings: AppSettings = Depends(get_settings)):
    role = w.allow(db, auth, w.MANAGERS)
    if payload.role == "university_owner" and role != "university_owner":
        raise HTTPException(403, "Only an owner can invite another owner.")
    user = db.scalar(select(LocalUser).where(LocalUser.email == payload.email))
    if user and w.find(db, auth.university_id, "membership", user.user_id, required=False):
        raise HTTPException(409, "This staff member already belongs to the university.")
    token = secrets.token_urlsafe(40)
    invitation = w.add(db, auth.university_id, "invitation", {**payload.model_dump(), "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(), "accepted": False, "revoked": False}, key=w.key_hash(token))
    w.audit(db, auth, "staff_invited", workspace_role=payload.role)
    w.commit(db)
    path = f"#/invite/{token}"
    delivery = "not_requested"
    if payload.deliver_email:
        from app.services.workflow_mail import send_invitation
        delivery = send_invitation(settings, payload.email, path)
    w.change(invitation, email_delivery=delivery)
    w.audit(db, auth, "staff_invitation_delivery", delivery=delivery)
    w.commit(db)
    return {"id": invitation.resource_key, "path": path, "expires_at": invitation.payload["expires_at"], "email_delivery": delivery}


@router.post("/invitations/{invitation_id}/revoke")
def revoke_invitation(invitation_id: str, db: Session = Db, auth: AuthenticatedUser = Auth):
    w.allow(db, auth, w.MANAGERS)
    invitation = w.find(db, auth.university_id, "invitation", invitation_id)
    w.change(invitation, revoked=True)
    w.audit(db, auth, "staff_invitation_revoked")
    w.commit(db)
    return {"ok": True}


@router.get("/public/invitations/{token}")
def invitation_info(token: str, db: Session = Db):
    invitation = token_record(db, token, "invitation")
    if invitation.payload["accepted"]:
        raise HTTPException(410, "This invitation was already accepted.")
    return {"email": invitation.payload["email"], "role": invitation.payload["role"], "university": db.get(University, invitation.university_id).name}


@router.post("/public/invitations/{token}")
def accept_invitation(token: str, payload: InvitationAccept, db: Session = Db):
    invitation = token_record(db, token, "invitation")
    if invitation.payload["accepted"]:
        raise HTTPException(410, "This invitation was already accepted.")
    user = db.scalar(select(LocalUser).where(LocalUser.email == invitation.payload["email"]))
    base_role = {"university_owner": "admin", "admissions_manager": "admin", "reviewer": "admissions_reviewer", "finance_viewer": "finance_viewer", "auditor": "read_only_auditor"}[invitation.payload["role"]]
    if user is None:
        user = LocalUser(email=invitation.payload["email"], password_hash=hash_password(payload.password), university_id=invitation.university_id, role=base_role)
        db.add(user)
        db.flush()
    elif not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Enter your existing account password to join this workspace.")
    if w.find(db, invitation.university_id, "membership", user.user_id, required=False):
        raise HTTPException(409, "This account already has a workspace membership.")
    w.add(db, invitation.university_id, "membership", {"user_id": user.user_id, "role": invitation.payload["role"], "status": "active", "program_ids": [], "department_ids": [], "workload_limit": 20}, key=user.user_id)
    w.change(invitation, accepted=True)
    from app.services.audit import record_audit_log
    record_audit_log(db=db, actor=None, action="staff_invitation_accepted", metadata={"university_id": invitation.university_id, "user_id": user.user_id})
    w.commit(db)
    token = create_session(db=db, user=user)
    return {"token": token, "university_id": invitation.university_id}


@router.put("/people/{user_id}/coverage")
def update_coverage(user_id: str, payload: CoverageInput, db: Session = Db, auth: AuthenticatedUser = Auth):
    w.allow(db, auth, w.MANAGERS)
    member = w.find(db, auth.university_id, "membership", user_id)
    if member.payload["role"] != "reviewer":
        raise HTTPException(400, "Coverage applies to reviewers.")
    for program_id in payload.program_ids:
        w.find(db, auth.university_id, "program", program_id)
    for department_id in payload.department_ids:
        w.find(db, auth.university_id, "department", department_id)
    w.change(member, **payload.model_dump())
    w.audit(db, auth, "reviewer_coverage_changed", reviewer_id=user_id)
    w.commit(db)
    return w.view(member)


@router.post("/departments", status_code=201)
def create_department(payload: NamedInput, db: Session = Db, auth: AuthenticatedUser = Auth):
    w.allow(db, auth, w.MANAGERS)
    department = w.add(db, auth.university_id, "department", payload.model_dump(), key=w.key_hash(payload.name.casefold()))
    w.audit(db, auth, "department_created")
    w.commit(db)
    return w.view(department)


@router.post("/cases/{case_id}/assign")
def assign_reviewer(case_id: str, payload: AssignmentInput, db: Session = Db, auth: AuthenticatedUser = Auth):
    _, case = w.case_access(db, auth, case_id, manage=True)
    health = w.readiness(db, auth.university_id, case)
    if health["completeness"] < 100 or health["status"] in {"withdrawn", "legacy"}:
        raise HTTPException(409, "Resolve required evidence before assigning this case.")
    member = w.find(db, auth.university_id, "membership", payload.reviewer_id)
    if member.payload["role"] != "reviewer" or member.payload["status"] != "active":
        raise HTTPException(400, "Select an active university reviewer.")
    if member.payload["program_ids"] and case.payload["program_id"] not in member.payload["program_ids"]:
        raise HTTPException(409, "This reviewer does not cover the application's program.")
    if member.payload["department_ids"] and case.payload["department_id"] not in member.payload["department_ids"]:
        raise HTTPException(409, "This reviewer does not cover the application's department.")
    key = w.key_hash(case_id, payload.reviewer_id)
    existing = w.find(db, auth.university_id, "assignment", key, required=False)
    if existing is None:
        open_count = 0
        for assignment in w.rows(db, auth.university_id, "assignment"):
            if assignment.payload["reviewer_id"] == payload.reviewer_id:
                other = w.find(db, auth.university_id, "case", assignment.application_id)
                fresh = any(r.payload["reviewer_id"] == payload.reviewer_id and r.payload["case_revision"] == other.payload["case_revision"] for r in w.rows(db, auth.university_id, "review", assignment.application_id))
                open_count += other.payload.get("external_status") != "withdrawn" and not fresh
        if open_count >= member.payload["workload_limit"]:
            raise HTTPException(409, "This reviewer has reached their workload limit.")
        existing = w.add(db, auth.university_id, "assignment", {**payload.model_dump(), "reviewer_email": db.get(LocalUser, payload.reviewer_id).email, "assigned_by": auth.user.email}, key=key, application_id=case_id)
    else:
        w.change(existing, **payload.model_dump())
    # Touch membership so simultaneous workload checks cannot both succeed.
    w.change(member, last_assignment_at=w.now())
    w.audit(db, auth, "reviewer_assigned", case_id, reviewer_id=payload.reviewer_id)
    w.commit(db)
    return w.view(existing)


def review_validate(db, auth, case_id, payload, submit):
    w.allow(db, auth, {"reviewer"})
    _, case = w.case_access(db, auth, case_id)
    if not case.payload["template_id"] or case.payload.get("external_status") == "withdrawn":
        raise HTTPException(409, "This case cannot be reviewed.")
    if payload.revision != case.payload["case_revision"]:
        raise HTTPException(409, "Evidence changed. Reload and recheck the case before saving.")
    template = w.find(db, auth.university_id, "template", case.payload["template_id"])
    criteria = {c["key"]: c for c in template.payload["criteria"]}
    if set(payload.scores) - set(criteria) or set(payload.criterion_notes) - set(criteria):
        raise HTTPException(400, "The submitted criteria do not belong to this application.")
    if any(score < 0 or score > criteria[key]["weight"] for key, score in payload.scores.items()):
        raise HTTPException(400, "Each score must be within its criterion's bounds.")
    if any(len(note) > 2000 for note in payload.criterion_notes.values()):
        raise HTTPException(400, "Criterion notes cannot exceed 2,000 characters.")
    if submit:
        health = w.readiness(db, auth.university_id, case)
        issues = evidence.summary(db, auth.university_id, case_id)["issues"]
        if health["completeness"] < 100 or issues:
            raise HTTPException(409, "Resolve evidence blockers before signing. " + " ".join(issues[:3]))
        if set(payload.scores) != set(criteria) or not payload.source_verified:
            raise HTTPException(400, "Score every criterion and confirm that you checked the source evidence.")
        if payload.conflict_declared:
            raise HTTPException(409, "A declared conflict of interest prevents signing; contact the admissions manager.")
    return case, template


@router.put("/cases/{case_id}/review/draft")
def save_review_draft(case_id: str, payload: ReviewInput, db: Session = Db, auth: AuthenticatedUser = Auth):
    case, _ = review_validate(db, auth, case_id, payload, False)
    if w.find(db, auth.university_id, "review", w.key_hash(case_id, auth.user.user_id, str(case.payload["case_revision"])), required=False):
        raise HTTPException(409, "You already signed this case revision. Its review is preserved.")
    key = w.key_hash(case_id, auth.user.user_id)
    draft = w.find(db, auth.university_id, "draft", key, required=False)
    data = {**payload.model_dump(exclude={"revision"}), "reviewer_id": auth.user.user_id, "case_revision": case.payload["case_revision"], "saved_at": w.now()}
    if draft:
        w.change(draft, **data)
    else:
        draft = w.add(db, auth.university_id, "draft", data, key=key, application_id=case_id)
    w.change(case, last_draft_at=w.now())
    w.commit(db)
    return w.view(draft)


@router.post("/cases/{case_id}/review/sign", status_code=201)
def sign_review(case_id: str, payload: ReviewInput, db: Session = Db, auth: AuthenticatedUser = Auth):
    case, template = review_validate(db, auth, case_id, payload, True)
    key = w.key_hash(case_id, auth.user.user_id, str(case.payload["case_revision"]))
    if w.find(db, auth.university_id, "review", key, required=False):
        raise HTTPException(409, "You already signed this case revision.")
    review = w.add(db, auth.university_id, "review", {**payload.model_dump(exclude={"revision"}), "reviewer_id": auth.user.user_id,
        "reviewer_email": auth.user.email, "case_revision": case.payload["case_revision"], "template_id": template.resource_key,
        "template_version": template.payload["version"], "total_score": sum(payload.scores.values()), "submitted_at": w.now(), "status": "submitted"}, key=key, application_id=case_id)
    # Lock/touch the case so a simultaneous evidence edit cannot pass sign-off.
    w.change(case, last_signed_at=w.now())
    draft = w.find(db, auth.university_id, "draft", w.key_hash(case_id, auth.user.user_id), required=False)
    if draft:
        db.delete(draft)
    w.audit(db, auth, "human_review_signed", case_id, case_revision=case.payload["case_revision"], template_id=template.resource_key)
    w.commit(db)
    return w.view(review)


@router.get("/settings")
def institution_settings(db: Session = Db, auth: AuthenticatedUser = Auth):
    w.allow(db, auth, w.MANAGERS)
    return w.settings_for(db, auth.university_id)


@router.put("/settings")
def save_settings(payload: SettingsInput, db: Session = Db, auth: AuthenticatedUser = Auth):
    w.allow(db, auth, w.MANAGERS)
    settings = w.find(db, auth.university_id, "settings", "default", required=False)
    if settings:
        w.change(settings, **payload.model_dump())
    else:
        settings = w.add(db, auth.university_id, "settings", payload.model_dump(), key="default")
    w.audit(db, auth, "institution_settings_saved")
    w.commit(db)
    return settings.payload


@router.get("/training")
def training(db: Session = Db, auth: AuthenticatedUser = Auth):
    w.allow(db, auth, w.MANAGERS)
    if not w.settings_for(db, auth.university_id)["fictional_example_enabled"]:
        raise HTTPException(404, "Enable the fictional walkthrough in Institution setup.")
    return {"candidate": "Example applicant", "program": "MS Data Science", "template_version": 1, "case_revision": 1,
        "documents": [{"id": "EXAMPLE-DOC-01", "type": "transcript", "page": 2, "text": "Academic transcript\nApplicant name: Example applicant\nDegree: Bachelor of Science\nLinear algebra: A\nProbability: A\nAlgorithms: B"},
                      {"id": "EXAMPLE-DOC-02", "type": "statement", "page": 1, "text": "Statement of purpose\nI plan to study uncertainty estimation and statistical learning, building on my final year project."}],
        "criteria": [{"key": "academic", "label": "Academic preparation", "weight": 60, "document_id": "EXAMPLE-DOC-01", "page": 2},
                     {"key": "motivation", "label": "Program motivation", "weight": 40, "document_id": "EXAMPLE-DOC-02", "page": 1}]}


CSV_COLUMNS = ["external_application_id", "external_candidate_id", "applicant_name", "program_code", "cycle", "application_status"]


def csv_response(columns, data, filename):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(columns)
    for row in data:
        values = [row.get(column, "") for column in columns]
        writer.writerow(["'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value for value in values])
    return Response(output.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})


@router.get("/exchange/template.csv")
def exchange_template(db: Session = Db, auth: AuthenticatedUser = Auth):
    w.allow(db, auth, w.MANAGERS)
    return csv_response(CSV_COLUMNS, [], "applications-template.csv")


def preview_row(db, tenant, raw, duplicates):
    values = {column: str(raw.get(column) or "").strip() for column in CSV_COLUMNS}
    status = values["application_status"] or "submitted"
    if status not in {"submitted", "in_progress", "withdrawn"}:
        return {"status": "invalid", "message": "Application status must be submitted, in_progress or withdrawn.", "values": values}
    values["application_status"] = status
    program = next((r for r in w.rows(db, tenant, "program") if r.payload["code"].casefold() == values["program_code"].casefold()), None)
    if not program or not program.payload["active_version_id"]:
        return {"status": "invalid", "message": "Map a program code with published criteria first.", "values": values}
    if not values["external_application_id"] or values["external_application_id"] in duplicates:
        return {"status": "invalid", "message": "External application ID must be present and unique within the CSV.", "values": values}
    try:
        payload = CaseInput(applicant_name=values["applicant_name"], external_candidate_id=values["external_candidate_id"],
            external_application_id=values["external_application_id"], program_id=program.resource_key, intake_term=values["cycle"])
    except ValueError:
        return {"status": "invalid", "message": "Check required candidate, application, program and cycle fields and their lengths.", "values": values}
    existing = next((r for r in w.rows(db, tenant, "case") if r.payload.get("external_application_id") == values["external_application_id"]), None)
    alias = w.find(db, tenant, "alias", w.key_hash("sis", values["external_candidate_id"]), required=False)
    if existing:
        if existing.payload["program_id"] != program.resource_key or not alias or alias.payload["candidate_id"] != existing.payload["candidate_id"]:
            return {"status": "conflict", "message": "Candidate identity or program differs. Resolve manually before importing.", "values": values}
        changes = {k: {"from": existing.payload.get(k), "to": value} for k, value in {"applicant_name": payload.applicant_name, "intake_term": payload.intake_term, "external_status": status}.items() if existing.payload.get(k) != value}
        return {"status": "update" if changes else "unchanged", "message": "Mapped changes" if changes else "Already in sync", "values": values,
                "payload": payload.model_dump(), "changes": changes, "case_id": existing.resource_key, "expected_case_revision": existing.payload["case_revision"]}
    return {"status": "create", "message": "New candidate application", "values": values, "payload": payload.model_dump(), "expected_template_id": program.payload["active_version_id"]}


@router.post("/exchange/import/preview", status_code=201)
async def preview_import(file: UploadFile = File(...), db: Session = Db, auth: AuthenticatedUser = Auth):
    w.allow(db, auth, w.MANAGERS)
    content = await read_upload_with_limit(file, max_bytes=5 * 1024 * 1024)
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
        if not reader.fieldnames or not set(CSV_COLUMNS[:-1]) <= set(reader.fieldnames):
            raise ValueError("CSV header is missing required columns.")
        raw_rows = list(reader)
        if not raw_rows or len(raw_rows) > 5000:
            raise ValueError("Import between 1 and 5,000 application rows.")
    except (ValueError, UnicodeDecodeError, csv.Error) as exc:
        raise HTTPException(400, str(exc))
    counts = {}
    for raw in raw_rows:
        identifier = str(raw.get("external_application_id") or "").strip()
        counts[identifier] = counts.get(identifier, 0) + 1
    duplicates = {value for value, count in counts.items() if count > 1}
    batch = w.add(db, auth.university_id, "import", {"filename": safe_filename(file.filename or "applications.csv"), "content_sha256": hashlib.sha256(content).hexdigest(),
        "status": "preview", "created_by": auth.user.email, "rows": [{**preview_row(db, auth.university_id, raw, duplicates), "row_number": index + 2} for index, raw in enumerate(raw_rows)]})
    w.audit(db, auth, "application_import_previewed", batch_id=batch.resource_key, row_count=len(raw_rows))
    w.commit(db)
    return w.view(batch)


@router.get("/exchange/history")
def exchange_history(db: Session = Db, auth: AuthenticatedUser = Auth):
    w.allow(db, auth, w.MANAGERS)
    return {"imports": [w.view(r) for r in w.rows(db, auth.university_id, "import")], "exports": [w.view(r) for r in w.rows(db, auth.university_id, "export")]}


@router.post("/exchange/import/{batch_id}/commit")
def commit_import(batch_id: str, db: Session = Db, auth: AuthenticatedUser = Auth):
    w.allow(db, auth, w.MANAGERS)
    batch = w.find(db, auth.university_id, "import", batch_id)
    if batch.payload["status"] != "preview":
        raise HTTPException(409, "This import batch was already committed.")
    result = []
    for original in batch.payload["rows"]:
        row = dict(original)
        if row["status"] not in {"create", "update", "unchanged"}:
            result.append(row)
            continue
        fresh = preview_row(db, auth.university_id, row["values"], set())
        changed = fresh["status"] != row["status"] or fresh.get("changes") != row.get("changes") or fresh.get("expected_case_revision") != row.get("expected_case_revision") or fresh.get("expected_template_id") != row.get("expected_template_id")
        if changed:
            row.update(status="conflict", message="The application or published program changed after preview. Preview again.")
            result.append(row)
            continue
        if row["status"] == "create":
            _, case = w.create_case(db, auth, CaseInput.model_validate(row["payload"]))
            w.change(case, external_status=row["values"]["application_status"])
            row.update(status="created", case_id=case.resource_key)
        elif row["status"] == "update":
            case = w.find(db, auth.university_id, "case", row["case_id"])
            w.change(case, **{key: value["to"] for key, value in row["changes"].items()})
            w.evidence_changed(db, auth.university_id, case.resource_key)
            w.audit(db, auth, "application_import_updated", case.resource_key)
            row["status"] = "updated"
        result.append(row)
    w.change(batch, rows=result, status="committed", committed_at=w.now(), committed_by=auth.user.email)
    w.audit(db, auth, "application_import_committed", batch_id=batch_id)
    w.commit(db)
    return w.view(batch)


@router.get("/exchange/scores.csv")
def export_scores(db: Session = Db, auth: AuthenticatedUser = Auth):
    w.allow(db, auth, w.MANAGERS)
    previous = w.rows(db, auth.university_id, "export")
    export = w.add(db, auth.university_id, "export", {"created_by": auth.user.email, "supersedes_export_id": previous[-1].resource_key if previous else ""})
    exported = []
    for review in w.rows(db, auth.university_id, "review"):
        case = w.find(db, auth.university_id, "case", review.application_id)
        if review.payload["case_revision"] != case.payload["case_revision"] or not case.payload["external_application_id"] or case.payload.get("external_status") == "withdrawn":
            continue
        template = w.find(db, auth.university_id, "template", review.payload["template_id"])
        for criterion in template.payload["criteria"]:
            exported.append({"external_application_id": case.payload["external_application_id"], "external_candidate_id": case.payload["external_candidate_id"],
                "case_revision": review.payload["case_revision"], "rubric_version": review.payload["template_version"], "criterion_key": criterion["key"],
                "score": review.payload["scores"][criterion["key"]], "max_score": criterion["weight"], "reviewer": review.payload["reviewer_email"],
                "review_submitted_at": review.payload["submitted_at"], "export_batch_id": export.resource_key, "supersedes_export_id": export.payload["supersedes_export_id"]})
    columns = ["external_application_id", "external_candidate_id", "case_revision", "rubric_version", "criterion_key", "score", "max_score", "reviewer", "review_submitted_at", "export_batch_id", "supersedes_export_id"]
    response = csv_response(columns, exported, f"signed-scores-{export.resource_key[:8]}.csv")
    w.change(export, row_count=len(exported), content_sha256=hashlib.sha256(response.body).hexdigest())
    w.audit(db, auth, "signed_scores_exported", batch_id=export.resource_key, row_count=len(exported))
    w.commit(db)
    return response


@router.get("/billing")
def billing_summary(db: Session = Db, auth: AuthenticatedUser = Auth):
    w.allow(db, auth, w.FINANCE)
    return {"contracts": [w.view(r) for r in w.rows(db, auth.university_id, "contract")], "usage": [w.view(r) for r in w.rows(db, auth.university_id, "usage")],
            "invoices": [w.view(r) for r in w.rows(db, auth.university_id, "invoice")]}


@router.post("/billing/contracts", status_code=201)
def publish_contract(payload: ContractInput, db: Session = Db, auth: AuthenticatedUser = Auth):
    if auth.user.role != "superadmin":
        raise HTTPException(403, "Only platform administrators can publish commercial terms.")
    university = db.get(University, auth.university_id)
    if not university:
        raise HTTPException(404, "University not found.")
    contracts = w.rows(db, auth.university_id, "contract")
    version = len(contracts) + 1
    contract = w.add(db, auth.university_id, "contract", {**payload.model_dump(), "version": version, "effective_from": w.now()}, key=str(version))
    w.audit(db, auth, "billing_contract_published", contract_id=contract.resource_key)
    w.commit(db)
    return w.view(contract)


@router.post("/billing/invoices", status_code=201)
def generate_invoice(db: Session = Db, auth: AuthenticatedUser = Auth):
    if auth.user.role != "superadmin":
        raise HTTPException(403, "Only platform administrators can generate invoices.")
    result = billing.generate_invoices(db, auth.university_id)
    w.audit(db, auth, "invoice_drafts_generated")
    w.commit(db)
    return result


@router.put("/billing/invoices/{invoice_id}")
def update_invoice(invoice_id: str, payload: InvoiceStatusInput, db: Session = Db, auth: AuthenticatedUser = Auth):
    if auth.user.role != "superadmin":
        raise HTTPException(403, "Only platform administrators can update invoices.")
    invoice = billing.invoice_status(db, auth.university_id, invoice_id, payload.status)
    w.audit(db, auth, "invoice_status_changed", invoice_id=invoice_id, status=payload.status)
    w.commit(db)
    return w.view(invoice)


@router.get("/billing/usage.csv")
def usage_csv(db: Session = Db, auth: AuthenticatedUser = Auth):
    w.allow(db, auth, w.FINANCE)
    data = []
    for usage in w.rows(db, auth.university_id, "usage"):
        data.append({**usage.payload, "pages_per_unit": usage.payload["contract_snapshot"]["pages_per_unit"], "price_per_unit_cents": usage.payload["contract_snapshot"]["price_per_unit_cents"]})
    return csv_response(["document_id", "filename", "page_count", "pages_per_unit", "price_per_unit_cents", "units", "charge_cents", "currency", "occurred_at", "invoice_id"], data, "document-usage.csv")


@router.get("/reports")
def reports(db: Session = Db, auth: AuthenticatedUser = Auth):
    w.allow(db, auth, w.FINANCE | {"auditor"})
    cases = w.rows(db, auth.university_id, "case")
    completeness = {}
    states = {}
    for case in cases:
        health = w.readiness(db, auth.university_id, case)
        states[health["status"]] = states.get(health["status"], 0) + 1
        name = case.payload["program_name"]
        stats = completeness.setdefault(name, {"program": name, "applications": 0, "total_completeness": 0, "ready": 0})
        stats["applications"] += 1
        stats["total_completeness"] += health["completeness"]
        stats["ready"] += health["status"] == "ready_for_review"
    missing = {}
    for requirement in w.rows(db, auth.university_id, "requirement"):
        if requirement.payload["required"] and requirement.payload["status"] not in {"accepted", "waived"}:
            label = requirement.payload["label"]
            missing[label] = missing.get(label, 0) + 1
    documents = w.rows(db, auth.university_id, "document")
    processing = {}
    for document in documents:
        if document.payload["current"]:
            status = db.get(IntakeJob, document.payload["job_id"]).status
            processing[status] = processing.get(status, 0) + 1
    assignments = w.rows(db, auth.university_id, "assignment")
    reviews = w.rows(db, auth.university_id, "review")
    throughput = []
    for member in w.rows(db, auth.university_id, "membership"):
        if member.payload["role"] != "reviewer":
            continue
        assigned = [a for a in assignments if a.payload["reviewer_id"] == member.resource_key]
        signed = [r for r in reviews if r.payload["reviewer_id"] == member.resource_key]
        durations = []
        for review in signed:
            assignment = next((a for a in assigned if a.application_id == review.application_id), None)
            if assignment:
                created = assignment.created_at.replace(tzinfo=timezone.utc) if assignment.created_at.tzinfo is None else assignment.created_at
                durations.append(max(0, (datetime.fromisoformat(review.payload["submitted_at"]) - created).total_seconds() / 3600))
        scored = [r for r in signed if r.payload["scores"]]
        throughput.append({"reviewer": db.get(LocalUser, member.resource_key).email, "assigned": len(assigned), "signed_revisions": len(signed),
                           "average_score": round(sum(r.payload["total_score"] for r in scored) / len(scored), 1) if scored else None,
                           "average_turnaround_hours": round(sum(durations) / len(durations), 1) if durations else None})
    result = {"states": states, "programs": [{**v, "average_completeness": round(v["total_completeness"] / v["applications"])} for v in completeness.values()],
              "missing": missing, "processing": processing, "reviewers": throughput, "late_versions": sum(d.payload["late"] for d in documents),
              "reopened_cases": states.get("evidence_changed", 0)}
    w.commit(db)
    return result


@router.get("/platform")
def platform_overview(db: Session = Db, auth: AuthenticatedUser = Auth):
    if auth.user.role != "superadmin":
        raise HTTPException(403, "Platform administrator access required.")
    universities = []
    for university in db.scalars(select(University).order_by(University.name)):
        tenant = university.university_id
        cases = w.rows(db, tenant, "case")
        usage = w.rows(db, tenant, "usage")
        spend = {}
        for item in usage:
            spend[item.payload["currency"]] = spend.get(item.payload["currency"], 0) + item.payload["charge_cents"]
        members = w.rows(db, tenant, "membership")
        universities.append({"id": tenant, "name": university.name, "applications": len(cases), "members": len(members), "documents": len(usage),
            "units": sum(u.payload["units"] for u in usage), "spend": spend,
            "onboarding": {"owner": any(m.payload["role"] in w.MANAGERS for m in members), "published_program": any(p.payload["active_version_id"] for p in w.rows(db, tenant, "program")), "first_application": bool(cases)},
            "contract_versions": len(w.rows(db, tenant, "contract"))})
    activity = [{"actor": a.actor, "action": a.action, "timestamp": a.timestamp.isoformat()} for a in db.scalars(select(AuditLog).where(AuditLog.action.in_(["billing_contract_published", "invoice_drafts_generated", "staff_invited", "criteria_published", "application_import_committed"])).order_by(AuditLog.timestamp.desc()).limit(30))]
    return {"universities": universities, "activity": activity}
