from __future__ import annotations

from evals import run_evals


EXPECTED_CASE_IDS = {
    "strong_applicant",
    "average_applicant",
    "missing_transcript",
    "weak_recommendation",
    "ambiguous_gpa_scale",
    "unreadable_document",
    "protected_attribute_irrelevant",
}


def test_required_synthetic_cases_are_present() -> None:
    cases = run_evals.load_cases(run_evals.CASES_DIR)

    assert {case["case_id"] for case in cases} == EXPECTED_CASE_IDS
    for case in cases:
        assert case["documents"]
        assert case["evidence"]
        assert "expected_checks" in case


def test_mock_evals_pass_all_cases() -> None:
    cases = run_evals.load_cases(run_evals.CASES_DIR)
    results = [run_evals.run_case(case, provider="mock") for case in cases]

    assert all(result.passed for result in results), [
        (result.case_id, [(check.name, check.message) for check in result.checks if not check.passed])
        for result in results
        if not result.passed
    ]
    assert all(len(result.output["criteria"]) == 6 for result in results)
    assert all(
        "decision support" in result.output["scorecard"]["decision_support_summary"].lower()
        for result in results
    )


def test_no_invented_facts_check_rejects_unsupported_snippets() -> None:
    case = next(
        case for case in run_evals.load_cases(run_evals.CASES_DIR) if case["case_id"] == "strong_applicant"
    )
    output = run_evals.generate_mock_output(case)
    output["criteria"][0]["supporting_evidence"][0]["snippet"] = (
        "Invented evidence: applicant published in Nature."
    )

    checks = {check.name: check for check in run_evals.evaluate_output(case, output)}

    assert checks["no_invented_facts"].passed is False


def test_azure_eval_requires_explicit_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("EVAL_ENABLE_AZURE_OPENAI", raising=False)
    assert run_evals.azure_enabled(False) is False

    monkeypatch.setenv("EVAL_ENABLE_AZURE_OPENAI", "true")
    assert run_evals.azure_enabled(False) is True
    assert run_evals.azure_enabled(True) is True
