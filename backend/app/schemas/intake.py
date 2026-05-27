from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


JobStatus = Literal["queued", "processing", "completed", "failed"]
UserRoleValue = Literal["superadmin", "admin", "admissions_reviewer", "read_only_auditor"]


class IntakeSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AuthUserRead(IntakeSchema):
    user_id: str
    email: str
    role: UserRoleValue
    billing_rate_per_unit: float = 1.0
    university_logo_data_url: str | None = None
    university_id: str | None = None
    pages_per_billable_unit: int = 10


class AuthResponse(IntakeSchema):
    token: str
    user: AuthUserRead


class LoginRequest(IntakeSchema):
    email: str = Field(min_length=1)
    password: str = Field(min_length=1)


class RegisterRequest(IntakeSchema):
    email: str = Field(min_length=1)
    password: str = Field(min_length=8)
    confirm_password: str = Field(min_length=8)

    @model_validator(mode="after")
    def passwords_must_match(self) -> RegisterRequest:
        if self.password != self.confirm_password:
            raise ValueError("passwords must match")
        return self


class PasswordChangeRequest(IntakeSchema):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8)
    confirm_new_password: str = Field(min_length=8)

    @model_validator(mode="after")
    def passwords_must_match(self) -> PasswordChangeRequest:
        if self.new_password != self.confirm_new_password:
            raise ValueError("new passwords must match")
        return self


class ProfileLogoUpdate(IntakeSchema):
    university_logo_data_url: str | None = Field(default=None, max_length=750_000)


class SectionAnalysisRead(IntakeSchema):
    section_id: str
    label: str
    score: float
    max_score: float
    evidence: list[str] = Field(default_factory=list)
    rubric_criteria: list[str] = Field(default_factory=list)
    status: str = "review"


class IntakeJobRead(IntakeSchema):
    job_id: str
    application_id: str
    document_id: str
    rubric_id: str
    status: JobStatus
    progress: int
    status_message: str
    parser_mode: str
    received_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    applicant_name: str
    applicant_id: str
    student_unique_id: str | None = None
    program_applied: str
    intake_term: str
    application_status: str
    document_name: str
    file_size: int | None = None
    document_type: str | None = None
    page_count: int | None = None
    extracted_text: str | None = None
    summary: str | None = None
    section_analysis: list[SectionAnalysisRead] = Field(default_factory=list)
    extracted_record: dict[str, Any] = Field(default_factory=dict)
    record_download_url: str | None = None


class JobStatusResponse(IntakeSchema):
    jobs: list[IntakeJobRead]


class ManagedUserRead(IntakeSchema):
    user_id: str
    email: str
    role: UserRoleValue
    verification_status: str = "verified"
    document_count: int = 0
    rubric_count: int = 0
    billing_rate_per_unit: float = 1.0
    university_logo_data_url: str | None = None
    university_id: str | None = None
    created_at: datetime
    last_login_at: datetime | None = None


class BillingRateUpdate(IntakeSchema):
    billing_rate_per_unit: float = Field(ge=0)


class RubricRow(IntakeSchema):
    row_id: str
    label: str = Field(min_length=1)
    condition: str = ""
    points: str = ""
    notes: str = ""


class RubricComponent(IntakeSchema):
    component_id: str
    name: str = Field(min_length=1)
    description: str = ""
    max_points: float = Field(default=0, ge=0)
    rows: list[RubricRow] = Field(default_factory=list)


class RubricSectionDefinition(IntakeSchema):
    section_id: str
    name: str = Field(min_length=1)
    description: str = ""
    max_points: float = Field(default=0, ge=0)
    components: list[RubricComponent] = Field(default_factory=list)


class SavedRubricBase(IntakeSchema):
    name: str = Field(min_length=1)
    description: str = ""
    total_points: float = Field(default=100, gt=0)
    sections: list[RubricSectionDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def section_points_should_not_exceed_total(self) -> SavedRubricBase:
        section_total = sum(section.max_points for section in self.sections)
        if section_total > self.total_points + 0.0001:
            raise ValueError("section max points cannot exceed total points")
        section_ids = [section.section_id for section in self.sections]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("section IDs must be unique")
        return self


class SavedRubricCreate(SavedRubricBase):
    rubric_id: str | None = None


class SavedRubricUpdate(SavedRubricBase):
    version: int = Field(default=1, ge=1)
    is_active: bool = True


class SavedRubricRead(SavedRubricUpdate):
    rubric_id: str
    created_at: datetime
    updated_at: datetime


class RubricStorageDiagnostics(IntakeSchema):
    database_dialect: str
    saved_rubrics_table_exists: bool
    rubric_count: int
    rubric_ids: list[str]


class UniversityCreate(BaseModel):
    name: str = Field(min_length=1)
    admin_email: str | None = None


class UniversityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    university_id: str
    name: str
    logo_data_url: str | None = None
    pages_per_billable_unit: int
    api_key: str
    webhook_url: str | None = None
    created_at: datetime


class UniversityIntegrationUpdate(BaseModel):
    webhook_url: str | None = None


class ReviewerCreateRequest(BaseModel):
    email: str = Field(min_length=1)
    password: str = Field(min_length=8)
