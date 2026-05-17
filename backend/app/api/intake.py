from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.applications import storage_service_dependency
from app.config import AppSettings, get_settings
from app.db.dependencies import get_db
from app.models import Applicant, Application, ApplicationDocument, IntakeJob
from app.schemas.intake import IntakeJobRead, JobStatusResponse
from app.security import UserRole
from app.services.audit import AuditAction, record_audit_log
from app.services.intake_jobs import ensure_intake_worker_started, enqueue_intake_job
from app.services.local_auth import AuthenticatedUser, require_local_roles
from app.services.rubric_store import get_saved_rubric
from app.services.storage import StorageError, StorageService, build_document_storage_path, safe_filename
from app.services.upload_validation import read_upload_with_limit, validate_upload_filename


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["intake-operations"])


@router.post("/upload", response_model=IntakeJobRead, status_code=status.HTTP_201_CREATED)
async def upload_intake_document(
    request: Request,
    file: UploadFile = File(...),
    rubric_id: str = Form(default="default_admissions_rubric"),
    applicant_name: str = Form(default="Applicant"),
    applicant_id: str | None = Form(default=None),
    program_applied: str = Form(default="Undeclared program"),
    intake_term: str = Form(default="Current intake"),
    application_id: str | None = Form(default=None),
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_settings),
    storage_service: StorageService = Depends(storage_service_dependency),
    authenticated: AuthenticatedUser = Depends(
        require_local_roles(UserRole.ADMIN, UserRole.ADMISSIONS_REVIEWER)
    ),
) -> IntakeJobRead:
    original_filename = validate_upload_filename(file.filename)
    content = await read_upload_with_limit(file, max_bytes=settings.max_upload_bytes)
    selected_rubric_id = rubric_id.strip() or "default_admissions_rubric"
    selected_rubric = get_saved_rubric(db, selected_rubric_id)
    if selected_rubric is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Selected rubric was not found.",
        )

    if application_id:
        application = db.get(Application, application_id)
        if application is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Application not found.",
            )
        applicant = application.applicant
    else:
        applicant = _get_or_create_applicant(
            db=db,
            applicant_id=applicant_id,
            applicant_name=applicant_name,
            program_applied=program_applied,
            intake_term=intake_term,
        )
        application = Application(
            applicant_id=applicant.applicant_id,
            submitted_at=datetime.now(timezone.utc),
            processing_status="pending",
            review_status="pending",
            final_human_decision=None,
            reviewer_notes=None,
            processing_errors=[],
            reviewer_audit_metadata=[],
        )
        db.add(application)
        db.flush()

    document_id = str(uuid4())
    cleaned_filename = safe_filename(original_filename)
    storage_path = build_document_storage_path(
        application_id=application.application_id,
        document_id=document_id,
        filename=cleaned_filename,
    )
    try:
        blob_url_or_path = storage_service.save_bytes(storage_path=storage_path, content=content)
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Configured storage backend is not implemented yet.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Configured storage path is not valid.",
        ) from exc
    except StorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Document storage failed.",
        ) from exc

    document = ApplicationDocument(
        document_id=document_id,
        application_id=application.application_id,
        original_filename=cleaned_filename,
        blob_url_or_path=blob_url_or_path,
        document_type=None,
        classification_confidence=None,
        classification_metadata={
            "file_size": len(content),
            "content_type": file.content_type,
            "intake_upload": True,
            "rubric_id": selected_rubric.rubric_id,
            "rubric_name": selected_rubric.name,
        },
        processing_status="uploaded",
        page_count=None,
    )
    db.add(document)
    job = IntakeJob(
        application_id=application.application_id,
        document_id=document.document_id,
        rubric_id=selected_rubric.rubric_id,
        status="queued",
        progress=5,
        status_message="Upload received. Waiting to parse.",
        parser_mode=settings.parser_mode,
        extracted_record={},
        section_analysis=[],
        error_metadata={},
    )
    db.add(job)
    record_audit_log(
        db=db,
        actor=authenticated.actor,
        action=AuditAction.DOCUMENT_UPLOAD,
        application_id=application.application_id,
        document_id=document.document_id,
        new_value={
            "job_id": job.job_id,
            "original_filename": cleaned_filename,
            "storage_path_pattern": "applications/{application_id}/raw/{document_id}/{safe_filename}",
            "file_size_bytes": len(content),
            "processing_status": "queued",
            "rubric_id": selected_rubric.rubric_id,
        },
    )
    db.commit()
    db.refresh(job)

    await ensure_intake_worker_started(request.app, settings)
    await enqueue_intake_job(request.app, job.job_id)

    logger.info("Queued intake job %s for document %s", job.job_id, document.document_id)
    return _job_to_read(job)


