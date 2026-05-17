from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppSettings
from app.db.init_db import init_db
from app.db.session import create_db_engine, create_session_factory, get_database_url
from app.models import ApplicationDocument, ExtractedDocumentContent, IntakeJob, RubricCriterionScore, RubricScorecard
from app.security import system_actor
from app.services.audit import AuditAction, record_audit_log
from app.services.application_processing import (
    ApplicationProcessingApplicationNotFoundError,
    ApplicationProcessingConfigurationError,
    ApplicationProcessingNoDocumentsError,
    ApplicationProcessingService,
)
from app.services.intake_parser import IntakeParserError, get_intake_parser_service
from app.services.local_auth import ensure_demo_user


logger = logging.getLogger(__name__)
INTAKE_TERMINAL_STATUSES = {"completed", "failed"}


async def ensure_intake_worker_started(app: FastAPI, settings: AppSettings) -> None:
    if not settings.intake_worker_enabled:
        return
    database_url = get_database_url(settings)
    current_url = getattr(app.state, "intake_database_url", None)
    task = getattr(app.state, "intake_worker_task", None)
    if current_url == database_url and task is not None and not task.done():
        return
    await stop_intake_worker(app)

    engine = create_db_engine(database_url)
    if database_url.startswith("sqlite") or settings.auto_create_db_schema:
        init_db(engine)
    session_factory = create_session_factory(engine)
    queue: asyncio.Queue[str] = asyncio.Queue()

    with session_factory() as db:
        ensure_demo_user(db)
        queued_jobs = reset_interrupted_jobs(db)
        for job_id in queued_jobs:
            queue.put_nowait(job_id)

    app.state.intake_database_url = database_url
    app.state.intake_queue = queue
    app.state.intake_session_factory = session_factory
    app.state.intake_worker_task = asyncio.create_task(
        intake_worker_loop(queue=queue, session_factory=session_factory, settings=settings)
    )


async def stop_intake_worker(app: FastAPI) -> None:
    task = getattr(app.state, "intake_worker_task", None)
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    app.state.intake_worker_task = None


async def enqueue_intake_job(app: FastAPI, job_id: str) -> None:
    queue = getattr(app.state, "intake_queue", None)
    if queue is None:
        raise RuntimeError("Intake worker queue is not initialized.")
    queue.put_nowait(job_id)


def reset_interrupted_jobs(db: Session) -> list[str]:
    jobs = list(
        db.scalars(
            select(IntakeJob).where(IntakeJob.status.in_(["queued", "processing"]))
        )
    )
    for job in jobs:
        if job.status == "processing":
            job.status = "queued"
            job.progress = 0
            job.started_at = None
            job.status_message = "Processing was interrupted. Waiting to restart."
    db.commit()
    return [job.job_id for job in jobs]


async def intake_worker_loop(
    *,
    queue: asyncio.Queue[str],
    session_factory: sessionmaker[Session],
    settings: AppSettings,
) -> None:
    while True:
        job_id = await queue.get()
        try:
            await process_intake_job(job_id=job_id, session_factory=session_factory, settings=settings)
        except Exception:
            logger.exception("Unhandled intake worker error for job %s", job_id)
        finally:
            queue.task_done()


