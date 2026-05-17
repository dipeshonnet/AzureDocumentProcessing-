---
prompt_name: document_summary
prompt_version: v1
input_schema: SummarizationPayload
output_schema: DocumentSummaryResult
safety_constraints:
  - Summarize only supplied OCR text.
  - Include evidence snippets for summaries, strengths, concerns, and missing information.
  - Do not infer facts not present in text.
  - Do not score, rank, admit, reject, or make an admissions decision.
---
# Task

Generate a grounded admissions document summary for reviewer decision support.

# Inputs

Filename: {{filename}}

Document type: {{document_type}}

OCR confidence: {{ocr_confidence}}

Detected headings:
{{detected_headings}}

OCR excerpt:
{{ocr_excerpt}}

# Output

Return strict JSON with short summary, section summaries, strengths, concerns, missing information, reviewer attention points, and evidence.
