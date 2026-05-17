from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import AppSettings  # noqa: E402
from app.db.init_db import init_db  # noqa: E402
from app.db.session import create_db_engine, create_session_factory  # noqa: E402
from app.models import Applicant, Application, ApplicationDocument, DocumentSummary, ExtractedDocumentContent  # noqa: E402
from app.services.rubric_scoring import (  # noqa: E402
    AzureOpenAIRubricScoringService,
    RubricCriterionEvidence,
    RubricCriterionScoreResult,
    apply_scoring_safeguards,
    calculate_scorecard,
    contains_protected_attribute_text,
)
from app.services.rubrics import load_default_rubric  # noqa: E402


CASES_DIR = Path(__file__).resolve().parent / "cases"
LOW_CONFIDENCE_THRESHOLD = 0.65
PROTECTED_SCORE_RISK_FLAG = "protected_attribute_text_present_do_not_use_for_scoring"


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    message: str


@dataclass(frozen=True)
class CaseEvalResult:
    case_id: str
    name: str
    provider: str
    passed: bool
    checks: list[CheckResult]
    output: dict[str, Any]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run admissions AI output evaluations.")
    parser.add_argument("--cases-dir", default=str(CASES_DIR), help="Directory containing synthetic YAML cases.")
    parser.add_argument("--provider", choices=("mock", "azure"), default="mock")
    parser.add_argument(
        "--enable-azure-openai",
        action="store_true",
        help="Required with --provider azure. Real Azure OpenAI calls may incur cost.",
    )
    parser.add_argument("--output", help="Optional path to write JSON evaluation results.")
    args = parser.parse_args()

    if args.provider == "azure" and not azure_enabled(args.enable_azure_openai):
        print(
            "Azure evals are disabled. Re-run with --provider azure --enable-azure-openai "
            "or set EVAL_ENABLE_AZURE_OPENAI=true.",
            file=sys.stderr,
        )
        return 2

    cases = load_cases(Path(args.cases_dir))
    results = [run_case(case, provider=args.provider) for case in cases]
    payload = {
        "provider": args.provider,
        "case_count": len(results),
        "passed": all(result.passed for result in results),
        "results": [serialize_case_result(result) for result in results],
    }

    print_report(results)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nWrote evaluation results to {output_path}")

    return 0 if payload["passed"] else 1


def load_cases(cases_dir: Path) -> list[dict[str, Any]]:
    cases = []
    for path in sorted(cases_dir.glob("*.yaml")):
        with path.open("r", encoding="utf-8") as stream:
            case = yaml.safe_load(stream) or {}
        case["_path"] = str(path)
        validate_case(case)
        cases.append(case)
    if not cases:
        raise RuntimeError(f"No evaluation cases found in {cases_dir}")
    return cases


def validate_case(case: dict[str, Any]) -> None:
    required = {"case_id", "name", "documents", "evidence", "expected_checks"}
    missing = sorted(required.difference(case))
    if missing:
        raise ValueError(f"Case {case.get('_path', '<unknown>')} is missing {missing}")


def run_case(case: dict[str, Any], *, provider: str) -> CaseEvalResult:
    output = generate_mock_output(case) if provider == "mock" else generate_azure_output(case)
    checks = evaluate_output(case, output)
    return CaseEvalResult(
        case_id=case["case_id"],
        name=case["name"],
        provider=provider,
        passed=all(check.passed for check in checks),
        checks=checks,
        output=output,
    )


