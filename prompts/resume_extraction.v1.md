---
prompt_name: resume_extraction
prompt_version: v1
input_schema: StructuredExtractionPayload
output_schema: ResumeExtraction
safety_constraints:
  - Use only supplied OCR text.
  - Extract education, work, projects, leadership, awards, and skills only when supported.
  - Mark missing evidence as uncertain.
  - Never make an admissions decision.
---
# Task

Extract structured reviewer-support facts from a resume or CV.

# Inputs

Filename: {{filename}}

Detected headings:
{{detected_headings}}

OCR excerpt:
{{ocr_excerpt}}

# Output

Return strict JSON matching the ResumeExtraction schema.
