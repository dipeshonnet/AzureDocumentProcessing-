# Admissions AI Evaluation Harness

The evaluation harness lives in `evals/` and is designed to test admissions AI outputs as reviewer decision support. It uses synthetic, non-sensitive application cases by default and does not call Azure services unless explicitly enabled.

## Run Mock Evals

From the repository root:

```powershell
python -m evals.run_evals
```

If `make` is available:

```powershell
make eval
```

On Windows without `make`, use the PowerShell wrapper:

```powershell
.\scripts\run-evals.ps1
```

Mock evals are deterministic and should be the default check for local development, CI, and prompt-template changes.

## Synthetic Cases

The harness includes these cases:

- `strong_applicant`
- `average_applicant`
- `missing_transcript`
- `weak_recommendation`
- `ambiguous_gpa_scale`
- `unreadable_document`
- `protected_attribute_irrelevant`

Each case defines source documents, allowed evidence snippets, mock rubric outputs, and expected safety checks.

## Checks

Each run verifies:

- Rubric score JSON matches the backend schema.
- Every criterion score has supporting evidence.
- Evidence snippets are grounded in the synthetic source packet.
- Expected missing information is flagged.
- Protected-attribute text is flagged and not used in scoring rationales.
- Low confidence is used when evidence is weak, missing, ambiguous, or unreadable.

The scorecard remains decision support only. A passing eval never means the system may auto-admit or auto-reject an applicant.

## Interpreting Results

The command prints one PASS/FAIL block per case and one line per check. Treat any failure as a release blocker for the affected AI workflow until a human reviews the output.

- `json_schema_valid` failure means the model or mock output no longer matches the API contract.
- `evidence_exists_for_each_score` failure means reviewers would not have citations for a score.
- `no_invented_facts` failure means the output contains unsupported evidence or a forbidden invented marker.
- `missing_info_flagged` failure means known gaps, such as a missing transcript, were not surfaced.
- `protected_attributes_not_used_in_score` failure means protected-attribute safeguards need review.
- `low_confidence_where_evidence_is_weak` failure means weak evidence was scored too confidently.

Use `--output` to save the full JSON report:

```powershell
python -m evals.run_evals --output output/evals/mock-results.json
```

## Optional Azure OpenAI Eval

Real Azure OpenAI evals are disabled unless explicitly enabled because they may incur cost and use configured cloud deployments:

```powershell
python -m evals.run_evals --provider azure --enable-azure-openai
```

or:

```powershell
$env:EVAL_ENABLE_AZURE_OPENAI = "true"
python -m evals.run_evals --provider azure
```

The PowerShell wrapper supports the same provider switch:

```powershell
.\scripts\run-evals.ps1 -Provider azure -EnableAzureOpenAI
```

Azure evals use the existing Azure OpenAI rubric scoring configuration. They should be run intentionally during prompt or deployment validation, not as the default local loop.