def generate_mock_output(case: dict[str, Any]) -> dict[str, Any]:
    rubric = load_default_rubric()
    default_score = float(case.get("default_score", 3.0))
    default_confidence = float(case.get("default_confidence", 0.78))
    mock_scores = case.get("mock_scores") or {}
    protected_present = case_contains_protected_attribute_text(case)
    criteria = []
    for criterion in rubric.criteria:
        config = dict(mock_scores.get(criterion.criterion_id) or {})
        evidence_key = config.get("evidence_key") or first_evidence_key(case)
        missing_information = list(config.get("missing_information") or [])
        confidence = float(config.get("confidence", default_confidence))
        score = float(config.get("score", default_score))
        rationale = config.get("rationale") or (
            f"Evaluation evidence supports an advisory score for {criterion.name}."
        )
        result = RubricCriterionScoreResult(
            criterion_id=criterion.criterion_id,
            criterion_name=criterion.name,
            score=score,
            max_score=criterion.max_score,
            rationale=rationale,
            supporting_evidence=[
                RubricCriterionEvidence(
                    snippet=evidence_for_key(case, evidence_key),
                    document_id=None,
                    page_number=1,
                    source=f"synthetic:{case['case_id']}:{evidence_key}",
                )
            ],
            missing_information=missing_information,
            confidence=confidence,
            requires_human_review=bool(missing_information) or confidence < LOW_CONFIDENCE_THRESHOLD,
            risk_flags=[],
        )
        criteria.append(
            apply_scoring_safeguards(
                result,
                low_confidence_threshold=LOW_CONFIDENCE_THRESHOLD,
                protected_attribute_text_present=protected_present,
            )
        )
    outcome = calculate_scorecard(results=criteria, rubric=rubric)
    return {
        "criteria": [criterion.model_dump(mode="json") for criterion in outcome.criteria],
        "scorecard": {
            "weighted_score": outcome.weighted_score,
            "max_score": outcome.max_score,
            "recommendation_band": outcome.recommendation_band,
            "decision_support_summary": outcome.decision_support_summary,
            "requires_human_review": outcome.requires_human_review,
            "risk_flags": outcome.risk_flags,
        },
    }


def generate_azure_output(case: dict[str, Any]) -> dict[str, Any]:
    settings = AppSettings().model_copy(
        update={
            "database_url": "sqlite:///:memory:",
            "rubric_scoring_backend": "azure",
        }
    )
    with tempfile.TemporaryDirectory() as _tmp_dir:
        engine = create_db_engine("sqlite:///:memory:")
        init_db(engine)
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            applicant = Applicant(
                first_name="Synthetic",
                last_name=case["case_id"],
                email=f"{case['case_id']}@example.edu",
                program_applied="Synthetic Program",
                intake_term="Eval",
                status="submitted",
            )
            application = Application(applicant=applicant, processing_status="ready_for_review")
            session.add(application)
            session.flush()
            for document_config in case["documents"]:
                text = str(document_config.get("text") or "")
                document = ApplicationDocument(
                    application=application,
                    original_filename=document_config.get("filename", "document.txt"),
                    blob_url_or_path=f"evals/{case['case_id']}/{document_config.get('filename', 'document.txt')}",
                    document_type=document_config.get("document_type"),
                    classification_confidence=0.9,
                    classification_metadata={"source": "synthetic_eval"},
                    processing_status="summarized",
                    page_count=1 if text else 0,
                )
                document.extracted_content = ExtractedDocumentContent(
                    raw_text=text,
                    pages=[{"page_number": 1, "text": text, "lines": [], "words": []}] if text else [],
                    tables=[],
                    key_value_pairs={"values": {}, "pairs": []},
                    extraction_confidence=document_config.get("extraction_confidence", 0.9),
                    extraction_metadata={"provider": "synthetic_eval"},
                    structured_extraction={"document_type": document_config.get("document_type"), "extraction": {}},
                )
                document.summary = DocumentSummary(
                    short_summary=text[:240] or "Document is unreadable.",
                    section_summaries=[],
                    strengths=[],
                    concerns=[] if text else ["Unreadable document"],
                    missing_information=[] if text else ["Readable text required"],
                    reviewer_attention_points=["Synthetic evaluation case."],
                    evidence=[{"snippet": text[:180] or "Unreadable document", "page_number": 1, "source": "synthetic_eval"}],
                )
            session.commit()
            service = AzureOpenAIRubricScoringService(db=session, settings=settings)
            outcome = service.score_application(application.application_id)
            return {
                "criteria": [criterion.model_dump(mode="json") for criterion in outcome.criteria],
                "scorecard": {
                    "weighted_score": outcome.weighted_score,
                    "max_score": outcome.max_score,
                    "recommendation_band": outcome.recommendation_band,
                    "decision_support_summary": outcome.decision_support_summary,
                    "requires_human_review": outcome.requires_human_review,
                    "risk_flags": outcome.risk_flags,
                },
            }


