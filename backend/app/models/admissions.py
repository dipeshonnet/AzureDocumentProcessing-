from __future__ import annotations

import secrets
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _uuid() -> str:
    return str(uuid4())


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _generate_api_key() -> str:
    return secrets.token_hex(32)


class University(Base):
    __tablename__ = "universities"

    university_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    logo_data_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    pages_per_billable_unit: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    api_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True, default=_generate_api_key)
    webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)


class Applicant(Base):
    __tablename__ = "applicants"

    applicant_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    student_unique_id: Mapped[str | None] = mapped_column(String(120), nullable=True, unique=True, index=True)
    first_name: Mapped[str] = mapped_column(String(120), nullable=False)
    last_name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    program_applied: Mapped[str] = mapped_column(String(200), nullable=False)
    intake_term: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="draft")

    applications: Mapped[list[Application]] = relationship(
        back_populates="applicant",
        cascade="all, delete-orphan",
    )


class Application(Base):
    __tablename__ = "applications"

    application_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    applicant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("applicants.applicant_id"),
        nullable=False,
        index=True,
    )
    university_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("universities.university_id"),
        nullable=True,
        index=True,
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    program_applied: Mapped[str | None] = mapped_column(String(200), nullable=True)
    intake_term: Mapped[str | None] = mapped_column(String(80), nullable=True)
    processing_status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    review_status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    final_human_decision: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reviewer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    processing_errors: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    reviewer_audit_metadata: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)

    university: Mapped[University | None] = relationship()
    applicant: Mapped[Applicant] = relationship(back_populates="applications")
    documents: Mapped[list[ApplicationDocument]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
    )
    rubric_scores: Mapped[list[RubricCriterionScore]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
    )
    scorecard: Mapped[RubricScorecard | None] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        uselist=False,
    )


class ApplicationDocument(Base):
    __tablename__ = "application_documents"

    document_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    application_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("applications.application_id"),
        nullable=False,
        index=True,
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    blob_url_or_path: Mapped[str] = mapped_column(Text, nullable=False)
    document_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    classification_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    classification_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    processing_status: Mapped[str] = mapped_column(String(50), nullable=False, default="uploaded")
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)

    application: Mapped[Application] = relationship(back_populates="documents")
    extracted_content: Mapped[ExtractedDocumentContent | None] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        uselist=False,
    )
    summary: Mapped[DocumentSummary | None] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        uselist=False,
    )


class ExtractedDocumentContent(Base):
    __tablename__ = "extracted_document_contents"

    document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("application_documents.document_id"),
        primary_key=True,
    )
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    pages: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    tables: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    key_value_pairs: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    extraction_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    extraction_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    structured_extraction: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    document: Mapped[ApplicationDocument] = relationship(back_populates="extracted_content")


class DocumentSummary(Base):
    __tablename__ = "document_summaries"

    document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("application_documents.document_id"),
        primary_key=True,
    )
    short_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    section_summaries: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    strengths: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    concerns: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    missing_information: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    reviewer_attention_points: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    evidence: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    summary_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    document: Mapped[ApplicationDocument] = relationship(back_populates="summary")


class RubricCriterionScore(Base):
    __tablename__ = "rubric_criterion_scores"

    score_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    criterion_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True, default=_uuid)
    application_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("applications.application_id"),
        nullable=False,
        index=True,
    )
    criterion_name: Mapped[str] = mapped_column(String(200), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    max_score: Mapped[float] = mapped_column(Float, nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    supporting_evidence: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    missing_information: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    requires_human_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    risk_flags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    reviewer_override: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    scoring_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    application: Mapped[Application] = relationship(back_populates="rubric_scores")


class RubricScorecard(Base):
    __tablename__ = "rubric_scorecards"

    application_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("applications.application_id"),
        primary_key=True,
    )
    weighted_score: Mapped[float] = mapped_column(Float, nullable=False)
    max_score: Mapped[float] = mapped_column(Float, nullable=False)
    recommendation_band: Mapped[str] = mapped_column(String(80), nullable=False)
    decision_support_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer_override: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    requires_human_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    risk_flags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)

    application: Mapped[Application] = relationship(back_populates="scorecard")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    audit_log_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(80), nullable=False)
    action: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    application_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    old_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    new_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)


class LocalUser(Base):
    __tablename__ = "local_users"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    university_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("universities.university_id"),
        nullable=True,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(80), nullable=False, default="admissions_reviewer")
    billing_rate_per_unit: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    university_logo_data_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    university: Mapped[University | None] = relationship()
    sessions: Mapped[list[AuthSession]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    token: Mapped[str] = mapped_column(String(160), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("local_users.user_id"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)

    user: Mapped[LocalUser] = relationship(back_populates="sessions")


class IntakeJob(Base):
    __tablename__ = "intake_jobs"

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    application_id: Mapped[str] = mapped_column(String(36), ForeignKey("applications.application_id"), nullable=False, index=True)
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("application_documents.document_id"), nullable=False, index=True)
    rubric_id: Mapped[str] = mapped_column(String(160), nullable=False, default="default_admissions_rubric")
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status_message: Mapped[str] = mapped_column(Text, nullable=False, default="Queued for parsing.")
    parser_mode: Mapped[str] = mapped_column(String(40), nullable=False, default="mock")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    extracted_record: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    section_analysis: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    error_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    application: Mapped[Application] = relationship()
    document: Mapped[ApplicationDocument] = relationship()


class SavedRubric(Base):
    __tablename__ = "saved_rubrics"

    rubric_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    university_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("universities.university_id"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    total_points: Mapped[float] = mapped_column(Float, nullable=False, default=100)
    sections: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now)

    university: Mapped[University | None] = relationship()
