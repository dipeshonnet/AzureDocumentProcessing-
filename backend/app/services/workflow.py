"""Admissions operations on the existing SQLAlchemy and document processing stack."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.models import Applicant, Application, IntakeJob, LocalUser, University
from app.models.workflow import WorkflowRecord as Record
from app.schemas.workflow import CaseInput, TemplateInput
from app.services.audit import record_audit_log

MANAGERS = {"university_owner", "admissions_manager"}
READERS = MANAGERS | {"reviewer", "auditor"}
FINANCE = MANAGERS | {"finance_viewer"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def key_hash(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()


def rows(db: Session, tenant: str, kind: str, application_id: str | None = None) -> list[Record]:
    stmt = select(Record).where(Record.university_id == tenant, Record.kind == kind)
    if application_id is not None:
        stmt = stmt.where(Record.application_id == application_id)
    return list(db.scalars(stmt.order_by(Record.created_at, Record.id)))


def find(db: Session, tenant: str, kind: str, key: str, *, required: bool = True) -> Record | None:
    # Row locking serializes related operations on Azure SQL/Postgres; the
    # version column also detects competing updates on SQLite.
    item = db.scalar(select(Record).where(Record.university_id == tenant, Record.kind == kind, Record.resource_key == key).with_for_update())
    if item is None and required:
        raise HTTPException(404, "Record not found in this workspace.")
    return item


def add(db: Session, tenant: str, kind: str, payload: dict, *, key: str | None = None, application_id: str | None = None) -> Record:
    item = Record(id=str(uuid4()), university_id=tenant, kind=kind, resource_key=key or str(uuid4()), application_id=application_id, payload=payload)
    db.add(item)
    db.flush()
    return item


def change(item: Record, **values) -> None:
    item.payload = {**item.payload, **values}


def view(item: Record) -> dict:
    created_at = item.created_at if item.created_at.tzinfo else item.created_at.replace(tzinfo=timezone.utc)
    return {**item.payload, "id": item.resource_key, "revision": item.revision, "created_at": created_at.isoformat()}


def check_revision(item: Record, revision: int | None) -> None:
    if revision is None or item.revision != revision:
        raise HTTPException(409, "This record changed. Reload before saving.")


def commit(db: Session) -> None:
    try:
        db.commit()
    except (IntegrityError, StaleDataError) as exc:
        db.rollback()
        raise HTTPException(409, "Another operation changed this record or identifier. Reload and try again.") from exc


def audit(db: Session, authenticated, action: str, application_id: str | None = None, **metadata) -> None:
    record_audit_log(db=db, actor=authenticated.actor, action=action, application_id=application_id,
                     metadata={"university_id": authenticated.university_id, **metadata})


def membership(db: Session, tenant: str, user: LocalUser) -> Record | None:
    member = find(db, tenant, "membership", user.user_id, required=False)
    if member is None and user.university_id == tenant:
        role = {"superadmin": "university_owner", "admin": "admissions_manager", "admissions_reviewer": "reviewer", "read_only_auditor": "auditor", "finance_viewer": "finance_viewer"}.get(user.role)
        if role:
            member = add(db, tenant, "membership", {"user_id": user.user_id, "role": role, "status": "active", "program_ids": [], "department_ids": [], "workload_limit": 20}, key=user.user_id)
    return member


def role_for(db: Session, authenticated) -> str:
    member = membership(db, authenticated.university_id, authenticated.user)
    if member and member.payload["status"] == "active":
        return member.payload["role"]
    if authenticated.user.role == "superadmin":
        return "university_owner"
    raise HTTPException(403, "Active workspace membership required.")


def allow(db: Session, authenticated, roles: set[str]) -> str:
    role = role_for(db, authenticated)
    if role not in roles:
        raise HTTPException(403, "Your workspace role cannot perform this action.")
    return role


def case_access(db: Session, authenticated, case_id: str, *, manage: bool = False) -> tuple[Application, Record]:
    role = allow(db, authenticated, MANAGERS if manage else READERS)
    application = db.get(Application, case_id)
    if application is None or application.university_id != authenticated.university_id:
        raise HTTPException(404, "Application not found in this workspace.")
    case = find(db, authenticated.university_id, "case", case_id)
    if role == "reviewer" and find(db, authenticated.university_id, "assignment", key_hash(case_id, authenticated.user.user_id), required=False) is None:
        raise HTTPException(403, "Only assigned reviewers can access this case.")
    return application, case


def settings_for(db: Session, tenant: str) -> dict:
    settings = find(db, tenant, "settings", "default", required=False)
    university = db.get(University, tenant)
    return settings.payload if settings else {"brand_name": university.name, "brand_color": "#216b58", "secure_link_expiry_days": 7, "fictional_example_enabled": False, "external_processing_approved": False}


def program_template(db: Session, tenant: str, program_id: str) -> tuple[Record, Record]:
    program = find(db, tenant, "program", program_id)
    active = program.payload.get("active_version_id")
    if not active:
        raise HTTPException(400, "Publish program criteria before creating applications.")
    return program, find(db, tenant, "template", active)


def save_template(db: Session, authenticated, program_id: str, payload: TemplateInput) -> Record:
    tenant = authenticated.university_id
    find(db, tenant, "program", program_id)
    versions = [r for r in rows(db, tenant, "template") if r.payload["program_id"] == program_id]
    draft = next((r for r in versions if r.payload["status"] == "draft"), None)
    data = payload.model_dump(exclude={"revision"})
    if draft:
        check_revision(draft, payload.revision)
        change(draft, **data)
    else:
        version = max((r.payload["version"] for r in versions), default=0) + 1
        draft = add(db, tenant, "template", {**data, "program_id": program_id, "version": version, "status": "draft"}, key=key_hash(program_id, str(version)))
    audit(db, authenticated, "criteria_draft_saved", program_id=program_id)
    return draft


def publish_template(db: Session, authenticated, version_id: str, revision: int) -> Record:
    template = find(db, authenticated.university_id, "template", version_id)
    check_revision(template, revision)
    if template.payload["status"] != "draft":
        raise HTTPException(409, "Published criteria are immutable. Create a new draft.")
    TemplateInput.model_validate({key: template.payload[key] for key in ("name", "requirements", "criteria")})
    if not template.payload["requirements"]:
        raise HTTPException(400, "Add at least one requirement before publishing.")
    change(template, status="published", published_at=now(), published_by=authenticated.user.user_id)
    program = find(db, authenticated.university_id, "program", template.payload["program_id"])
    change(program, active_version_id=version_id)
    audit(db, authenticated, "criteria_published", version_id=version_id)
    return template


def aliases_for(db: Session, tenant: str, candidate_id: str) -> list[dict]:
    return [view(r) for r in rows(db, tenant, "alias") if r.payload["candidate_id"] == candidate_id]


def link_alias(db: Session, tenant: str, candidate_id: str, source: str, value: str) -> Record:
    key = key_hash(source, value)
    existing = find(db, tenant, "alias", key, required=False)
    if existing:
        if existing.payload["candidate_id"] != candidate_id:
            raise HTTPException(409, "This university system ID belongs to another candidate.")
        return existing
    return add(db, tenant, "alias", {"source": source, "value": value, "candidate_id": candidate_id}, key=key)


def create_case(db: Session, authenticated, payload: CaseInput) -> tuple[Application, Record]:
    tenant = authenticated.university_id
    program, template = program_template(db, tenant, payload.program_id)
    if payload.department_id:
        find(db, tenant, "department", payload.department_id)
    if payload.external_application_id and any(r.payload.get("external_application_id") == payload.external_application_id for r in rows(db, tenant, "case")):
        raise HTTPException(409, "External application ID already exists in this workspace.")
    alias = find(db, tenant, "alias", key_hash(payload.source, payload.external_candidate_id), required=False)
    candidate = db.get(Applicant, alias.payload["candidate_id"]) if alias else None
    if payload.candidate_id:
        candidate = db.get(Applicant, payload.candidate_id)
        if candidate is None or not db.scalar(select(Application.application_id).where(Application.applicant_id == payload.candidate_id, Application.university_id == tenant)):
            raise HTTPException(404, "Candidate not found in this workspace.")
        if alias and alias.payload["candidate_id"] != payload.candidate_id:
            raise HTTPException(409, "Candidate ID conflicts with the selected candidate.")
    if candidate is None:
        first, _, last = payload.applicant_name.partition(" ")
        candidate = Applicant(first_name=first, last_name=last, email=f"{uuid4().hex}@intake.local", student_unique_id=payload.external_candidate_id,
                              program_applied=program.payload["name"], intake_term=payload.intake_term, status="submitted")
        db.add(candidate)
        db.flush()
    link_alias(db, tenant, candidate.applicant_id, payload.source, payload.external_candidate_id)
    application = Application(applicant_id=candidate.applicant_id, university_id=tenant, program_applied=program.payload["name"], intake_term=payload.intake_term, submitted_at=datetime.now(timezone.utc))
    db.add(application)
    db.flush()
    if payload.external_application_id:
        add(db, tenant, "external_application", {"external_id": payload.external_application_id, "candidate_id": candidate.applicant_id},
            key=key_hash(payload.external_application_id), application_id=application.application_id)
    case = add(db, tenant, "case", {"candidate_id": candidate.applicant_id, "applicant_name": payload.applicant_name,
        "external_candidate_id": payload.external_candidate_id, "external_application_id": payload.external_application_id,
        "source": payload.source, "program_id": payload.program_id, "program_name": program.payload["name"], "intake_term": payload.intake_term,
        "department_id": payload.department_id, "template_id": template.resource_key, "template_version": template.payload["version"],
        "case_revision": 1, "status": "awaiting_documents", "external_status": "submitted"}, key=application.application_id, application_id=application.application_id)
    for definition in template.payload["requirements"]:
        due = (datetime.now(timezone.utc) + timedelta(days=definition["due_days"])).isoformat() if definition["due_days"] is not None else None
        add(db, tenant, "requirement", {**definition, "status": "missing", "due_at": due, "waiver_reason": "", "value": None}, application_id=application.application_id)
    audit(db, authenticated, "application_created", application.application_id, template_id=template.resource_key)
    return application, case


def evidence_changed(db: Session, tenant: str, application_id: str) -> None:
    case = find(db, tenant, "case", application_id)
    change(case, case_revision=case.payload["case_revision"] + 1)
    # Submitted review records remain immutable; freshness is derived from the
    # recorded case revision. Their scores and provenance are never overwritten.


def requirement_values(requirement: Record) -> list[dict]:
    if "values" in requirement.payload:
        return requirement.payload["values"]
    value = requirement.payload.get("value")
    return [{"slot": 1, "value": value, "verified_by": requirement.payload.get("verified_by", ""),
             "verified_at": requirement.payload.get("verified_at")}] if value is not None else []


def readiness(db: Session, tenant: str, case: Record) -> dict:
    requirements = rows(db, tenant, "requirement", case.resource_key)
    documents = rows(db, tenant, "document", case.resource_key)
    if not case.payload.get("template_id"):
        return {"completeness": 0, "missing_requirements": 0, "status": "legacy", "document_count": len(documents), "current_document_count": len(documents)}
    for requirement in requirements:
        current = [d for d in documents if d.payload["requirement_id"] == requirement.resource_key and d.payload["current"]]
        completed = sum(1 for d in current if (job := db.get(IntakeJob, d.payload["job_id"])) and job.status == "completed")
        if requirement.payload["waiver_reason"]:
            status = "waived"
        elif requirement.payload["capture_type"] != "document":
            values = requirement_values(requirement)
            status = "accepted" if values and len(values) >= requirement.payload["min_count"] else "missing"
        elif completed >= requirement.payload["min_count"] and (completed > 0 or not requirement.payload["required"]):
            status = "accepted"
        elif any(db.get(IntakeJob, d.payload["job_id"]).status in {"queued", "processing"} for d in current):
            status = "processing"
        else:
            status = "missing"
        requirement.payload = {**requirement.payload, "status": status} if requirement.payload["status"] != status else requirement.payload
    blocking = [r for r in requirements if r.payload["required"]]
    fulfilled = sum(r.payload["status"] in {"accepted", "waived"} for r in blocking)
    completeness = round(100 * fulfilled / len(blocking)) if blocking else 100
    assignments = rows(db, tenant, "assignment", case.resource_key)
    reviews = rows(db, tenant, "review", case.resource_key)
    fresh = {r.payload["reviewer_id"] for r in reviews if r.payload["case_revision"] == case.payload["case_revision"]}
    if case.payload.get("external_status") == "withdrawn":
        state = "withdrawn"
    elif reviews and any(r.payload["case_revision"] != case.payload["case_revision"] for r in reviews) and not all(a.payload["reviewer_id"] in fresh for a in assignments):
        state = "evidence_changed"
    elif completeness < 100:
        state = "awaiting_documents"
    elif assignments and all(a.payload["reviewer_id"] in fresh for a in assignments):
        state = "review_complete"
    elif assignments:
        state = "in_review"
    else:
        state = "ready_for_review"
    if case.payload["status"] != state:
        change(case, status=state)
    # Return revisions that include derived status updates; otherwise the next
    # UI action would submit the pre-commit requirement version and conflict.
    db.flush()
    return {"completeness": completeness, "missing_requirements": len(blocking) - fulfilled, "status": state,
        "document_count": len(documents), "current_document_count": sum(d.payload["current"] for d in documents)}


def migrate_legacy(db: Session) -> None:
    """Add scopes and a read-only legacy case shell; never invent requirements."""
    applications = list(db.scalars(select(Application)))
    for user in db.scalars(select(LocalUser)):
        if user.university_id:
            membership(db, user.university_id, user)
    # Historical globally matched candidates are split when shared by tenants.
    ownership: dict[str, str] = {}
    copies: dict[tuple[str, str], str] = {}
    for application in applications:
        tenant = application.university_id
        if not tenant:
            continue
        original_id = application.applicant_id
        if original_id in ownership and ownership[original_id] != tenant:
            if (original_id, tenant) not in copies:
                old = application.applicant
                copy = Applicant(student_unique_id=old.student_unique_id, first_name=old.first_name, last_name=old.last_name,
                    email=f"{uuid4().hex}@intake.local", program_applied=old.program_applied, intake_term=old.intake_term, status=old.status)
                db.add(copy)
                db.flush()
                copies[(original_id, tenant)] = copy.applicant_id
            application.applicant_id = copies[(original_id, tenant)]
        else:
            ownership[original_id] = tenant
        applicant = db.get(Applicant, application.applicant_id)
        if applicant.student_unique_id:
            existing = find(db, tenant, "alias", key_hash("sis", applicant.student_unique_id), required=False)
            if not existing:
                link_alias(db, tenant, applicant.applicant_id, "sis", applicant.student_unique_id)
        if find(db, tenant, "case", application.application_id, required=False) is None:
            add(db, tenant, "case", {"candidate_id": applicant.applicant_id, "applicant_name": f"{applicant.first_name} {applicant.last_name}".strip(),
                "external_candidate_id": applicant.student_unique_id or "", "external_application_id": "", "source": "sis",
                "program_id": None, "program_name": application.program_applied or applicant.program_applied,
                "intake_term": application.intake_term or applicant.intake_term, "department_id": None, "template_id": None,
                "template_version": None, "case_revision": 1, "status": "legacy", "external_status": "submitted"},
                key=application.application_id, application_id=application.application_id)
    db.flush()
