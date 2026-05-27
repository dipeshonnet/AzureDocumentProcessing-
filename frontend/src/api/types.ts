export type UserRole = "superadmin" | "admin" | "admissions_reviewer" | "read_only_auditor";

export type AuthUser = {
  user_id: string;
  email: string;
  role: UserRole;
  billing_rate_per_unit?: number;
  university_logo_data_url?: string | null;
  university_id?: string | null;
  pages_per_billable_unit?: number;
};

export type University = {
  university_id: string;
  name: string;
  logo_data_url: string | null;
  pages_per_billable_unit: number;
  api_key: string | null;
  webhook_url: string | null;
  created_at: string;
};

export type AuthResponse = {
  token: string;
  user: AuthUser;
};

export type JobStatus = "selected" | "uploading" | "queued" | "processing" | "completed" | "failed";

export type SectionAnalysis = {
  section_id: string;
  label: string;
  score: number;
  max_score: number;
  evidence: string[];
  rubric_criteria: string[];
  status: string;
};

export type Job = {
  job_id: string;
  application_id: string;
  document_id: string;
  rubric_id: string;
  status: Exclude<JobStatus, "selected" | "uploading">;
  progress: number;
  status_message: string;
  parser_mode: string;
  received_at: string;
  started_at: string | null;
  finished_at: string | null;
  applicant_name: string;
  applicant_id: string;
  student_unique_id: string | null;
  program_applied: string;
  intake_term: string;
  application_status: string;
  document_name: string;
  file_size: number | null;
  document_type: string | null;
  page_count: number | null;
  extracted_text: string | null;
  summary: string | null;
  section_analysis: SectionAnalysis[];
  extracted_record: Record<string, unknown>;
  record_download_url: string | null;
};

export type UploadQueueItem = {
  local_id: string;
  file: File;
  filename: string;
  extension: string;
  size: number;
  status: JobStatus;
  progress: number;
  status_message: string;
  job?: Job;
};

export type RubricRow = {
  row_id: string;
  label: string;
  condition: string;
  points: string;
  notes: string;
};

export type RubricComponent = {
  component_id: string;
  name: string;
  description: string;
  max_points: number;
  rows: RubricRow[];
};

export type RubricSection = {
  section_id: string;
  name: string;
  description: string;
  max_points: number;
  components: RubricComponent[];
};

export type Rubric = {
  rubric_id: string;
  name: string;
  description: string;
  version: number;
  total_points: number;
  is_active: boolean;
  created_at?: string;
  updated_at?: string;
  sections: RubricSection[];
};

export type RubricInput = {
  rubric_id?: string;
  name: string;
  description: string;
  version?: number;
  total_points: number;
  is_active?: boolean;
  sections: RubricSection[];
};

export type EvidenceSnippet = {
  snippet: string;
  document_id?: string | null;
  page_number?: number | null;
  source?: string | null;
  criterion_id?: string;
  criterion_name?: string;
};

export type ManagedUser = {
  user_id: string;
  email: string;
  role: UserRole;
  verification_status: string;
  document_count: number;
  rubric_count: number;
  billing_rate_per_unit: number;
  university_logo_data_url: string | null;
  created_at: string;
  last_login_at: string | null;
};
