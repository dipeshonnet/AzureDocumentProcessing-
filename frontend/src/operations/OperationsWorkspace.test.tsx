import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import OperationsWorkspace from "./OperationsWorkspace";
import PublicWorkflow from "./PublicWorkflow";
import Programs from "./Programs";
import CaseDetail from "./CaseDetail";
import { PeoplePage } from "./Administration";
import { setAuthToken, setWorkspaceId } from "../api/client";

const settings = { brand_name: "Test University", brand_color: "#216b58", secure_link_expiry_days: 7, fictional_example_enabled: false, external_processing_approved: false };
const context = { university_id: "uni-1", role: "admissions_manager", settings, platform_admin: false, memberships: [{ id: "uni-1", name: "Test University", role: "admissions_manager" }] };
const program = { id: "program-1", name: "Data Science", code: "DS", active_version_id: "version-1" };
function mockApi(responses: Record<string, unknown>) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${new URL(url, "http://localhost").pathname}`;
    if (!(key in responses)) throw new Error(`Unexpected API request: ${key}`);
    const value = responses[key];
    return value instanceof Response ? value : new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("admissions case workflows", () => {
  beforeEach(() => { localStorage.clear(); window.location.hash = "#/operations"; });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it("opens finance users in billing without candidate or staff navigation", async () => {
    mockApi({ "GET /api/ops/context": { ...context, role: "finance_viewer" }, "GET /api/ops/billing": { usage: [], invoices: [], contracts: [] } });
    render(<OperationsWorkspace />);
    expect(await screen.findByRole("heading", { name: "University billing" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Applications" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "People" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Generate invoice drafts" })).not.toBeInTheDocument();
  });

  it("blocks finance users from a candidate deep link before loading its contents", async () => {
    window.location.hash = "#/operations/case/private-case/documents";
    const fetch = mockApi({ "GET /api/ops/context": { ...context, role: "finance_viewer" } });
    render(<OperationsWorkspace />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Your workspace role cannot access this section.");
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("sends the selected workspace with authenticated workflow requests", async () => {
    setAuthToken("test-token"); setWorkspaceId("uni-1");
    const fetch = mockApi({ "GET /api/ops/context": context, "GET /api/ops/dashboard": { cases: [], setup: { published_program: false }, action_count: 0, blocked_count: 0 } });
    render(<OperationsWorkspace />);
    expect(await screen.findByRole("heading", { name: "Launch checklist" })).toBeInTheDocument();
    expect(fetch.mock.calls[0][1]?.headers).toMatchObject({ Authorization: "Bearer test-token", "X-Workspace-Id": "uni-1" });
  });

  it("previews a draft and publishes its explicit revision", async () => {
    const draft = { id: "version-1", revision: 3, version: 1, name: "Admission checklist", status: "draft", requirements: [{ key: "transcript", label: "Transcript", required: true, capture_type: "document", min_count: 1, max_count: 2 }], criteria: [] };
    const fetch = mockApi({ "GET /api/ops/programs": [program], "GET /api/ops/programs/program-1/versions": [draft], "POST /api/ops/versions/version-1/publish": { ...draft, status: "published" } });
    render(<Programs canManage />);
    await screen.findByRole("option", { name: /Data Science/ });
    fireEvent.change(screen.getByLabelText("Program"), { target: { value: "program-1" } });
    expect(await screen.findByText(/Applications stay pinned to this version/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Publish version 1" }));
    await screen.findByText("Criteria published.");
    const call = fetch.mock.calls.find(([url]) => url.includes("/publish"));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ revision: 3, reason: "Publish program criteria" });
  });

  it("secure applicant upload uses no staff token and returns a limited receipt", async () => {
    setAuthToken("private-staff-token");
    const fetch = mockApi({ "GET /api/ops/public/uploads/opaque-token": { requirement_label: "Transcript", instructions: "Send your transcript", branding: { brand_name: "Test University" }, allowed_extensions: [".txt"], expires_at: "2027-01-01T00:00:00Z" }, "POST /api/ops/public/uploads/opaque-token": { received: true } });
    render(<PublicWorkflow kind="upload" token="opaque-token" />);
    const input = await screen.findByLabelText("Your document");
    fireEvent.change(input, { target: { files: [new File(["Transcript"], "transcript.txt", { type: "text/plain" })] } });
    fireEvent.submit(input.closest("form")!);
    expect(await screen.findByRole("heading", { name: "Document received" })).toBeInTheDocument();
    const call = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(call?.[1]?.headers).toEqual({});
    expect(screen.queryByLabelText("Your document")).not.toBeInTheDocument();
  });

  it("shows a revoked link error without offering an upload", async () => {
    mockApi({ "GET /api/ops/public/uploads/revoked": new Response(JSON.stringify({ detail: "This upload link has expired or was revoked." }), { status: 410 }) });
    render(<PublicWorkflow kind="upload" token="revoked" />);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("expired or was revoked"));
    expect(screen.queryByRole("button", { name: "Upload securely" })).not.toBeInTheDocument();
  });

  it("autosaves bounded human scores with the evidence revision before signing", async () => {
    const detail = { case: { id: "case-1", candidate_id: "candidate-1", applicant_name: "Fictional Student", program_name: "DS", template_version: 1, case_revision: 3, completeness: 100, status: "in_review" }, template: { criteria: [{ key: "academic", label: "Academic preparation", weight: 100, evidence_keys: [] }] }, evidence: { issues: [], conflicts: [] }, aliases: [], requirements: [], documents: [], assignments: [], reviews: [], activity: [], links: [], draft: null, own_review_signed: false };
    const fetch = mockApi({ "GET /api/ops/cases/case-1": detail, "GET /api/ops/cases": [], "PUT /api/ops/cases/case-1/review/draft": { saved_at: "2026-10-01T10:00:00Z" }, "POST /api/ops/cases/case-1/review/sign": {} });
    render(<CaseDetail id="case-1" tab="evaluation" canManage={false} reviewer />);
    const score = await screen.findByLabelText("Score for Academic preparation");
    fireEvent.change(score, { target: { value: "75" } });
    fireEvent.click(screen.getByLabelText("I checked the original source evidence against the pinned criteria."));
    expect(screen.getByRole("button", { name: "Sign review for revision 3" })).toBeDisabled();
    await waitFor(() => expect(screen.getByRole("button", { name: "Sign review for revision 3" })).toBeEnabled(), { timeout: 3000 });
    const saved = fetch.mock.calls.find(([, init]) => init?.method === "PUT");
    expect(JSON.parse(String(saved?.[1]?.body))).toMatchObject({ revision: 3, scores: { academic: 75 }, source_verified: true });
    fireEvent.submit(score.closest("form")!);
    await screen.findByText("Review signed. Its evidence revision and criteria are preserved.");
  });

  it("requests invitation email only when selected and offers the link if mail is unavailable", async () => {
    const fetch = mockApi({ "GET /api/ops/people": { people: [], departments: [], invitations: [] }, "GET /api/ops/programs": [], "POST /api/ops/invitations": { path: "#/invite/test-token", email_delivery: "not_configured" } });
    render(<PeoplePage owner />);
    fireEvent.change(screen.getByLabelText("Staff email"), { target: { value: "reviewer@example.test" } });
    fireEvent.click(screen.getByLabelText("Email this invitation using the configured mail service."));
    fireEvent.click(screen.getByRole("button", { name: "Create invitation" }));
    expect(await screen.findByText("Email delivery is not configured. Copy and share the invitation link.")).toBeInTheDocument();
    expect(screen.getByLabelText("Invitation link")).toHaveValue(window.location.href.split("#")[0] + "#/invite/test-token");
    const submitted = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(String(submitted?.[1]?.body))).toMatchObject({ deliver_email: true, email: "reviewer@example.test" });
  });
});
