# Admissions AI Prompt Versioning

Versioned prompts live in the repository-level `prompts/` directory. Each prompt file uses this naming pattern:

```text
{prompt_name}.{prompt_version}.md
```

Example:

```text
prompts/document_classification.v1.md
```

## Required Metadata

Each prompt must begin with YAML front matter:

```yaml
---
prompt_name: document_classification
prompt_version: v1
input_schema: ClassificationPayload
output_schema: DocumentClassificationResult
safety_constraints:
  - Use only supplied evidence.
  - Never make an admissions decision.
---
```

The backend validates that these metadata fields are present before rendering a prompt.

## Auditability Rules

- Do not edit an already-used prompt version in place.
- To change behavior, create a new file such as `document_classification.v2.md`.
- Keep the old file in the repository so historical AI outputs remain auditable.
- Update the service mapping intentionally after the new prompt is reviewed.
- Record the reason for the prompt change in the pull request or change log.
- Keep output schemas stable unless the application code and tests are updated at the same time.
- Do not remove safety constraints that prevent final admissions decisions, protected-attribute inference, unsupported claims, or ungrounded evidence.

## Persisted Prompt Metadata

The backend stores `prompt_name` and `prompt_version` with AI-generated artifacts:

- Document classification metadata
- Structured extraction metadata
- Document summary metadata
- Rubric criterion scoring metadata

This lets reviewers and auditors connect each generated output back to the exact prompt template used.

## Testing

After adding or changing a prompt, run:

```powershell
python -B -m pytest backend/tests/test_prompt_versioning.py -p no:cacheprovider
python -B -m pytest backend/tests -p no:cacheprovider
```
