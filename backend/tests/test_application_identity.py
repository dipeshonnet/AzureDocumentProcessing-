from __future__ import annotations

from app.config import AppSettings
from app.services.application_identity import (
    IdentityDerivationPayload,
    derive_application_identity,
    split_applicant_name,
)


def test_derives_applicant_identity_from_labeled_ocr_text() -> None:
    identity = derive_application_identity(
        payload=IdentityDerivationPayload(
            text=(
                "Applicant Name: Ada Lovelace\n"
                "Program Applied: MSc Analytics\n"
                "Personal statement text follows."
            ),
            structured_extractions=[],
        ),
        settings=AppSettings.from_mapping({}),
    )

    assert identity.applicant_name == "Ada Lovelace"
    assert identity.program_applied == "MSc Analytics"
    assert identity.uncertain is False


def test_derives_program_from_transcript_structured_extraction() -> None:
    identity = derive_application_identity(
        payload=IdentityDerivationPayload(
            text="Official transcript",
            structured_extractions=[
                {
                    "document_type": "transcript",
                    "extraction": {
                        "degree": {"value": "MSc"},
                        "major": {"value": "Computer Science"},
                    },
                }
            ],
        ),
        settings=AppSettings.from_mapping({}),
    )

    assert identity.applicant_name is None
    assert identity.program_applied == "MSc Computer Science"
    assert identity.uncertain is True


def test_split_applicant_name_preserves_last_name_parts() -> None:
    assert split_applicant_name("Ada King Lovelace") == ("Ada", "King Lovelace")
