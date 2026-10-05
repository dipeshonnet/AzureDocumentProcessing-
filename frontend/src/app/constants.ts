import type { JobStatus, Rubric } from "../api/types";

export const allowedExtensions = new Set(["pdf", "docx", "txt", "jpg", "jpeg", "png"]);
export const allowedLogoTypes = new Set(["image/png", "image/jpeg", "image/webp"]);
export const maxLogoBytes = 750 * 1024;
export const terminalStatuses = new Set<JobStatus>(["completed", "failed"]);

export const fallbackRubrics: Rubric[] = [
  {
    rubric_id: "default_admissions_rubric",
    name: "Default Rubric",
    description: "Default local operations rubric. Edit and save copies for each program.",
    version: 1,
    total_points: 100,
    is_active: true,
    sections: [
      { section_id: "gpa", name: "GPA", description: "Academic GPA review.", max_points: 30, components: [] },
      { section_id: "prerequisites", name: "Prerequisites", description: "Prerequisite status review.", max_points: 5, components: [] },
      { section_id: "recommendation", name: "Letters of recommendation", description: "Recommendation letter review.", max_points: 20, components: [] },
      { section_id: "short_answer", name: "Short answer", description: "Writing and relevance review.", max_points: 20, components: [] },
      { section_id: "experience", name: "Experience", description: "Experience review.", max_points: 10, components: [] },
      { section_id: "socioeconomic_status", name: "Socioeconomic status", description: "Policy-approved contextual review.", max_points: 15, components: [] }
    ]
  }
];