def evaluate_output(case: dict[str, Any], output: dict[str, Any]) -> list[CheckResult]:
    return [
        check_json_schema_valid(output),
        check_evidence_exists(output),
        check_no_invented_facts(case, output),
        check_missing_info_flagged(case, output),
        check_protected_attributes_not_used(case, output),
        check_low_confidence_for_weak_evidence(case, output),
    ]


def check_json_schema_valid(output: dict[str, Any]) -> CheckResult:
    try:
        if not output.get("criteria"):
            return CheckResult("json_schema_valid", False, "No rubric criterion outputs were returned.")
        for item in output.get("criteria", []):
            RubricCriterionScoreResult.model_validate(item)
    except ValidationError as exc:
        return CheckResult("json_schema_valid", False, str(exc))
    return CheckResult("json_schema_valid", True, "All rubric score JSON objects match the schema.")


def check_evidence_exists(output: dict[str, Any]) -> CheckResult:
    missing = [
        item.get("criterion_id", "<unknown>")
        for item in output.get("criteria", [])
        if not item.get("supporting_evidence")
    ]
    if missing:
        return CheckResult("evidence_exists_for_each_score", False, f"Missing evidence for {missing}.")
    return CheckResult("evidence_exists_for_each_score", True, "Every criterion score has supporting evidence.")


def check_no_invented_facts(case: dict[str, Any], output: dict[str, Any]) -> CheckResult:
    source_segments = source_segments_for_case(case)
    invalid_snippets = []
    for item in output.get("criteria", []):
        for evidence in item.get("supporting_evidence") or []:
            snippet = str(evidence.get("snippet") or "")
            if not snippet_is_grounded(snippet, source_segments):
                invalid_snippets.append(snippet)
    output_text = json.dumps(output).lower()
    forbidden = [
        marker
        for marker in case.get("expected_checks", {}).get("forbidden_fact_markers", [])
        if str(marker).lower() in output_text
    ]
    if invalid_snippets or forbidden:
        return CheckResult(
            "no_invented_facts",
            False,
            f"Invalid evidence snippets={invalid_snippets}; forbidden markers={forbidden}.",
        )
    return CheckResult("no_invented_facts", True, "Evidence snippets come from the synthetic source packet.")


def check_missing_info_flagged(case: dict[str, Any], output: dict[str, Any]) -> CheckResult:
    expected_keywords = [
        str(item).lower()
        for item in case.get("expected_checks", {}).get("missing_info_keywords", [])
    ]
    if not expected_keywords:
        return CheckResult("missing_info_flagged", True, "No missing-information expectation for this case.")
    missing_text = " | ".join(
        str(value)
        for criterion in output.get("criteria", [])
        for value in criterion.get("missing_information", [])
    ).lower()
    absent = [keyword for keyword in expected_keywords if keyword.lower() not in missing_text]
    if absent:
        return CheckResult("missing_info_flagged", False, f"Missing expected flags: {absent}.")
    return CheckResult("missing_info_flagged", True, "Expected missing information is flagged.")


