---
prompt_name: transcript_extraction
prompt_version: v1
input_schema: StructuredExtractionPayload
output_schema: TranscriptExtraction
safety_constraints:
  - Use only supplied OCR text.
  - Include evidence snippets for important fields.
  - Mark unsupported or missing facts as uncertain instead of inventing data.
  - Never make an admissions decision.
---
# Task

Extract structured transcript information from the OCR excerpt.

# Inputs

Filename: {{filename}}

Detected headings:
{{detected_headings}}

OCR excerpt:
{{ocr_excerpt}}

# Output

Return strict JSON matching the TranscriptExtraction schema.
