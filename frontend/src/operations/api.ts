import { apiBaseUrl, apiRequest, authHeaders } from "../api/client";

export type Role = "university_owner" | "admissions_manager" | "reviewer" | "finance_viewer" | "auditor";
export type Settings = { brand_name: string; brand_color: string; secure_link_expiry_days: number; fictional_example_enabled: boolean; external_processing_approved: boolean };
export type Context = { university_id: string; role: Role; memberships: { id: string; name: string; role: Role }[]; settings: Settings; platform_admin: boolean };
export type Versioned = { id: string; revision: number; created_at: string };
export type Program = Versioned & { name: string; code: string; active_version_id: string | null };
export type RequirementDefinition = { key: string; label: string; capture_type: "document" | "number" | "text" | "external"; required: boolean; min_count: number; max_count: number; instructions: string; allowed_extensions: string[]; upload_channels: ("staff" | "secure_link")[]; due_days: number | null };
export type Criterion = { key: string; label: string; weight: number; instructions: string; evidence_keys: string[] };
export type Template = Versioned & { program_id: string; version: number; name: string; status: "draft" | "published"; requirements: RequirementDefinition[]; criteria: Criterion[] };
export type Requirement = Versioned & RequirementDefinition & { status: string; due_at: string | null; waiver_reason: string; value: string | number | null; values?: { slot: number; value: string | number; verified_by: string; verified_at: string | null }[]; value_history?: { before: unknown; after: unknown }[] };
export type Assignment = Versioned & { reviewer_id: string; reviewer_email: string; due_at: string | null; priority: string };
export type Case = Versioned & { candidate_id: string; applicant_name: string; external_candidate_id: string; external_application_id: string; source: string; program_id: string | null; program_name: string; intake_term: string; template_id: string | null; template_version: number | null; case_revision: number; status: string; completeness: number; missing_requirements: number; document_count: number; assignments: Assignment[] };
export type Document = Versioned & { requirement_id: string | null; slot: number | null; version: number; current: boolean; job_id: string; filename: string; channel: string; received_at: string; late: boolean; status: string; page_count: number | null; error: string | null };
export type Citation = { page: number | null; page_index: number; quote: string; line?: number };
export type ExtractedField = { id: string; key: string; label: string; suggested_value: string; citations: Citation[]; uncertainty: string; staff_status: string; staff_value: string | null; staff_citation: Citation | null; reason: string };
export type Evidence = Versioned & { document_id: string; filename: string; requirement_id: string; current?: boolean; pages: { number: number | null; text: string }[]; proposed_type: string; classification_state: string; staff_status: string; staff_type: string | null; fields: ExtractedField[]; history: { actor: string; at: string; before: unknown; after: unknown }[]; document_types?: Record<string, string> };
export type ReviewInput = { scores: Record<string, number>; criterion_notes: Record<string, string>; comments: string; revision: number; source_verified: boolean; conflict_declared: boolean };
export type Review = Versioned & ReviewInput & { reviewer_id: string; reviewer_email: string; case_revision: number; template_version: number; total_score: number; submitted_at: string };
export type Detail = { own_review_signed?: boolean; case: Case; aliases: (Versioned & { source: string; value: string })[]; requirements: Requirement[]; documents: Document[]; template: Template | null; evidence: { documents: Evidence[]; issues: string[]; conflicts: { key: string; label: string; entries: { filename: string; effective_value: string }[] }[] }; assignments: Assignment[]; reviews: Review[]; draft: (ReviewInput & { case_revision: number; saved_at: string }) | null; activity: { id: string; action: string; actor: string; timestamp: string }[]; links: { id: string; requirement_id: string; expires_at: string; revoked: boolean; uploads: number; max_uploads: number }[] };
export type Person = Versioned & { user_id: string; email: string; role: Role; status: string; workload_limit: number; program_ids: string[]; department_ids: string[]; open_reviews: number; last_login_at: string | null };
export type Department = Versioned & { name: string };
export type People = { people: Person[]; departments: Department[]; invitations: { id: string; email: string; role: string; expires_at: string; accepted: boolean; revoked: boolean }[] };
export type ImportRow = { row_number: number; status: string; message: string; values: Record<string, string>; changes?: Record<string, { from: string; to: string }> };
export type ImportBatch = Versioned & { filename: string; status: string; rows: ImportRow[] };
export type ExportBatch = Versioned & { row_count: number; content_sha256: string; supersedes_export_id: string };
export type Exchange = { imports: ImportBatch[]; exports: ExportBatch[] };
export type Contract = Versioned & { version: number; effective_from: string; pages_per_unit: number; price_per_unit_cents: number; currency: string; billing_timezone: string; invoice_terms_days: number };
export type Usage = Versioned & { filename: string; page_count: number; units: number; charge_cents: number; currency: string; occurred_at: string; invoice_id: string | null; contract_snapshot: Contract };
export type Invoice = Versioned & { invoice_number: string; status: string; total_cents: number; currency: string; period_start: string; period_end: string; due_at: string; lines: Usage[] };
export type Billing = { contracts: Contract[]; usage: Usage[]; invoices: Invoice[] };
export type Reports = { states: Record<string, number>; programs: { program: string; applications: number; average_completeness: number; ready: number }[]; missing: Record<string, number>; processing: Record<string, number>; reviewers: { reviewer: string; assigned: number; signed_revisions: number; average_score: number | null; average_turnaround_hours: number | null }[]; late_versions: number; reopened_cases: number };
export type Platform = { universities: { id: string; name: string; applications: number; members: number; documents: number; units: number; spend: Record<string, number>; onboarding: Record<string, boolean>; contract_versions: number }[]; activity: { action: string; actor: string; timestamp: string }[] };
export type Dashboard = { cases: Case[]; action_count: number; blocked_count: number; setup: Record<string, boolean> };
export type Training = { candidate: string; program: string; template_version: number; case_revision: number; documents: { id: string; type: string; page: number; text: string }[]; criteria: (Criterion & { document_id: string; page: number })[] };

export function ops<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  return apiRequest<T>(`/api/ops${path}`, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
}

export async function opsFile<T>(path: string, file: File, fields: Record<string, string> = {}, publicRequest = false): Promise<T> {
  const data = new FormData(); data.append("file", file);
  Object.entries(fields).forEach(([key, value]) => data.append(key, value));
  const response = await fetch(`${apiBaseUrl}/api/ops${path}`, { method: "POST", body: data, headers: publicRequest ? {} : authHeaders() });
  return readResponse<T>(response);
}

export async function readResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as { detail?: string | { msg: string }[] };
    throw new Error(typeof body.detail === "string" ? body.detail : Array.isArray(body.detail) ? body.detail.map(i => i.msg).join("; ") : `Request failed (${response.status}).`);
  }
  return response.json() as Promise<T>;
}

export async function sourceBlob(path: string): Promise<Blob> {
  const response = await fetch(`${apiBaseUrl}/api/ops${path}`, { headers: authHeaders() });
  if (!response.ok) { await readResponse(response); }
  return response.blob();
}

export async function download(path: string, filename: string): Promise<void> {
  const blob = await sourceBlob(path);
  const url = URL.createObjectURL(blob); const anchor = document.createElement("a");
  anchor.href = url; anchor.download = filename; document.body.append(anchor); anchor.click(); anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function publicOps<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${apiBaseUrl}/api/ops/public${path}`, { method: body ? "POST" : "GET", headers: { "Content-Type": "application/json" }, ...(body ? { body: JSON.stringify(body) } : {}) });
  return readResponse<T>(response);
}