def check_protected_attributes_not_used(case: dict[str, Any], output: dict[str, Any]) -> CheckResult:
    expected = bool(case.get("expected_checks", {}).get("protected_attribute_mentioned"))
    risk_flags = {
        flag
        for criterion in output.get("criteria", [])
        for flag in criterion.get("risk_flags", [])
    }
    rationales = " ".join(str(criterion.get("rationale", "")) for criterion in output.get("criteria", [])).lower()
    protected_terms = ("race", "religion", "gender", "nationality", "disability", "age", "ethnicity")
    rationale_mentions = [term for term in protected_terms if term in rationales]
    if expected and PROTECTED_SCORE_RISK_FLAG not in risk_flags:
        return CheckResult(
            "protected_attributes_not_used_in_score",
            False,
            "Protected-attribute text was expected but the score output was not flagged.",
        )
    if rationale_mentions:
        return CheckResult(
            "protected_attributes_not_used_in_score",
            False,
            f"Protected terms appeared in scoring rationales: {rationale_mentions}.",
        )
    return CheckResult(
        "protected_attributes_not_used_in_score",
        True,
        "Protected attributes are either absent or flagged without appearing in rationales.",
    )


def check_low_confidence_for_weak_evidence(case: dict[str, Any], output: dict[str, Any]) -> CheckResult:
    expected = set(case.get("expected_checks", {}).get("low_confidence_criteria", []))
    if not expected:
        return CheckResult("low_confidence_where_evidence_is_weak", True, "No weak-evidence confidence expectation for this case.")
    by_id = {criterion.get("criterion_id"): criterion for criterion in output.get("criteria", [])}
    too_high = [
        criterion_id
        for criterion_id in sorted(expected)
        if float(by_id.get(criterion_id, {}).get("confidence", 1.0)) >= LOW_CONFIDENCE_THRESHOLD
    ]
    if too_high:
        return CheckResult(
            "low_confidence_where_evidence_is_weak",
            False,
            f"Expected low confidence for {too_high}.",
        )
    return CheckResult("low_confidence_where_evidence_is_weak", True, "Weak-evidence criteria have low confidence.")


def first_evidence_key(case: dict[str, Any]) -> str:
    return next(iter((case.get("evidence") or {}).keys()))


def evidence_for_key(case: dict[str, Any], key: str) -> str:
    evidence = case.get("evidence") or {}
    if key not in evidence:
        raise KeyError(f"Evidence key '{key}' not found in case {case['case_id']}")
    return str(evidence[key])


def source_segments_for_case(case: dict[str, Any]) -> list[str]:
    segments = [str(value) for value in (case.get("evidence") or {}).values()]
    segments.extend(str(document.get("text") or "") for document in case.get("documents", []))
    return [segment for segment in segments if segment.strip()]


def snippet_is_grounded(snippet: str, source_segments: list[str]) -> bool:
    cleaned = snippet.strip()
    if not cleaned:
        return False
    lowered = cleaned.lower()
    return any(lowered in source.lower() or source.lower() in lowered for source in source_segments)


def case_contains_protected_attribute_text(case: dict[str, Any]) -> bool:
    text = "\n".join(str(document.get("text") or "") for document in case.get("documents", []))
    text += "\n".join(str(value) for value in (case.get("evidence") or {}).values())
    return contains_protected_attribute_text(text)


def azure_enabled(flag_enabled: bool) -> bool:
    return flag_enabled or os.getenv("EVAL_ENABLE_AZURE_OPENAI", "").strip().lower() in {"1", "true", "yes"}


def serialize_case_result(result: CaseEvalResult) -> dict[str, Any]:
    return {
        "case_id": result.case_id,
        "name": result.name,
        "provider": result.provider,
        "passed": result.passed,
        "checks": [check.__dict__ for check in result.checks],
        "output": result.output,
    }


def print_report(results: list[CaseEvalResult]) -> None:
    print("Admissions AI Evaluation Results")
    print("================================")
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"{status} {result.case_id} - {result.name}")
        for check in result.checks:
            check_status = "PASS" if check.passed else "FAIL"
            print(f"  {check_status} {check.name}: {check.message}")
    print("================================")
    print(f"Overall: {'PASS' if all(result.passed for result in results) else 'FAIL'}")


if __name__ == "__main__":
    raise SystemExit(main())
