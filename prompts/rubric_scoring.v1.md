---
prompt_name: rubric_scoring
prompt_version: v1
input_schema: RubricScoringContext
output_schema: RubricCriterionScoreResult
safety_constraints:
  - Score exactly one criterion.
  - Do not infer protected attributes.
  - Do not use race, religion, gender, nationality, disability, age, or other protected traits as scoring factors.
  - Do not make final admission decisions.
  - If evidence is insufficient, lower confidence and request human review.
---
# Task

Score exactly one admissions rubric criterion as reviewer decision support only.

# Criterion

Application ID: {{application_id}}

Criterion ID: {{criterion_id}}

Criterion name: {{criterion_name}}

Description: {{criterion_description}}

Max score: {{max_score}}

Scoring levels:
{{scoring_levels}}

Evidence required:
{{evidence_required}}

Human review triggers:
{{human_review_triggers}}

Protected attribute preprocessing note:
{{protected_attribute_note}}

# Evidence packet

{{evidence_text}}

# Output

Return strict JSON with criterion_id, criterion_name, score, max_score, rationale, supporting_evidence, missing_information, confidence, requires_human_review, and risk_flags.
