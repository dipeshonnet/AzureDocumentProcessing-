---
prompt_name: personal_statement_extraction
prompt_version: v1
input_schema: StructuredExtractionPayload
output_schema: PersonalStatementExtraction
safety_constraints:
  - Use only supplied OCR text.
  - Ground every important observation in evidence.
  - Mark missing evidence as uncertain.
  - Never infer protected attributes or make an admissions decision.
---
# Task

Extract reviewer-support facts from a personal statement or statement of purpose.

# Inputs

Filename: {{filename}}

Detected headings:
{{detected_headings}}

OCR excerpt:
{{ocr_excerpt}}

# Output

Return strict JSON matching the PersonalStatementExtraction schema.
