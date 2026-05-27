import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { setAuthToken } from "./api/client";

const user = {
  user_id: "user-1",
  email: "superadmin",
  role: "admin",
  billing_rate_per_unit: 1,
  university_logo_data_url: null
};

describe("operations console", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.location.hash = "#/intake";
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows the login screen when unauthenticated", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "Authentication required." }), { status: 401 }))
    );

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getAllByLabelText("Email")[0]).toHaveValue("");
    expect(screen.getAllByLabelText("Password")[0]).toHaveValue("");
    expect(screen.queryByText(/Demo:/i)).not.toBeInTheDocument();
  });

  it("renders selected file cards immediately after file selection", async () => {
    mockAuthenticatedFetch();
    setAuthToken("test-token");
    render(<App />);

    expect(await screen.findByText("Applicant document intake")).toBeInTheDocument();
    const input = screen.getByLabelText("Select admissions files or drag them here");
    fireEvent.change(input, {
      target: {
        files: [new File(["essay"], "personal_statement.txt", { type: "text/plain" })]
      }
    });

    expect(await screen.findByText("personal_statement.txt")).toBeInTheDocument();
    expect(screen.getByText("Ready to upload.")).toBeInTheDocument();
  });

  it("disables upload inputs and clear after upload starts", async () => {
    mockAuthenticatedFetch();
    setAuthToken("test-token");
    const xhrInstances: MockUploadXhr[] = [];
    vi.stubGlobal(
      "XMLHttpRequest",
      class extends MockUploadXhr {
        constructor() {
          super();
          xhrInstances.push(this);
        }
      }
    );
    render(<App />);

    await screen.findByText("Applicant document intake");
    fireEvent.change(screen.getByLabelText("Student ID"), { target: { value: "A1" } });
    fireEvent.change(screen.getByLabelText("Application program"), { target: { value: "Nursing" } });
    const input = screen.getByLabelText("Select admissions files or drag them here");
    fireEvent.change(input, {
      target: {
        files: [new File(["transcript"], "transcript.pdf", { type: "application/pdf" })]
      }
    });
    fireEvent.click(screen.getByRole("button", { name: /ingest document/i }));

    await waitFor(() => expect(screen.getByRole("button", { name: /clear/i })).toBeDisabled());
    expect(input).toBeDisabled();
    expect(xhrInstances).toHaveLength(1);
  });

  it("loads saved rubrics into the upload dropdown and editor", async () => {
    mockAuthenticatedFetch([sampleRubric]);
    setAuthToken("test-token");
    render(<App />);

    expect(await screen.findByRole("option", { name: "Custom nursing rubric" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Rubrics" }));

    expect(await screen.findByText("Save multiple program rubrics, then choose the applicable one during document upload.")).toBeInTheDocument();
    expect(screen.getAllByDisplayValue("Science GPA").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByDisplayValue("3.75-4.0")).toBeInTheDocument();
  });

  it("collapses and expands rubric editor sections", async () => {
    mockAuthenticatedFetch([sampleRubric]);
    setAuthToken("test-token");
    render(<App />);

    await screen.findByRole("option", { name: "Custom nursing rubric" });
    fireEvent.click(screen.getByRole("button", { name: "Rubrics" }));

    expect(await screen.findByDisplayValue("3.75-4.0")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Collapse GPA" }));
    expect(screen.queryByDisplayValue("3.75-4.0")).not.toBeInTheDocument();
    expect(screen.getByText("1 sub-components")).toBeInTheDocument();
    expect(screen.getByText("1 rows")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Expand GPA" }));
    expect(await screen.findByDisplayValue("3.75-4.0")).toBeInTheDocument();
  });

  it("renders profile billing ledger from completed jobs", async () => {
    mockAuthenticatedFetch([], sampleJobs);
    setAuthToken("test-token");
    render(<App />);

    await screen.findByText("Applicant document intake");
    fireEvent.click(screen.getByRole("button", { name: "Profile" }));

    expect(await screen.findByText("Usage ledger")).toBeInTheDocument();
    expect(screen.getByText("66")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("$7.00")).toBeInTheDocument();
    expect(screen.getByText("SampleApplicationSMU.pdf")).toBeInTheDocument();
  });

  it("shows live recommendation scores in the recommendation rubric card", async () => {
    mockAuthenticatedFetch([], [recommendationJob]);
    setAuthToken("test-token");
    render(<App />);

    await screen.findByText("Applicant document intake");
    fireEvent.click(await screen.findByRole("button", { name: "05A_Recommendation_Brett_Bricker.pdf" }));

    expect(await screen.findByRole("heading", { name: "Letters of recommendation" })).toBeInTheDocument();
    const recommendationCard = screen.getByRole("heading", { name: "Letters of recommendation" }).closest("article");
    expect(recommendationCard).toHaveTextContent("20 / 20");
    expect(recommendationCard).toHaveTextContent("I am writing to recommend Ariana Arvanitis for law school.");
    expect(recommendationCard).toHaveTextContent("Recommendation Strength");

    const shortAnswerCard = screen.getByRole("heading", { name: "Short answer" }).closest("article");
    expect(shortAnswerCard).toHaveTextContent("0 / 20");
    expect(shortAnswerCard).toHaveTextContent("Missing or not parsed yet. Human review required.");
  });

  it("shows saved rubric personal statement scores without using short answer aliases", async () => {
    mockAuthenticatedFetch([personalStatementRubric], [personalStatementJob]);
    setAuthToken("test-token");
    render(<App />);

    await screen.findByText("Applicant document intake");
    fireEvent.click(await screen.findByRole("button", { name: "03_Personal_Statement.pdf" }));

    const personalStatementCard = await screen.findByRole("heading", { name: "Personal Statement" });
    expect(personalStatementCard.closest("article")).toHaveTextContent("20 / 20");
    expect(personalStatementCard.closest("article")).toHaveTextContent("Applicant connects legal goals to prior experience.");

    const shortAnswerCard = screen.getByRole("heading", { name: "Short answer" }).closest("article");
    expect(shortAnswerCard).toHaveTextContent("0 / 20");
    expect(shortAnswerCard).toHaveTextContent("Missing or not parsed yet. Human review required.");
  });

  it("submits profile password changes", async () => {
    const fetchMock = mockAuthenticatedFetch();
    setAuthToken("test-token");
    render(<App />);

    await screen.findByText("Applicant document intake");
    fireEvent.click(screen.getByRole("button", { name: "Profile" }));

    fireEvent.change(await screen.findByLabelText("Current password"), { target: { value: "EverydayAI" } });
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "EverydayAI2" } });
    fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: "EverydayAI2" } });
    fireEvent.click(screen.getByRole("button", { name: "Update password" }));

    await waitFor(() => expect(screen.getByText("Password updated.")).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/auth/password"),
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({
          current_password: "EverydayAI",
          new_password: "EverydayAI2",
          confirm_new_password: "EverydayAI2"
        })
      })
    );
  });

  it("uploads a university logo from profile", async () => {
    const fetchMock = mockAuthenticatedFetch();
    setAuthToken("test-token");
    render(<App />);

    await screen.findByText("Applicant document intake");
    fireEvent.click(screen.getByRole("button", { name: "Profile" }));

    const logoInput = await screen.findByLabelText("Upload university logo");
    fireEvent.change(logoInput, {
      target: {
        files: [new File(["logo"], "school-logo.png", { type: "image/png" })]
      }
    });

    await waitFor(() => expect(screen.getByText("University logo updated.")).toBeInTheDocument());
    expect(screen.getByAltText("University logo")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/auth/profile-logo"),
      expect.objectContaining({
        method: "PUT",
        body: expect.stringContaining("data:image/png;base64")
      })
    );
  });

  it("lets admins update a user billing rate", async () => {
    const fetchMock = mockAuthenticatedFetch([], [], [sampleManagedUser]);
    setAuthToken("test-token");
    render(<App />);

    await screen.findByText("Applicant document intake");
    fireEvent.click(screen.getByRole("button", { name: "Users" }));

    const rateInput = await screen.findByLabelText("Billing rate for superadmin");
    fireEvent.change(rateInput, { target: { value: "2.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.getByText("Updated billing rate for superadmin.")).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/auth/users/user-1/billing-rate"),
      expect.objectContaining({ method: "PUT", body: JSON.stringify({ billing_rate_per_unit: 2.5 }) })
    );
  });
});

