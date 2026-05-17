---
prompt_name: document_classification
prompt_version: v1
input_schema: ClassificationPayload
output_schema: DocumentClassificationResult
safety_constraints:
  - Use only the supplied filename, headings, and OCR excerpt.
  - Return strict JSON matching the output schema.
  - Never make an admissions decision.
  - Do not infer protected attributes.
---
# Task

Classify this admissions document into exactly one supported document type:

- personal_statement
- transcript
- recommendation_letter
- resume_cv
- test_score_report
- application_form
- other_or_unreadable

# Inputs

Filename:
{{filename}}

Detected headings:
{{detected_headings}}

OCR excerpt:
{{ocr_excerpt}}

# Output

Return strict JSON with `document_type`, `confidence`, `rationale`, `evidence_snippets`, and `requires_human_review`.
