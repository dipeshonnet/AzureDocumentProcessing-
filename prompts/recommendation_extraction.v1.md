---
prompt_name: recommendation_extraction
prompt_version: v1
input_schema: StructuredExtractionPayload
output_schema: RecommendationLetterExtraction
safety_constraints:
  - Use only supplied OCR text.
  - Include evidence for recommender identity, relationship, strengths, and caveats when available.
  - Mark missing evidence as uncertain with an explanation.
  - Never make an admissions decision.
---
# Task

Extract structured reviewer-support facts from a recommendation letter.

# Inputs

Filename: {{filename}}

Detected headings:
{{detected_headings}}

OCR excerpt:
{{ocr_excerpt}}

# Output

Return strict JSON matching the RecommendationLetterExtraction schema.
