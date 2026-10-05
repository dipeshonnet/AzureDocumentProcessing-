"""Tenant-scoped operations records, additive to the existing processing schema.

Kinds are written only through domain services, never through a generic API.
The version column provides optimistic locking on SQLite as well as Azure SQL.
Published templates, evidence versions, reviews and usage snapshots are append-only.
"""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WorkflowRecord(Base):
    __tablename__ = "admissions_workflow_records"
    __table_args__ = (
        UniqueConstraint("university_id", "kind", "resource_key", name="uq_workflow_resource"),
        Index("ix_workflow_case", "university_id", "application_id", "kind"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    university_id: Mapped[str] = mapped_column(String(36), ForeignKey("universities.university_id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_key: Mapped[str] = mapped_column(String(160), nullable=False)
    application_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    __mapper_args__ = {"version_id_col": revision}