@router.get("/jobs/status", response_model=JobStatusResponse)
def list_job_status(
    job_ids: str | None = Query(default=None),
    db: Session = Depends(get_db),
    authenticated: AuthenticatedUser = Depends(
        require_local_roles(UserRole.ADMIN, UserRole.ADMISSIONS_REVIEWER, UserRole.READ_ONLY_AUDITOR)
    ),
) -> JobStatusResponse:
    _ = authenticated
    query = select(IntakeJob).order_by(IntakeJob.received_at.desc())
    requested_ids = _split_job_ids(job_ids)
    if requested_ids:
        query = query.where(IntakeJob.job_id.in_(requested_ids))
    jobs = list(db.scalars(query).unique())
    return JobStatusResponse(jobs=[_job_to_read(job) for job in jobs])


@router.get("/jobs/{job_id}", response_model=IntakeJobRead)
def get_job(
    job_id: str,
    db: Session = Depends(get_db),
    authenticated: AuthenticatedUser = Depends(
        require_local_roles(UserRole.ADMIN, UserRole.ADMISSIONS_REVIEWER, UserRole.READ_ONLY_AUDITOR)
    ),
) -> IntakeJobRead:
    _ = authenticated
    job = db.get(IntakeJob, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    return _job_to_read(job)


@router.get("/jobs/{job_id}/record")
def download_job_record(
    job_id: str,
    db: Session = Depends(get_db),
    authenticated: AuthenticatedUser = Depends(
        require_local_roles(UserRole.ADMIN, UserRole.ADMISSIONS_REVIEWER, UserRole.READ_ONLY_AUDITOR)
    ),
) -> Response:
    job = db.get(IntakeJob, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    if job.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Extracted record is only available after parsing completes.",
        )
    record_audit_log(
        db=db,
        actor=authenticated.actor,
        action=AuditAction.EXTRACTED_RECORD_DOWNLOAD,
        application_id=job.application_id,
        document_id=job.document_id,
        new_value={"job_id": job.job_id, "status": job.status},
    )
    db.commit()
    payload = {
        "job_id": job.job_id,
        "application_id": job.application_id,
        "document_id": job.document_id,
        "extracted_record": job.extracted_record,
        "section_analysis": job.section_analysis,
        "decision_support_only": True,
    }
    return Response(
        content=json.dumps(payload, indent=2, default=str),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{job.job_id}-extracted-record.json"'},
    )


def _get_or_create_applicant(
    *,
    db: Session,
    applicant_id: str | None,
    applicant_name: str,
    program_applied: str,
    intake_term: str,
) -> Applicant:
    cleaned_id = (applicant_id or "").strip()
    if cleaned_id:
        applicant = db.get(Applicant, cleaned_id)
        if applicant is not None:
            applicant.program_applied = program_applied.strip() or applicant.program_applied
            applicant.intake_term = intake_term.strip() or applicant.intake_term
            return applicant

    first_name, last_name = _split_name(applicant_name)
    new_applicant_id = cleaned_id if cleaned_id and len(cleaned_id) <= 36 else str(uuid4())
    email_local = "".join(ch for ch in new_applicant_id.lower() if ch.isalnum()) or uuid4().hex
    applicant = Applicant(
        applicant_id=new_applicant_id,
        first_name=first_name,
        last_name=last_name,
        email=f"{email_local}@intake.local",
        program_applied=program_applied.strip() or "Undeclared program",
        intake_term=intake_term.strip() or "Current intake",
        status="submitted",
    )
    db.add(applicant)
    db.flush()
    return applicant


def _split_name(applicant_name: str) -> tuple[str, str]:
    parts = [part for part in applicant_name.strip().split() if part]
    if not parts:
        return "Applicant", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _split_job_ids(job_ids: str | None) -> list[str]:
    if not job_ids:
        return []
    return [job_id.strip() for job_id in job_ids.split(",") if job_id.strip()]


def _job_to_read(job: IntakeJob) -> IntakeJobRead:
    document = job.document
    application = job.application
    applicant = application.applicant
    extracted_content = document.extracted_content if document is not None else None
    file_size = None
    if document is not None:
        metadata = document.classification_metadata or {}
        raw_file_size = metadata.get("file_size") or metadata.get("file_size_bytes")
        if isinstance(raw_file_size, int):
            file_size = raw_file_size
    return IntakeJobRead(
        job_id=job.job_id,
        application_id=job.application_id,
        document_id=job.document_id,
        rubric_id=job.rubric_id,
        status=job.status,
        progress=job.progress,
        status_message=job.status_message,
        parser_mode=job.parser_mode,
        received_at=job.received_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        applicant_name=f"{applicant.first_name} {applicant.last_name}".strip(),
        applicant_id=applicant.applicant_id,
        program_applied=applicant.program_applied,
        application_status=application.processing_status,
        document_name=document.original_filename,
        file_size=file_size,
        document_type=document.document_type,
        page_count=document.page_count,
        extracted_text=extracted_content.raw_text if extracted_content is not None else None,
        summary=(job.extracted_record or {}).get("summary"),
        section_analysis=job.section_analysis or [],
        extracted_record=job.extracted_record or {},
        record_download_url=f"/api/jobs/{job.job_id}/record" if job.status == "completed" else None,
    )
