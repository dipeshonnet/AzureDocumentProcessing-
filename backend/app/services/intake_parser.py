from __future__ import annotations

from dataclasses import dataclass

from app.models import Applicant, Application, ApplicationDocument, IntakeJob


class IntakeParserError(Exception):
    pass


@dataclass(frozen=True)
class ParsedIntakeRecord:
    document_type: str
    extracted_text: str
    summary: str
    sections: list[dict]
    record: dict


class IntakeParserService:
    def parse(
        self,
        *,
        job: IntakeJob,
        applicant: Applicant,
        application: Application,
        document: ApplicationDocument,
    ) -> ParsedIntakeRecord:
        raise NotImplementedError


class MockIntakeParserService(IntakeParserService):
    def parse(
        self,
        *,
        job: IntakeJob,
        applicant: Applicant,
        application: Application,
        document: ApplicationDocument,
    ) -> ParsedIntakeRecord:
        filename = document.original_filename
        if "parser_fail" in filename.lower():
            raise IntakeParserError("Mock parser was asked to fail for this document.")

        document_type = infer_document_type(filename)
        applicant_name = f"{applicant.first_name} {applicant.last_name}".strip()
        program_applied = application.program_applied or applicant.program_applied
        intake_term = application.intake_term or applicant.intake_term
        extracted_text = (
            f"Mock extracted admissions text for {applicant_name}. "
            f"Document {filename} was parsed as {document_type}. "
            f"Program: {program_applied}. Rubric: {job.rubric_id}."
        )
        sections = [
            section("gpa", "GPA", 24, 30, "Mock evidence indicates GPA is reviewable."),
            section("prerequisites", "Prerequisites", 4, 5, "Prerequisite coursework appears present."),
            section("recommendation", "Recommendation", 16, 20, "Recommendation materials are available for review."),
            section("short_answer", "Short answer", 15, 20, "Short answer content is readable and specific enough for review."),
            section("experience", "Experience", 8, 10, "Experience evidence is present in supporting materials."),
            section("socioeconomic_status", "Socioeconomic status", 10, 15, "Contextual information is not used automatically and requires human review."),
        ]
        record = {
            "applicant": {
                "applicant_id": applicant.applicant_id,
                "student_unique_id": applicant.student_unique_id,
                "name": applicant_name,
                "program_applied": program_applied,
                "intake_term": intake_term,
            },
            "application": {
                "application_id": application.application_id,
                "program_applied": program_applied,
                "intake_term": intake_term,
                "status": application.processing_status,
            },
            "document": {
                "document_id": document.document_id,
                "filename": filename,
                "document_type": document_type,
                "file_size": (document.classification_metadata or {}).get("file_size"),
            },
            "summary": extracted_text,
            "sections": sections,
            "decision_support_only": True,
        }
        return ParsedIntakeRecord(
            document_type=document_type,
            extracted_text=extracted_text,
            summary="Mock parser produced a reviewer-ready extracted record.",
            sections=sections,
            record=record,
        )


def get_intake_parser_service(parser_mode: str) -> IntakeParserService:
    if parser_mode.strip().lower() == "mock":
        return MockIntakeParserService()
    raise IntakeParserError(f"Unsupported parser mode: {parser_mode}.")


def infer_document_type(filename: str) -> str:
    lowered = filename.lower()
    if "transcript" in lowered:
        return "transcript"
    if "recommend" in lowered or "letter" in lowered:
        return "recommendation_letter"
    if "resume" in lowered or "cv" in lowered:
        return "resume_cv"
    if "score" in lowered or "test" in lowered:
        return "test_score_report"
    if "statement" in lowered or "essay" in lowered or "short" in lowered:
        return "personal_statement"
    return "other_or_unreadable"


def section(section_id: str, label: str, score: float, max_score: float, evidence: str) -> dict:
    return {
        "section_id": section_id,
        "label": label,
        "score": score,
        "max_score": max_score,
        "evidence": [evidence],
        "rubric_criteria": [f"{label} rubric review"],
        "status": "review",
    }
