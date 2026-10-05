import type {
  Rubric,
  RubricComponent,
  RubricInput,
  RubricRow,
  RubricSection,
  SectionAnalysis
} from "../api/types";
import { localId } from "./files";

export type RubricSetter = (value: Rubric | ((current: Rubric) => Rubric)) => void;

export function cloneRubric(rubric: Rubric): Rubric {
  return JSON.parse(JSON.stringify(rubric)) as Rubric;
}

export function rubricToInput(rubric: Rubric): RubricInput {
  return {
    rubric_id: rubric.rubric_id,
    name: rubric.name,
    description: rubric.description,
    version: rubric.version,
    total_points: rubric.total_points,
    is_active: rubric.is_active,
    sections: rubric.sections
  };
}

export function ensureDefaultSections(sections: SectionAnalysis[], rubric: Rubric): SectionAnalysis[] {
  return rubric.sections.map((rubricSection) => {
    const scoredSection = sections.find((section) => section.section_id === rubricSection.section_id);
    if (scoredSection) {
      return adaptScoredSectionToRubricSection(scoredSection, rubricSection);
    }
    return {
      section_id: rubricSection.section_id,
      label: rubricSection.name,
      score: 0,
      max_score: rubricSection.max_points,
      evidence: ["Missing or not parsed yet. Human review required."],
      rubric_criteria: [`${rubricSection.name} rubric review`],
      status: "missing"
    };
  });
}

export function updateSection(setDraft: RubricSetter, sectionId: string, patch: Partial<RubricSection>) {
  setDraft((current) => ({
    ...current,
    sections: current.sections.map((section) =>
      section.section_id === sectionId ? { ...section, ...patch } : section
    )
  }));
}

export function addSectionAfter(setDraft: RubricSetter, index: number) {
  setDraft((current) => {
    const nextSections = [...current.sections];
    nextSections.splice(index + 1, 0, newSection());
    return { ...current, sections: nextSections };
  });
}

export function removeSection(setDraft: RubricSetter, sectionId: string) {
  setDraft((current) => ({
    ...current,
    sections: current.sections.filter((section) => section.section_id !== sectionId)
  }));
}

export function updateComponent(
  setDraft: RubricSetter,
  sectionId: string,
  componentId: string,
  patch: Partial<RubricComponent>
) {
  setDraft((current) => ({
    ...current,
    sections: current.sections.map((section) =>
      section.section_id === sectionId
        ? {
            ...section,
            components: section.components.map((component) =>
              component.component_id === componentId ? { ...component, ...patch } : component
            )
          }
        : section
    )
  }));
}

export function addComponent(setDraft: RubricSetter, sectionId: string) {
  setDraft((current) => ({
    ...current,
    sections: current.sections.map((section) =>
      section.section_id === sectionId
        ? { ...section, components: [...section.components, newComponent()] }
        : section
    )
  }));
}

export function removeComponent(setDraft: RubricSetter, sectionId: string, componentId: string) {
  setDraft((current) => ({
    ...current,
    sections: current.sections.map((section) =>
      section.section_id === sectionId
        ? { ...section, components: section.components.filter((component) => component.component_id !== componentId) }
        : section
    )
  }));
}

export function updateRow(
  setDraft: RubricSetter,
  sectionId: string,
  componentId: string,
  rowId: string,
  patch: Partial<RubricRow>
) {
  setDraft((current) => ({
    ...current,
    sections: current.sections.map((section) =>
      section.section_id === sectionId
        ? {
            ...section,
            components: section.components.map((component) =>
              component.component_id === componentId
                ? {
                    ...component,
                    rows: component.rows.map((row) => (row.row_id === rowId ? { ...row, ...patch } : row))
                  }
                : component
            )
          }
        : section
    )
  }));
}

export function addRow(setDraft: RubricSetter, sectionId: string, componentId: string) {
  setDraft((current) => ({
    ...current,
    sections: current.sections.map((section) =>
      section.section_id === sectionId
        ? {
            ...section,
            components: section.components.map((component) =>
              component.component_id === componentId
                ? { ...component, rows: [...component.rows, newRow()] }
                : component
            )
          }
        : section
    )
  }));
}

export function removeRow(setDraft: RubricSetter, sectionId: string, componentId: string, rowId: string) {
  setDraft((current) => ({
    ...current,
    sections: current.sections.map((section) =>
      section.section_id === sectionId
        ? {
            ...section,
            components: section.components.map((component) =>
              component.component_id === componentId
                ? { ...component, rows: component.rows.filter((row) => row.row_id !== rowId) }
                : component
            )
          }
        : section
    )
  }));
}

function adaptScoredSectionToRubricSection(
  section: SectionAnalysis,
  rubricSection: RubricSection
): SectionAnalysis {
  const sourceMax = Number(section.max_score);
  const targetMax = Number(rubricSection.max_points);
  const shouldScale = Number.isFinite(sourceMax) && sourceMax > 0 && Number.isFinite(targetMax) && targetMax > 0;
  const score = shouldScale ? Math.min(targetMax, Math.max(0, (section.score / sourceMax) * targetMax)) : section.score;
  const maxScore = shouldScale ? targetMax : section.max_score;
  const rubricCriteria = [section.label, ...section.rubric_criteria].filter(
    (value, index, values) => value && values.indexOf(value) === index
  );
  return {
    ...section,
    section_id: rubricSection.section_id,
    label: rubricSection.name,
    score,
    max_score: maxScore,
    rubric_criteria: rubricCriteria
  };
}

function newSection(): RubricSection {
  return {
    section_id: `section_${localId()}`,
    name: "New section",
    description: "",
    max_points: 0,
    components: [newComponent()]
  };
}

function newComponent(): RubricComponent {
  return {
    component_id: `component_${localId()}`,
    name: "New sub-component",
    description: "",
    max_points: 0,
    rows: [newRow()]
  };
}

function newRow(): RubricRow {
  return {
    row_id: `row_${localId()}`,
    label: "New row",
    condition: "",
    points: "",
    notes: ""
  };
}
