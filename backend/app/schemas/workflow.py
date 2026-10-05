from typing import Literal
import math
import re

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


WorkspaceRole = Literal["university_owner", "admissions_manager", "reviewer", "finance_viewer", "auditor"]


class RequirementDefinition(Input):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")
    label: str = Field(min_length=1, max_length=200)
    capture_type: Literal["document", "number", "text", "external"] = "document"
    required: bool = True
    min_count: int = Field(default=1, ge=0, le=20)
    max_count: int = Field(default=1, ge=1, le=20)
    instructions: str = Field(default="", max_length=2000)
    allowed_extensions: list[str] = Field(default_factory=lambda: [".pdf", ".docx", ".txt", ".png", ".jpg", ".jpeg"])
    upload_channels: list[Literal["staff", "secure_link"]] = Field(default_factory=lambda: ["staff", "secure_link"])
    due_days: int | None = Field(default=None, ge=0, le=3650)

    @model_validator(mode="after")
    def valid_counts(self):
        if self.min_count > self.max_count or (self.required and self.min_count == 0):
            raise ValueError("Required items need a positive minimum; minimum cannot exceed maximum.")
        supported = {".pdf", ".docx", ".txt", ".png", ".jpg", ".jpeg"}
        if self.capture_type == "document" and (not self.upload_channels or not self.allowed_extensions or not set(self.allowed_extensions) <= supported):
            raise ValueError("Choose supported file formats and at least one upload channel.")
        return self


class CriterionDefinition(Input):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")
    label: str = Field(min_length=1, max_length=200)
    weight: float = Field(gt=0, le=100)
    instructions: str = Field(default="", max_length=2000)
    evidence_keys: list[str] = Field(default_factory=list)


class TemplateInput(Input):
    name: str = Field(min_length=1, max_length=200)
    requirements: list[RequirementDefinition] = Field(default_factory=list, max_length=60)
    criteria: list[CriterionDefinition] = Field(default_factory=list, max_length=60)
    revision: int | None = None

    @model_validator(mode="after")
    def valid_keys(self):
        for items in (self.requirements, self.criteria):
            keys = [item.key for item in items]
            if len(keys) != len(set(keys)):
                raise ValueError("Requirement and criterion keys must be unique within each list.")
        requirement_keys = {item.key for item in self.requirements}
        if any(set(item.evidence_keys) - requirement_keys for item in self.criteria):
            raise ValueError("Every criterion evidence key must reference a configured requirement.")
        if self.criteria and not math.isclose(sum(item.weight for item in self.criteria), 100, abs_tol=0.001):
            raise ValueError("Scoring weights must total 100; checklist-only programs may omit criteria.")
        return self


class ProgramInput(Input):
    name: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")


class CaseInput(Input):
    applicant_name: str = Field(min_length=1, max_length=200)
    external_candidate_id: str = Field(min_length=1, max_length=120)
    external_application_id: str = Field(default="", max_length=120)
    source: Literal["sis", "crm", "portal"] = "sis"
    program_id: str
    intake_term: str = Field(min_length=1, max_length=80)
    department_id: str | None = None
    candidate_id: str | None = None


class AliasInput(Input):
    source: Literal["sis", "crm", "portal"]
    value: str = Field(min_length=1, max_length=120)


class ValueInput(Input):
    value: str = Field(min_length=1, max_length=2000)
    revision: int
    slot: int | None = Field(default=None, ge=1, le=20)


class ReasonInput(Input):
    reason: str = Field(min_length=1, max_length=2000)
    revision: int


class AssignmentInput(Input):
    reviewer_id: str
    due_at: str | None = None
    priority: Literal["normal", "high", "urgent"] = "normal"

    @model_validator(mode="after")
    def date_valid(self):
        if self.due_at:
            from datetime import date
            date.fromisoformat(self.due_at)
        return self


class ReviewInput(Input):
    scores: dict[str, float] = Field(default_factory=dict)
    criterion_notes: dict[str, str] = Field(default_factory=dict)
    comments: str = Field(default="", max_length=5000)
    revision: int
    source_verified: bool = False
    conflict_declared: bool = False


class EvidenceInput(Input):
    revision: int
    field_id: str | None = None
    status: Literal["verified", "corrected", "rejected"]
    value: str = Field(default="", max_length=300)
    reason: str = Field(default="", max_length=2000)
    source_index: int | None = Field(default=None, ge=0)
    source_quote: str = Field(default="", max_length=1000)


class LinkInput(Input):
    expiry_days: int | None = Field(default=None, ge=1, le=90)
    max_uploads: int = Field(default=1, ge=1, le=20)


class InvitationInput(Input):
    email: str = Field(min_length=3, max_length=320)
    role: WorkspaceRole
    deliver_email: bool = False

    @model_validator(mode="after")
    def email_valid(self):
        self.email = self.email.lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", self.email):
            raise ValueError("Enter a valid staff email address.")
        return self


class InvitationAccept(Input):
    password: str = Field(min_length=8, max_length=200)


class CoverageInput(Input):
    program_ids: list[str] = Field(default_factory=list)
    department_ids: list[str] = Field(default_factory=list)
    workload_limit: int = Field(default=20, ge=1, le=250)


class NamedInput(Input):
    name: str = Field(min_length=1, max_length=200)


class SettingsInput(Input):
    brand_name: str = Field(min_length=1, max_length=200)
    brand_color: str = Field(default="#216b58", pattern=r"^#[0-9a-fA-F]{6}$")
    secure_link_expiry_days: int = Field(default=7, ge=1, le=90)
    fictional_example_enabled: bool = False
    external_processing_approved: bool = False


class ContractInput(Input):
    pages_per_unit: int = Field(ge=1, le=10000)
    price_per_unit_cents: int = Field(ge=0, le=100000000)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    billing_timezone: str = Field(default="UTC", max_length=100)
    invoice_terms_days: int = Field(default=30, ge=0, le=365)

    @model_validator(mode="after")
    def valid_zone(self):
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            ZoneInfo(self.billing_timezone)
        except ZoneInfoNotFoundError:
            raise ValueError("Choose a valid billing timezone.")
        return self


class InvoiceStatusInput(Input):
    status: Literal["draft", "issued", "paid", "overdue", "void", "credited"]