async def process_intake_job(
    *,
    job_id: str,
    session_factory: sessionmaker[Session],
    settings: AppSettings,
) -> None:
    with session_factory() as db:
        job = db.get(IntakeJob, job_id)
        if job is None or job.status in INTAKE_TERMINAL_STATUSES:
            return
        job.status = "processing"
        job.progress = 35
        job.started_at = datetime.now(timezone.utc)
        job.status_message = "Parsing document."
        db.commit()

    if settings.intake_mock_processing_delay_seconds and job.parser_mode.strip().lower() == "mock":
        await asyncio.sleep(settings.intake_mock_processing_delay_seconds)

    with session_factory() as db:
        job = db.get(IntakeJob, job_id)
        if job is None:
            return
        if job.parser_mode.strip().lower() in {"azure", "live"}:
            process_live_intake_job(db=db, job=job, settings=settings)
            return

        document = db.get(ApplicationDocument, job.document_id)
        if document is None:
            mark_failed(db, job, "Document record was not found.", {"error_type": "DocumentNotFound"})
            return
        application = job.application
        applicant = application.applicant
        try:
            parser = get_intake_parser_service(job.parser_mode)
            parsed = parser.parse(job=job, applicant=applicant, application=application, document=document)
        except IntakeParserError as exc:
            mark_failed(db, job, str(exc), {"error_type": exc.__class__.__name__})
            return
        except Exception as exc:
            mark_failed(db, job, "Parser failed unexpectedly.", {"error_type": exc.__class__.__name__})
            return

        document.document_type = parsed.document_type
        document.classification_confidence = 0.92
        document.classification_metadata = {
            **(document.classification_metadata or {}),
            "source": "mock_intake_parser",
            "file_size": (document.classification_metadata or {}).get("file_size"),
            "requires_human_review": False,
        }
        document.processing_status = "completed"
        document.page_count = 1
        existing = db.get(ExtractedDocumentContent, document.document_id)
        if existing is None:
            db.add(
                ExtractedDocumentContent(
                    document_id=document.document_id,
                    raw_text=parsed.extracted_text,
                    pages=[{"page_number": 1, "text": parsed.extracted_text, "lines": [], "words": []}],
                    tables=[],
                    key_value_pairs={"values": {}, "pairs": []},
                    extraction_confidence=0.97,
                    extraction_metadata={"provider": "mock_intake_parser", "parser_mode": job.parser_mode},
                    structured_extraction={"document_type": parsed.document_type, "extraction": parsed.record},
                )
            )
        else:
            existing.raw_text = parsed.extracted_text
            existing.pages = [{"page_number": 1, "text": parsed.extracted_text, "lines": [], "words": []}]
            existing.extraction_confidence = 0.97
            existing.extraction_metadata = {"provider": "mock_intake_parser", "parser_mode": job.parser_mode}
            existing.structured_extraction = {"document_type": parsed.document_type, "extraction": parsed.record}

        job.status = "completed"
        job.progress = 100
        job.status_message = "Parsing completed."
        job.finished_at = datetime.now(timezone.utc)
        job.extracted_record = parsed.record
        job.section_analysis = parsed.sections
        job.error_metadata = {}
        application.processing_status = "ready_for_review"
        application.review_status = "pending"
        record_audit_log(
            db=db,
            actor=system_actor(),
            action=AuditAction.INTAKE_PARSE,
            application_id=application.application_id,
            document_id=document.document_id,
            new_value={
                "job_id": job.job_id,
                "status": job.status,
                "document_type": parsed.document_type,
                "parser_mode": job.parser_mode,
                "section_count": len(parsed.sections),
            },
        )
        db.commit()


def process_live_intake_job(*, db: Session, job: IntakeJob, settings: AppSettings) -> None:
    try:
        processing_service = ApplicationProcessingService(db=db, settings=settings)
        outcome = processing_service.process_application(
            job.application_id,
            force=False,
            actor=system_actor(),
        )
    except (
        ApplicationProcessingApplicationNotFoundError,
        ApplicationProcessingConfigurationError,
        ApplicationProcessingNoDocumentsError,
    ) as exc:
        mark_failed(db, job, safe_job_error_message(exc), {"error_type": exc.__class__.__name__})
        return
    except Exception as exc:
        logger.exception("Live intake processing failed for job %s", job.job_id)
        mark_failed(db, job, "Live processing failed unexpectedly.", {"error_type": exc.__class__.__name__})
        return

    refreshed_job = db.get(IntakeJob, job.job_id)
    if refreshed_job is None:
        return
    document = db.get(ApplicationDocument, refreshed_job.document_id)
    application = outcome.application
    if document is None:
        mark_failed(db, refreshed_job, "Document record was not found.", {"error_type": "DocumentNotFound"})
        return

    document_errors = [
        error for error in (application.processing_errors or [])
        if error.get("document_id") == document.document_id
    ]
    if document.processing_status == "failed" or application.processing_status == "failed":
        relevant_errors = document_errors or application.processing_errors or []
        message = relevant_errors[0]["message"] if relevant_errors else "Live processing failed."
        mark_failed(
            db,
            refreshed_job,
            message,
            {
                "error_type": relevant_errors[0].get("error_type", "ApplicationProcessingError")
                if relevant_errors
                else "ApplicationProcessingError",
                "processing_errors": application.processing_errors or [],
            },
        )
        return

    refreshed_job.status = "completed"
    refreshed_job.progress = 100
    refreshed_job.status_message = "Live processing completed."
    refreshed_job.finished_at = datetime.now(timezone.utc)
    refreshed_job.extracted_record = live_extracted_record(
        job=refreshed_job,
        document=document,
        scorecard=outcome.scorecard,
    )
    refreshed_job.section_analysis = live_section_analysis(outcome.score_rows)
    refreshed_job.error_metadata = {
        "processing_status": application.processing_status,
        "processing_errors": application.processing_errors or [],
    }
    record_audit_log(
        db=db,
        actor=system_actor(),
        action=AuditAction.INTAKE_PARSE,
        application_id=refreshed_job.application_id,
        document_id=refreshed_job.document_id,
        new_value={
            "job_id": refreshed_job.job_id,
            "status": refreshed_job.status,
            "parser_mode": refreshed_job.parser_mode,
            "processing_status": application.processing_status,
            "section_count": len(refreshed_job.section_analysis or []),
        },
    )
    db.commit()