function mockAuthenticatedFetch(rubrics: unknown[] = [], jobs: unknown[] = [], managedUsers: unknown[] = [sampleManagedUser]) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/auth/me")) {
      return new Response(JSON.stringify(user), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    if (url.includes("/api/auth/password") && init?.method === "PUT") {
      return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    if (url.includes("/api/auth/profile-logo") && init?.method === "PUT") {
      const body = JSON.parse(String(init.body || "{}")) as { university_logo_data_url?: string | null };
      return new Response(
        JSON.stringify({ ...user, university_logo_data_url: body.university_logo_data_url ?? null }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      );
    }
    if (url.includes("/api/auth/users") && init?.method === "PUT") {
      const body = JSON.parse(String(init.body || "{}")) as { billing_rate_per_unit?: number };
      return new Response(
        JSON.stringify({ ...sampleManagedUser, billing_rate_per_unit: body.billing_rate_per_unit ?? 1 }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      );
    }
    if (url.includes("/api/auth/users")) {
      return new Response(JSON.stringify(managedUsers), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    if (url.includes("/api/jobs/status")) {
      return new Response(JSON.stringify({ jobs }), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    if (url.includes("/api/rubrics")) {
      return new Response(JSON.stringify(rubrics), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    return new Response(JSON.stringify({ detail: "Not found" }), { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const sampleRubric = {
  rubric_id: "custom_nursing_rubric",
  name: "Custom nursing rubric",
  description: "Saved rubric from backend.",
  version: 1,
  total_points: 100,
  is_active: true,
  created_at: "2026-05-13T00:00:00Z",
  updated_at: "2026-05-13T00:00:00Z",
  sections: [
    {
      section_id: "gpa",
      name: "GPA",
      description: "Science GPA tiers.",
      max_points: 30,
      components: [
        {
          component_id: "science_gpa",
          name: "Science GPA",
          description: "Transcript evidence.",
          max_points: 20,
          rows: [
            {
              row_id: "science_gpa_375_400",
              label: "Science GPA",
              condition: "3.75-4.0",
              points: "20",
              notes: ""
            }
          ]
        }
      ]
    }
  ]
};

const sampleJobs = [
  {
    job_id: "job-1",
    application_id: "app-1",
    document_id: "doc-1",
    rubric_id: "custom_nursing_rubric",
    status: "completed",
    progress: 100,
    status_message: "Parsed.",
    parser_mode: "mock",
    received_at: "2026-05-09T21:14:00Z",
    started_at: "2026-05-09T21:14:00Z",
    finished_at: "2026-05-09T21:14:00Z",
    applicant_name: "Applicant One",
    applicant_id: "A1",
    student_unique_id: "A1",
    program_applied: "Nursing",
    intake_term: "Fall 2026",
    application_status: "completed",
    document_name: "Arvanitis_A_L45090489_CAS.pdf",
    file_size: 1000,
    document_type: "pdf",
    page_count: 20,
    extracted_text: "",
    summary: "",
    section_analysis: [],
    extracted_record: {},
    record_download_url: "/api/jobs/job-1/record"
  },
  {
    job_id: "job-2",
    application_id: "app-1",
    document_id: "doc-2",
    rubric_id: "custom_nursing_rubric",
    status: "completed",
    progress: 100,
    status_message: "Parsed.",
    parser_mode: "mock",
    received_at: "2026-05-09T16:24:00Z",
    started_at: "2026-05-09T16:24:00Z",
    finished_at: "2026-05-09T16:24:00Z",
    applicant_name: "Applicant One",
    applicant_id: "A1",
    student_unique_id: "A1",
    program_applied: "Nursing",
    intake_term: "Fall 2026",
    application_status: "completed",
    document_name: "SampleApplicationSMU.pdf",
    file_size: 2000,
    document_type: "pdf",
    page_count: 46,
    extracted_text: "",
    summary: "",
    section_analysis: [],
    extracted_record: {},
    record_download_url: "/api/jobs/job-2/record"
  }
];

const recommendationJob = {
  job_id: "job-recommendation",
  application_id: "app-1",
  document_id: "doc-recommendation",
  rubric_id: "default_admissions_rubric",
  status: "completed",
  progress: 100,
  status_message: "Live processing completed.",
  parser_mode: "azure",
  received_at: "2026-05-17T14:42:00Z",
  started_at: "2026-05-17T14:42:00Z",
  finished_at: "2026-05-17T14:43:00Z",
  applicant_name: "Ariana Arvanitis",
  applicant_id: "A1",
  student_unique_id: "A1",
  program_applied: "Law school",
  intake_term: "Fall 2026",
  application_status: "needs_manual_review",
  document_name: "05A_Recommendation_Brett_Bricker.pdf",
  file_size: 2000,
  document_type: "recommendation_letter",
  page_count: 2,
  extracted_text: "Recommendation letter content.",
  summary: "A strong workplace recommendation for Ariana Arvanitis.",
  section_analysis: [
    {
      section_id: "recommendation",
      label: "Letters of recommendation",
      score: 20,
      max_score: 20,
      evidence: ["I am writing to recommend Ariana Arvanitis for law school."],
      rubric_criteria: ["Recommendation Strength"],
      status: "review"
    },
    {
      section_id: "writing_quality",
      label: "Writing Quality",
      score: 4,
      max_score: 5,
      evidence: ["Recommendation letter prose is clear."],
      rubric_criteria: ["Writing Quality"],
      status: "review"
    }
  ],
  extracted_record: {},
  record_download_url: "/api/jobs/job-recommendation/record"
};

const personalStatementRubric = {
  rubric_id: "law_personal_statement_rubric",
  name: "Law admissions rubric",
  description: "Saved rubric from backend.",
  version: 1,
  total_points: 40,
  is_active: true,
  created_at: "2026-05-18T00:00:00Z",
  updated_at: "2026-05-18T00:00:00Z",
  sections: [
    { section_id: "personal_statement", name: "Personal Statement", description: "Personal statement review.", max_points: 20, components: [] },
    { section_id: "short_answer", name: "Short answer", description: "Short answer review.", max_points: 20, components: [] }
  ]
};

const personalStatementJob = {
  job_id: "job-personal-statement",
  application_id: "app-1",
  document_id: "doc-personal-statement",
  rubric_id: "law_personal_statement_rubric",
  status: "completed",
  progress: 100,
  status_message: "Live processing completed.",
  parser_mode: "azure",
  received_at: "2026-05-18T14:42:00Z",
  started_at: "2026-05-18T14:42:00Z",
  finished_at: "2026-05-18T14:43:00Z",
  applicant_name: "Ariana Arvanitis",
  applicant_id: "A1",
  student_unique_id: "A1",
  program_applied: "Law school",
  intake_term: "Fall 2026",
  application_status: "needs_manual_review",
  document_name: "03_Personal_Statement.pdf",
  file_size: 2000,
  document_type: "personal_statement",
  page_count: 2,
  extracted_text: "Personal statement content.",
  summary: "A personal statement about legal goals.",
  section_analysis: [
    {
      section_id: "personal_statement",
      label: "Personal Statement",
      score: 20,
      max_score: 20,
      evidence: ["Applicant connects legal goals to prior experience."],
      rubric_criteria: ["Personal Statement"],
      status: "review"
    }
  ],
  extracted_record: {},
  record_download_url: "/api/jobs/job-personal-statement/record"
};

const sampleManagedUser = {
  user_id: "user-1",
  email: "superadmin",
  role: "admin",
  verification_status: "verified",
  document_count: 2,
  rubric_count: 1,
  billing_rate_per_unit: 1,
  university_logo_data_url: null,
  created_at: "2026-05-04T08:54:00Z",
  last_login_at: "2026-05-13T12:18:00Z"
};

class MockUploadXhr {
  upload: { onprogress?: (event: ProgressEvent) => void } = {};
  status = 0;
  responseText = "";
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  open = vi.fn();
  setRequestHeader = vi.fn();
  send = vi.fn(() => {
    this.upload.onprogress?.({ lengthComputable: true, loaded: 20, total: 100 } as ProgressEvent);
  });
}
