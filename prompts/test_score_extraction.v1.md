---
prompt_name: test_score_extraction
prompt_version: v1
input_schema: StructuredExtractionPayload
output_schema: TestScoreExtraction
safety_constraints:
  - Use only supplied OCR text.
  - Do not invent scores, components, or dates.
  - Mark missing evidence as uncertain.
  - Never make an admissions decision.
---
# Task

Extract structured test score information from the OCR excerpt.

# Inputs

Filename: {{filename}}

Detected headings:
{{detected_headings}}

OCR excerpt:
{{ocr_excerpt}}

# Output

Return strict JSON matching the TestScoreExtraction schema.