def live_extracted_record(
    *,
    job: IntakeJob,
    document: ApplicationDocument,
    scorecard: RubricScorecard | None,
) -> dict[str, Any]:
    application = job.application
    applicant = application.applicant
    extracted = document.extracted_content
    summary = document.summary
    structured = extracted.structured_extraction if extracted else {}
    return {
        "applicant": {
            "applicant_id": applicant.applicant_id,
            "name": f"{applicant.first_name} {applicant.last_name}".strip(),
            "program_applied": applicant.program_applied,
            "intake_term": applicant.intake_term,
        },
        "application": {
            "application_id": application.application_id,
            "status": application.processing_status,
            "review_status": application.review_status,
        },
        "document": {
            "document_id": document.document_id,
            "filename": document.original_filename,
            "document_type": document.document_type,
            "page_count": document.page_count,
            "classification_confidence": document.classification_confidence,
            "classification_metadata": document.classification_metadata or {},
        },
        "summary": summary.short_summary if summary else None,
        "structured_extraction": structured,
        "scorecard": {
            "weighted_score": scorecard.weighted_score,
            "max_score": scorecard.max_score,
            "recommendation_band": scorecard.recommendation_band,
            "requires_human_review": scorecard.requires_human_review,
            "risk_flags": scorecard.risk_flags,
        }
        if scorecard is not None
        else None,
        "decision_support_only": True,
    }


def live_section_analysis(scores: list[RubricCriterionScore]) -> list[dict[str, Any]]:
    sections = []
    for score in scores:
        evidence = [
            str(item.get("snippet"))
            for item in (score.supporting_evidence or [])
            if item.get("snippet")
        ]
        if not evidence and score.missing_information:
            evidence = list(score.missing_information)
        sections.append(
            {
                "section_id": score.criterion_id,
                "label": score.criterion_name,
                "score": score.score,
                "max_score": score.max_score,
                "evidence": evidence,
                "rubric_criteria": [score.criterion_name],
                "status": "human_review" if score.requires_human_review else "review",
            }
        )
    return sections


def safe_job_error_message(exc: Exception) -> str:
    message = str(exc).strip()
    return message or exc.__class__.__name__


def mark_failed(db: Session, job: IntakeJob, message: str, metadata: dict[str, Any]) -> None:
    job.status = "failed"
    job.progress = 100
    job.status_message = message
    job.finished_at = datetime.now(timezone.utc)
    job.error_metadata = metadata
    if job.document:
        job.document.processing_status = "failed"
    record_audit_log(
        db=db,
        actor=system_actor(),
        action=AuditAction.INTAKE_PARSE,
        application_id=job.application_id,
        document_id=job.document_id,
        new_value={
            "job_id": job.job_id,
            "status": job.status,
            "parser_mode": job.parser_mode,
            "error_type": metadata.get("error_type"),
        },
    )
    db.commit()
