import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  Clock3,
  Copy,
  Download,
  DollarSign,
  FileText,
  FolderInput,
  Image as ImageIcon,
  KeyRound,
  LayoutDashboard,
  ListChecks,
  LogOut,
  Plug,
  Plus,
  Save,
  ShieldCheck,
  Trash2,
  UploadCloud,
  UserCircle,
  Users
} from "lucide-react";
import {
  changePassword,
  clearAuthToken,
  createRubric,
  downloadJobRecord,
  fetchCurrentUser,
  fetchJobsStatus,
  fetchManagedUsers,
  fetchRubrics,
  login,
  logout,
  register,
  setAuthToken,
  updateRubric,
  updateManagedUserBillingRate,
  updateProfileLogo,
  uploadFileWithProgress
} from "./api/client";
import type { AuthUser, Job, JobStatus, ManagedUser, Rubric, RubricComponent, RubricInput, RubricRow, RubricSection, SectionAnalysis, UploadQueueItem } from "./api/types";

type Page = "intake" | "integrations" | "rubrics" | "users" | "profile";
type Notice = { type: "success" | "error"; message: string } | null;

const allowedExtensions = new Set(["pdf", "docx", "txt", "jpg", "jpeg", "png"]);
const allowedLogoTypes = new Set(["image/png", "image/jpeg", "image/webp"]);
const maxLogoBytes = 750 * 1024;
const terminalStatuses = new Set<JobStatus>(["completed", "failed"]);
const fallbackRubrics: Rubric[] = [
  {
    rubric_id: "default_admissions_rubric",
    name: "Nursing admissions operations rubric",
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

export default function App() {
  const [page, setPage] = useHashPage();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [rubrics, setRubrics] = useState<Rubric[]>(fallbackRubrics);

  useEffect(() => {
    let active = true;
    fetchCurrentUser()
      .then((currentUser) => {
        if (active) {
          setUser(currentUser);
        }
      })
      .catch(() => {
        clearAuthToken();
        if (active) {
          setUser(null);
        }
      })
      .finally(() => {
        if (active) {
          setAuthLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, []);

  async function handleLogout() {
    try {
      await logout();
    } catch {
      clearAuthToken();
    }
    setUser(null);
  }

  const loadRubrics = useCallback(async () => {
    try {
      const savedRubrics = await fetchRubrics();
      setRubrics(savedRubrics.length ? savedRubrics : fallbackRubrics);
    } catch {
      setRubrics(fallbackRubrics);
    }
  }, []);

  useEffect(() => {
    if (user) {
      void loadRubrics();
    }
  }, [user, loadRubrics]);

  if (authLoading) {
    return <div className="loading-screen">Opening secure workspace...</div>;
  }

  if (!user) {
    return <AuthScreen onAuthenticated={setUser} />;
  }

  return (
    <div className="ops-shell">
      <aside className="ops-sidebar" aria-label="Main navigation">
        <a className="ops-brand" href="#/intake" onClick={() => setPage("intake")}>
          <img src="/admissions-mark.svg" alt="" />
          <span>
            <strong>Admission Analyser</strong>
            <small>Secure document workspace</small>
          </span>
        </a>
        <nav className="ops-nav">
          <NavButton page="intake" current={page} onClick={setPage} icon={<FolderInput size={18} />}>
            Intake
          </NavButton>
          <NavButton page="integrations" current={page} onClick={setPage} icon={<Plug size={18} />}>
            Integrations
          </NavButton>
          <NavButton page="rubrics" current={page} onClick={setPage} icon={<ListChecks size={18} />}>
            Rubrics
          </NavButton>
          <NavButton page="users" current={page} onClick={setPage} icon={<Users size={18} />}>
            Users
          </NavButton>
          <NavButton page="profile" current={page} onClick={setPage} icon={<UserCircle size={18} />}>
            Profile
          </NavButton>
          <button className="nav-item" type="button" onClick={() => void handleLogout()}>
            <LogOut size={18} aria-hidden="true" />
            Log out
          </button>
        </nav>
      </aside>

      <main className="ops-workspace">
        <header className="page-header">
          <div>
            <p className="eyebrow">{pageEyebrow(page)}</p>
            <h1>{page === "intake" ? "Applicant document intake" : pageTitle(page)}</h1>
            <p>{pageDescription(page)}</p>
          </div>
          <div className="decision-chip">
            <ShieldCheck size={17} aria-hidden="true" />
            Decision support only
          </div>
        </header>

        {page === "intake" ? <IntakePage user={user} rubrics={rubrics} /> : null}
        {page === "integrations" ? <IntegrationsPage /> : null}
        {page === "rubrics" ? <RubricsPage rubrics={rubrics} onRubricsChanged={loadRubrics} /> : null}
        {page === "users" ? <UsersPage user={user} /> : null}
        {page === "profile" ? <ProfilePage user={user} /> : null}
      </main>
    </div>
  );
}

function AuthScreen({ onAuthenticated }: { onAuthenticated: (user: AuthUser) => void }) {
  const [loginEmail, setLoginEmail] = useState("superadmin");
  const [loginPassword, setLoginPassword] = useState("EverydayAI");
  const [registerEmail, setRegisterEmail] = useState("");
  const [registerPassword, setRegisterPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [notice, setNotice] = useState<Notice>(null);
  const [saving, setSaving] = useState(false);

  async function submitLogin(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setNotice(null);
    try {
      const response = await login(loginEmail, loginPassword);
      setAuthToken(response.token);
      onAuthenticated(response.user);
    } catch (error) {
      setNotice({ type: "error", message: errorMessage(error) });
    } finally {
      setSaving(false);
    }
  }

  async function submitRegistration(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setNotice(null);
    try {
      const response = await register(registerEmail, registerPassword, confirmPassword);
      setAuthToken(response.token);
      onAuthenticated(response.user);
    } catch (error) {
      setNotice({ type: "error", message: errorMessage(error) });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="auth-page">
      <header className="auth-brand">
        <img src="/admissions-mark.svg" alt="" />
        <div>
          <strong>Admission Analyser</strong>
          <span>Secure document intake for admissions operations teams.</span>
        </div>
      </header>
      {notice ? <NoticeBanner notice={notice} /> : null}
      <div className="auth-grid">
        <form className="auth-card" onSubmit={(event) => void submitLogin(event)}>
          <p className="eyebrow">Existing user</p>
          <h1>Sign in</h1>
          <p className="muted">Use the local demo account or your registered workspace credentials.</p>
          <label>
            Email
            <input value={loginEmail} onChange={(event) => setLoginEmail(event.target.value)} autoComplete="username" />
          </label>
          <label>
            Password
            <input
              value={loginPassword}
              onChange={(event) => setLoginPassword(event.target.value)}
              type="password"
              autoComplete="current-password"
              required
            />
          </label>
          <div className="demo-credentials">
            Demo: <strong>superadmin</strong> / <strong>EverydayAI</strong>
          </div>
          <button className="primary-button" type="submit" disabled={saving}>
            Sign in
          </button>
        </form>

        <form className="auth-card" onSubmit={(event) => void submitRegistration(event)}>
          <p className="eyebrow">New user</p>
          <h2>Register</h2>
          <p className="muted">Registration creates a local admissions reviewer account for development.</p>
          <label>
            Email
            <input value={registerEmail} onChange={(event) => setRegisterEmail(event.target.value)} autoComplete="username" />
          </label>
          <label>
            Password
            <input
              value={registerPassword}
              onChange={(event) => setRegisterPassword(event.target.value)}
              type="password"
              autoComplete="new-password"
            />
          </label>
          <label>
            Confirm password
            <input
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              type="password"
              autoComplete="new-password"
            />
          </label>
          <button className="secondary-button" type="submit" disabled={saving}>
            Create account
          </button>
        </form>
      </div>
    </div>
  );
}

function IntakePage({ user, rubrics }: { user: AuthUser; rubrics: Rubric[] }) {
  const [applicantName, setApplicantName] = useState("Jordan Lee");
  const [applicantId, setApplicantId] = useState("");
  const [program, setProgram] = useState("MSc Data Science");
  const [selectedRubricId, setSelectedRubricId] = useState(rubrics[0]?.rubric_id ?? fallbackRubrics[0].rubric_id);
  const [queueItems, setQueueItems] = useState<UploadQueueItem[]>([]);
  const [recentJobs, setRecentJobs] = useState<Job[]>([]);
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadStarted, setUploadStarted] = useState(false);
  const applicationIdRef = useRef<string | undefined>(undefined);

  const terminalCount = queueItems.filter((item) => terminalStatuses.has(item.status)).length;
  useEffect(() => {
    if (!rubrics.some((rubric) => rubric.rubric_id === selectedRubricId)) {
      setSelectedRubricId(rubrics[0]?.rubric_id ?? fallbackRubrics[0].rubric_id);
    }
  }, [rubrics, selectedRubricId]);

  const refreshRecent = useCallback(async () => {
    try {
      const jobs = await fetchJobsStatus();
      setRecentJobs(jobs);
    } catch {
      // Recent activity is secondary; upload cards still show user-facing errors.
    }
  }, []);

  const mergeJobs = useCallback((jobs: Job[]) => {
    if (!jobs.length) {
      return;
    }
    setQueueItems((current) =>
      current.map((item) => {
        const updatedJob = jobs.find((job) => job.job_id === item.job?.job_id);
        if (!updatedJob) {
          return item;
        }
        return {
          ...item,
          job: updatedJob,
          status: updatedJob.status,
          progress: updatedJob.progress,
          status_message: updatedJob.status_message
        };
      })
    );
    setRecentJobs((current) => mergeRecentJobs(current, jobs));
    const firstTerminal = jobs.find((job) => terminalStatuses.has(job.status));
    if (firstTerminal) {
      setSelectedJob(firstTerminal);
      setReviewOpen(true);
    }
  }, []);

  useEffect(() => {
    void refreshRecent();
  }, [refreshRecent]);

  useEffect(() => {
    const pendingIds = queueItems
      .filter((item) => item.job && !terminalStatuses.has(item.status))
      .map((item) => item.job?.job_id)
      .filter((jobId): jobId is string => Boolean(jobId));
    if (!pendingIds.length) {
      return undefined;
    }
    const poll = async () => {
      try {
        mergeJobs(await fetchJobsStatus(pendingIds));
      } catch (error) {
        setNotice({ type: "error", message: errorMessage(error) });
      }
    };
    void poll();
    const intervalId = window.setInterval(() => void poll(), 1200);
    return () => window.clearInterval(intervalId);
  }, [mergeJobs, queueItems]);

  function addFiles(files: FileList | File[]) {
    const nextItems = Array.from(files).map((file) => {
      const extension = fileExtension(file.name);
      const supported = allowedExtensions.has(extension);
      return {
        local_id: localId(),
        file,
        filename: file.name,
        extension: extension || "unknown",
        size: file.size,
        status: supported ? "selected" : "failed",
        progress: supported ? 0 : 100,
        status_message: supported ? "Ready to upload." : "Unsupported file type. This file will not block the others."
      } satisfies UploadQueueItem;
    });
    setQueueItems((current) => [...current, ...nextItems]);
  }

  async function uploadSelected() {
    const uploadable = queueItems.filter((item) => item.status === "selected");
    if (!uploadable.length) {
      setNotice({ type: "error", message: "Select at least one supported admissions file." });
      return;
    }
    setUploading(true);
    setUploadStarted(true);
    setNotice(null);
    let applicationId = applicationIdRef.current;
    for (const item of uploadable) {
      setQueueItems((current) =>
        current.map((candidate) =>
          candidate.local_id === item.local_id
            ? { ...candidate, status: "uploading", progress: 1, status_message: "Uploading file." }
            : candidate
        )
      );
      try {
        const job = await uploadFileWithProgress(
          {
            file: item.file,
            rubric_id: selectedRubricId,
            applicant_name: applicantName,
            applicant_id: applicantId,
            program_applied: program,
            intake_term: "Current intake",
            application_id: applicationId
          },
          (progress) =>
            setQueueItems((current) =>
              current.map((candidate) =>
                candidate.local_id === item.local_id
                  ? { ...candidate, progress: Math.max(1, Math.min(progress, 95)), status_message: "Uploading file." }
                  : candidate
              )
            )
        );
        applicationId = job.application_id;
        applicationIdRef.current = applicationId;
        setQueueItems((current) =>
          current.map((candidate) =>
            candidate.local_id === item.local_id
              ? {
                  ...candidate,
                  job,
                  status: job.status,
                  progress: job.progress,
                  status_message: job.status_message
                }
              : candidate
          )
        );
        mergeJobs([job]);
      } catch (error) {
        setQueueItems((current) =>
          current.map((candidate) =>
            candidate.local_id === item.local_id
              ? {
                  ...candidate,
                  status: "failed",
                  progress: 100,
                  status_message: errorMessage(error)
                }
              : candidate
          )
        );
      }
    }
    setUploading(false);
    await refreshRecent();
  }

  const latestReviewJob =
    selectedJob ??
    queueItems
      .map((item) => item.job)
      .filter((job): job is Job => Boolean(job))
      .find((job) => terminalStatuses.has(job.status)) ??
    null;

  return (
    <div className="intake-layout">
      {notice ? <NoticeBanner notice={notice} /> : null}
      <section className="panel intake-panel">
        <div className="panel-heading">
          <div>
            <h2>Manual intake</h2>
            <p>Files are stored, queued, parsed, and presented for human review.</p>
          </div>
          <span className="summary-count">
            {queueItems.length ? `${terminalCount} of ${queueItems.length} documents finished` : "No documents selected"}
          </span>
        </div>

        <div className="intake-form-grid">
          <label>
            Applicant name
            <input value={applicantName} onChange={(event) => setApplicantName(event.target.value)} disabled={uploading} />
          </label>
          <label>
            Applicant ID optional
            <input value={applicantId} onChange={(event) => setApplicantId(event.target.value)} disabled={uploading} />
          </label>
          <label>
            Program
            <input value={program} onChange={(event) => setProgram(event.target.value)} disabled={uploading} />
          </label>
          <label>
            Rubric
            <select value={selectedRubricId} onChange={(event) => setSelectedRubricId(event.target.value)} disabled={uploading}>
              {rubrics.map((rubric) => (
                <option key={rubric.rubric_id} value={rubric.rubric_id}>
                  {rubric.name}
                </option>
              ))}
            </select>
          </label>
        </div>

        <label
          className={`dropzone ${uploading ? "dropzone-disabled" : ""}`}
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault();
            if (!uploading) {
              addFiles(event.dataTransfer.files);
            }
          }}
        >
          <UploadCloud size={30} aria-hidden="true" />
          <strong>Select admissions files or drag them here</strong>
          <span>Transcripts, statements, recommendation letters, score reports, and supporting materials are all supported.</span>
          <input
            aria-label="Select admissions files or drag them here"
            type="file"
            multiple
            disabled={uploading}
            onChange={(event) => {
              if (event.currentTarget.files) {
                addFiles(event.currentTarget.files);
                event.currentTarget.value = "";
              }
            }}
          />
        </label>

        {queueItems.length ? (
          <div className="selected-documents">
            <div className="panel-subheading">
              <h3>Selected documents</h3>
              <div className="button-row">
                <button
                  className="secondary-button"
                  type="button"
                  onClick={() => {
                    setQueueItems([]);
                    setSelectedJob(null);
                    setReviewOpen(false);
                    applicationIdRef.current = undefined;
                  }}
                  disabled={uploading || uploadStarted}
                >
                  <Trash2 size={16} aria-hidden="true" />
                  Clear
                </button>
                <button className="primary-button" type="button" onClick={() => void uploadSelected()} disabled={uploading}>
                  <UploadCloud size={16} aria-hidden="true" />
                  Ingest document
                </button>
              </div>
            </div>
            <div className="file-card-grid">
              {queueItems.map((item) => (
                <button
                  key={item.local_id}
                  className={`file-card ${selectedJob?.job_id === item.job?.job_id ? "file-card-active" : ""}`}
                  type="button"
                  onClick={() => {
                    if (item.job) {
                      setSelectedJob(item.job);
                      if (terminalStatuses.has(item.job.status)) {
                        setReviewOpen(true);
                      }
                    }
                  }}
                >
                  <div className="file-card-main">
                    <FileText size={20} aria-hidden="true" />
                    <span>
                      <strong>{item.filename}</strong>
                      <small>{item.extension.toUpperCase()} - {formatBytes(item.size)}</small>
                    </span>
                  </div>
                  <StatusPill status={item.status} />
                  <ProgressBar value={item.progress} />
                  <p>{item.status_message}</p>
                </button>
              ))}
            </div>
          </div>
        ) : null}
      </section>

      <LatestReview
        job={latestReviewJob}
        open={reviewOpen}
        onToggle={() => setReviewOpen((current) => !current)}
        onDownload={async (job) => {
          try {
            await downloadJobRecord(job.job_id);
          } catch (error) {
            setNotice({ type: "error", message: errorMessage(error) });
          }
        }}
      />

      <RecentActivity jobs={recentJobs} onSelect={(job) => { setSelectedJob(job); setReviewOpen(terminalStatuses.has(job.status)); }} />
      <p className="signed-in-note">Signed in as {user.email} ({user.role.replace(/_/g, " ")}).</p>
    </div>
  );
}

function LatestReview({
  job,
  open,
  onToggle,
  onDownload
}: {
  job: Job | null;
  open: boolean;
  onToggle: () => void;
  onDownload: (job: Job) => Promise<void>;
}) {
  return (
    <section className="panel latest-review">
      <button className="section-toggle" type="button" onClick={onToggle} disabled={!job}>
        <span>
          <h2>Latest intake review</h2>
          <small>{job ? `${job.document_name} - ${job.status_message}` : "Select or finish a job to inspect the extracted record."}</small>
        </span>
        <strong>{open && job ? "Collapse" : "Expand"}</strong>
      </button>
      {open && job ? (
        <div className="review-body">
          <div className="review-summary-grid">
            <SummaryTile label="Applicant name" value={job.applicant_name} />
            <SummaryTile label="Applicant ID" value={job.applicant_id} />
            <SummaryTile label="Application program" value={job.program_applied} />
            <SummaryTile label="Application status" value={job.application_status} />
            <SummaryTile label="Document name" value={job.document_name} />
            <SummaryTile label="File size" value={job.file_size ? formatBytes(job.file_size) : "Unknown"} />
            <SummaryTile label="Received at" value={formatDateTime(job.received_at)} />
            <SummaryTile label="Pipeline status" value={humanize(job.status)} />
            <SummaryTile label="Document type" value={humanize(job.document_type)} />
            <SummaryTile label="Parsing engine" value={job.parser_mode} />
          </div>
          <div className={`status-message status-message-${job.status}`}>
            {job.status_message}
          </div>
          <div className="button-row">
            <button className="secondary-button" type="button" disabled={job.status !== "completed"} onClick={() => void onDownload(job)}>
              <Download size={16} aria-hidden="true" />
              Download extracted record
            </button>
          </div>
          <div className="extracted-text-block">
            <h3>Extracted admissions text</h3>
            <pre>{job.extracted_text || "No extracted text is available yet."}</pre>
          </div>
          <div className="analysis-section">
            <h3>Admissions section alignment</h3>
            <p>{job.summary || "No section summary is available yet."}</p>
            <div className="section-tags">
              {job.section_analysis.map((section) => (
                <span key={section.section_id}>{section.label}</span>
              ))}
            </div>
            <div className="analysis-card-grid">
              {ensureDefaultSections(job.section_analysis).map((section) => (
                <article className="analysis-card" key={section.section_id}>
                  <div>
                    <h4>{section.label}</h4>
                    <strong>{formatScore(section.score, section.max_score)}</strong>
                  </div>
                  <p>{section.evidence[0] ?? "Evidence not available. Human review required."}</p>
                  <small>{section.rubric_criteria.join(", ") || "Rubric criterion pending"}</small>
                </article>
              ))}
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function RecentActivity({ jobs, onSelect }: { jobs: Job[]; onSelect: (job: Job) => void }) {
  return (
    <section className="panel recent-activity">
      <div className="panel-heading">
        <div>
          <h2>Recent activity</h2>
          <p>Latest intake jobs from the local backend.</p>
        </div>
      </div>
      <div className="responsive-table">
        <table>
          <thead>
            <tr>
              <th>Applicant</th>
              <th>Program</th>
              <th>Document</th>
              <th>Pipeline status</th>
              <th>Received date</th>
              <th>Record</th>
            </tr>
          </thead>
          <tbody>
            {jobs.length ? (
              jobs.map((job) => (
                <tr key={job.job_id}>
                  <td>{job.applicant_name}</td>
                  <td>{job.program_applied}</td>
                  <td>
                    <button className="link-button" type="button" onClick={() => onSelect(job)}>
                      {job.document_name}
                    </button>
                  </td>
                  <td><StatusPill status={job.status} /></td>
                  <td>{formatDateTime(job.received_at)}</td>
                  <td>{job.status === "completed" ? "Available" : "Pending"}</td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={6}>No intake jobs yet.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function RubricsPage({
  rubrics,
  onRubricsChanged
}: {
  rubrics: Rubric[];
  onRubricsChanged: () => Promise<void>;
}) {
  const [selectedRubricId, setSelectedRubricId] = useState(rubrics[0]?.rubric_id ?? fallbackRubrics[0].rubric_id);
  const [draft, setDraft] = useState<Rubric>(() => cloneRubric(rubrics[0] ?? fallbackRubrics[0]));
  const [notice, setNotice] = useState<Notice>(null);
  const [saving, setSaving] = useState(false);
  const [collapsedSectionIds, setCollapsedSectionIds] = useState<Set<string>>(() => new Set());

  useEffect(() => {
    const selected = rubrics.find((rubric) => rubric.rubric_id === selectedRubricId);
    if (selected) {
      setDraft(cloneRubric(selected));
      return;
    }
    if (rubrics.length && selectedRubricId === fallbackRubrics[0].rubric_id) {
      setSelectedRubricId(rubrics[0].rubric_id);
      setDraft(cloneRubric(rubrics[0]));
    }
  }, [rubrics, selectedRubricId]);

  useEffect(() => {
    setCollapsedSectionIds(new Set());
  }, [selectedRubricId]);

  const sectionTotal = draft.sections.reduce((sum, section) => sum + Number(section.max_points || 0), 0);
  const allSectionsCollapsed = draft.sections.length > 0 && draft.sections.every((section) => collapsedSectionIds.has(section.section_id));

  function toggleSection(sectionId: string) {
    setCollapsedSectionIds((current) => {
      const next = new Set(current);
      if (next.has(sectionId)) {
        next.delete(sectionId);
      } else {
        next.add(sectionId);
      }
      return next;
    });
  }

  function collapseAllSections() {
    setCollapsedSectionIds(new Set(draft.sections.map((section) => section.section_id)));
  }

  function expandAllSections() {
    setCollapsedSectionIds(new Set());
  }

  async function saveDraft() {
    setSaving(true);
    setNotice(null);
    try {
      const input = rubricToInput(draft);
      const saved = rubrics.some((rubric) => rubric.rubric_id === draft.rubric_id)
        ? await updateRubric(draft.rubric_id, input)
        : await createRubric(input);
      setSelectedRubricId(saved.rubric_id);
      await onRubricsChanged();
      setNotice({ type: "success", message: "Rubric saved. It is now available on the Intake upload form." });
    } catch (error) {
      setNotice({ type: "error", message: errorMessage(error) });
    } finally {
      setSaving(false);
    }
  }

  function createNewRubric() {
    const template = cloneRubric(rubrics[0] ?? fallbackRubrics[0]);
    const nextId = localId();
    setSelectedRubricId(nextId);
    setDraft({
      ...template,
      rubric_id: nextId,
      name: "New admissions rubric",
      description: "Customize sections, sub-components, and scoring rows.",
      version: 1,
      created_at: undefined,
      updated_at: undefined
    });
    setNotice(null);
    setCollapsedSectionIds(new Set());
  }

  function duplicateCurrentRubric() {
    const nextId = localId();
    setSelectedRubricId(nextId);
    setDraft({
      ...cloneRubric(draft),
      rubric_id: nextId,
      name: `${draft.name} copy`,
      version: 1,
      created_at: undefined,
      updated_at: undefined
    });
    setNotice(null);
    setCollapsedSectionIds(new Set());
  }

  return (
    <div className="rubrics-layout">
      {notice ? <NoticeBanner notice={notice} /> : null}
      <section className="panel rubric-list-panel">
        <div className="panel-heading">
          <div>
            <h2>Rubrics</h2>
            <p>Save multiple program rubrics, then choose the applicable one during document upload.</p>
          </div>
        </div>
        <div className="rubric-list">
          {rubrics.map((rubric) => (
            <button
              className={`rubric-list-item ${rubric.rubric_id === selectedRubricId ? "rubric-list-item-active" : ""}`}
              key={rubric.rubric_id}
              type="button"
              onClick={() => setSelectedRubricId(rubric.rubric_id)}
            >
              <strong>{rubric.name}</strong>
              <span>{rubric.sections.length} sections - {trimNumber(rubric.total_points)} pts</span>
            </button>
          ))}
        </div>
        <div className="rubric-actions">
          <button className="secondary-button" type="button" onClick={createNewRubric}>
            <Plus size={16} aria-hidden="true" />
            New rubric
          </button>
          <button className="secondary-button" type="button" onClick={duplicateCurrentRubric}>
            <Copy size={16} aria-hidden="true" />
            Duplicate
          </button>
        </div>
      </section>

      <section className="panel rubric-editor-panel">
        <div className="panel-heading">
          <div>
            <h2>Rubric editor</h2>
            <p>Add sections, sub-components, and row-level scoring rules like GPA ranges, prerequisite status, and recommendation letter categories.</p>
          </div>
          <span className="summary-count">{trimNumber(sectionTotal)} of {trimNumber(draft.total_points)} pts assigned</span>
        </div>

        <div className="rubric-meta-grid">
          <label>
            Rubric name
            <input value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
          </label>
          <label>
            Total points
            <input type="number" value={draft.total_points} onChange={(event) => setDraft({ ...draft, total_points: Number(event.target.value) })} />
          </label>
          <label>
            Version
            <input type="number" value={draft.version} onChange={(event) => setDraft({ ...draft, version: Number(event.target.value) || 1 })} />
          </label>
          <label>
            Active
            <select value={draft.is_active ? "active" : "inactive"} onChange={(event) => setDraft({ ...draft, is_active: event.target.value === "active" })}>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </select>
          </label>
          <label className="wide">
            Description
            <textarea value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} rows={3} />
          </label>
        </div>

        <div className="rubric-section-stack">
          <div className="rubric-section-toolbar">
            <span>{draft.sections.length} sections</span>
            <button className="secondary-button" type="button" onClick={allSectionsCollapsed ? expandAllSections : collapseAllSections}>
              {allSectionsCollapsed ? <ChevronDown size={16} aria-hidden="true" /> : <ChevronRight size={16} aria-hidden="true" />}
              {allSectionsCollapsed ? "Expand all" : "Collapse all"}
            </button>
          </div>
          {draft.sections.map((section, sectionIndex) => {
            const isCollapsed = collapsedSectionIds.has(section.section_id);
            const sectionBodyId = `rubric-section-${section.section_id}-body`;
            const rowCount = section.components.reduce((sum, component) => sum + component.rows.length, 0);
            return (
              <article className={`rubric-section-card ${isCollapsed ? "rubric-section-card-collapsed" : ""}`} key={section.section_id}>
                <div className="rubric-section-header">
                  <button
                    className="icon-button rubric-section-collapse"
                    type="button"
                    onClick={() => toggleSection(section.section_id)}
                    aria-expanded={!isCollapsed}
                    aria-controls={sectionBodyId}
                    aria-label={`${isCollapsed ? "Expand" : "Collapse"} ${section.name || "rubric section"}`}
                    title={isCollapsed ? "Expand section" : "Collapse section"}
                  >
                    {isCollapsed ? <ChevronRight size={18} aria-hidden="true" /> : <ChevronDown size={18} aria-hidden="true" />}
                  </button>
                  <label>
                    Section
                    <input
                      value={section.name}
                      onChange={(event) => updateSection(setDraft, section.section_id, { name: event.target.value })}
                    />
                  </label>
                  <label>
                    Max points
                    <input
                      type="number"
                      value={section.max_points}
                      onChange={(event) => updateSection(setDraft, section.section_id, { max_points: Number(event.target.value) })}
                    />
                  </label>
                  <button className="secondary-button" type="button" onClick={() => removeSection(setDraft, section.section_id)} disabled={draft.sections.length === 1}>
                    <Trash2 size={16} aria-hidden="true" />
                    Remove
                  </button>
                </div>
                <div className="rubric-section-summary">
                  <span>{section.components.length} sub-components</span>
                  <span>{rowCount} rows</span>
                </div>
                {!isCollapsed ? (
                  <div className="rubric-section-body" id={sectionBodyId}>
                    <label>
                      Section guidance
                      <textarea
                        value={section.description}
                        onChange={(event) => updateSection(setDraft, section.section_id, { description: event.target.value })}
                        rows={2}
                      />
                    </label>

                    <div className="rubric-component-stack">
                      {section.components.map((component) => (
                        <div className="rubric-component" key={component.component_id}>
                          <div className="rubric-component-header">
                            <label>
                              Sub-component
                              <input
                                value={component.name}
                                onChange={(event) => updateComponent(setDraft, section.section_id, component.component_id, { name: event.target.value })}
                              />
                            </label>
                            <label>
                              Component points
                              <input
                                type="number"
                                value={component.max_points}
                                onChange={(event) => updateComponent(setDraft, section.section_id, component.component_id, { max_points: Number(event.target.value) })}
                              />
                            </label>
                            <button className="secondary-button" type="button" onClick={() => removeComponent(setDraft, section.section_id, component.component_id)}>
                              <Trash2 size={16} aria-hidden="true" />
                              Remove
                            </button>
                          </div>
                          <label>
                            Component guidance
                            <textarea
                              value={component.description}
                              onChange={(event) => updateComponent(setDraft, section.section_id, component.component_id, { description: event.target.value })}
                              rows={2}
                            />
                          </label>
                          <div className="rubric-row-table">
                            <div className="rubric-row-head">
                              <span>Label</span>
                              <span>Range / condition</span>
                              <span>Points / result</span>
                              <span>Notes</span>
                              <span></span>
                            </div>
                            {component.rows.map((row) => (
                              <div className="rubric-row" key={row.row_id}>
                                <input
                                  aria-label="Rubric row label"
                                  value={row.label}
                                  onChange={(event) => updateRow(setDraft, section.section_id, component.component_id, row.row_id, { label: event.target.value })}
                                />
                                <input
                                  aria-label="Rubric row condition"
                                  value={row.condition}
                                  onChange={(event) => updateRow(setDraft, section.section_id, component.component_id, row.row_id, { condition: event.target.value })}
                                />
                                <input
                                  aria-label="Rubric row points"
                                  value={row.points}
                                  onChange={(event) => updateRow(setDraft, section.section_id, component.component_id, row.row_id, { points: event.target.value })}
                                />
                                <textarea
                                  aria-label="Rubric row notes"
                                  value={row.notes}
                                  onChange={(event) => updateRow(setDraft, section.section_id, component.component_id, row.row_id, { notes: event.target.value })}
                                  rows={2}
                                />
                                <button className="secondary-button" type="button" onClick={() => removeRow(setDraft, section.section_id, component.component_id, row.row_id)}>
                                  <Trash2 size={16} aria-hidden="true" />
                                </button>
                              </div>
                            ))}
                          </div>
                          <button className="secondary-button" type="button" onClick={() => addRow(setDraft, section.section_id, component.component_id)}>
                            <Plus size={16} aria-hidden="true" />
                            Add row
                          </button>
                        </div>
                      ))}
                    </div>

                    <div className="button-row">
                      <button className="secondary-button" type="button" onClick={() => addComponent(setDraft, section.section_id)}>
                        <Plus size={16} aria-hidden="true" />
                        Add sub-component
                      </button>
                      <button className="secondary-button" type="button" onClick={() => addSectionAfter(setDraft, sectionIndex)}>
                        <Plus size={16} aria-hidden="true" />
                        Add section below
                      </button>
                    </div>
                  </div>
                ) : null}
              </article>
            );
          })}
        </div>

        <div className="sticky-save-bar">
          <span>Saved rubrics appear in the Intake rubric dropdown.</span>
          <button className="primary-button" type="button" onClick={() => void saveDraft()} disabled={saving}>
            <Save size={16} aria-hidden="true" />
            Save rubric
          </button>
        </div>
      </section>
    </div>
  );
}

type RubricSetter = (value: Rubric | ((current: Rubric) => Rubric)) => void;

function cloneRubric(rubric: Rubric): Rubric {
  return JSON.parse(JSON.stringify(rubric)) as Rubric;
}

function rubricToInput(rubric: Rubric): RubricInput {
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

function updateSection(setDraft: RubricSetter, sectionId: string, patch: Partial<RubricSection>) {
  setDraft((current) => ({
    ...current,
    sections: current.sections.map((section) =>
      section.section_id === sectionId ? { ...section, ...patch } : section
    )
  }));
}

function addSectionAfter(setDraft: RubricSetter, index: number) {
  setDraft((current) => {
    const nextSections = [...current.sections];
    nextSections.splice(index + 1, 0, newSection());
    return { ...current, sections: nextSections };
  });
}

function removeSection(setDraft: RubricSetter, sectionId: string) {
  setDraft((current) => ({
    ...current,
    sections: current.sections.filter((section) => section.section_id !== sectionId)
  }));
}

function updateComponent(
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

function addComponent(setDraft: RubricSetter, sectionId: string) {
  setDraft((current) => ({
    ...current,
    sections: current.sections.map((section) =>
      section.section_id === sectionId
        ? { ...section, components: [...section.components, newComponent()] }
        : section
    )
  }));
}

function removeComponent(setDraft: RubricSetter, sectionId: string, componentId: string) {
  setDraft((current) => ({
    ...current,
    sections: current.sections.map((section) =>
      section.section_id === sectionId
        ? { ...section, components: section.components.filter((component) => component.component_id !== componentId) }
        : section
    )
  }));
}

function updateRow(
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

function addRow(setDraft: RubricSetter, sectionId: string, componentId: string) {
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

function removeRow(setDraft: RubricSetter, sectionId: string, componentId: string, rowId: string) {
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

function IntegrationsPage() {
  return (
    <section className="panel integration-grid">
      <IntegrationCard title="Google Drive" status="Placeholder" description="Connect admissions folders in a future release." />
      <IntegrationCard title="Future sources" status="Planned" description="Mailbox, SIS, and partner upload feeds can be added behind the same intake job API." />
    </section>
  );
}

function ProfilePage({ user }: { user: AuthUser }) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [billingRate, setBillingRate] = useState(user.billing_rate_per_unit ?? 1);
  const [logoDataUrl, setLogoDataUrl] = useState<string | null>(user.university_logo_data_url ?? null);
  const [logoNotice, setLogoNotice] = useState<Notice>(null);
  const [logoSaving, setLogoSaving] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmNewPassword, setConfirmNewPassword] = useState("");
  const [passwordNotice, setPasswordNotice] = useState<Notice>(null);
  const [passwordSaving, setPasswordSaving] = useState(false);
  useEffect(() => {
    fetchJobsStatus().then(setJobs).catch(() => setJobs([]));
    fetchCurrentUser()
      .then((currentUser) => {
        setBillingRate(currentUser.billing_rate_per_unit ?? 1);
        setLogoDataUrl(currentUser.university_logo_data_url ?? null);
      })
      .catch(() => {
        setBillingRate(user.billing_rate_per_unit ?? 1);
        setLogoDataUrl(user.university_logo_data_url ?? null);
      });
  }, [user.billing_rate_per_unit, user.university_logo_data_url]);

  async function handleLogoSelection(files: FileList | null) {
    const file = files?.[0];
    if (!file) {
      return;
    }
    if (!allowedLogoTypes.has(file.type)) {
      setLogoNotice({ type: "error", message: "Use a PNG, JPEG, or WebP logo." });
      return;
    }
    if (file.size > maxLogoBytes) {
      setLogoNotice({ type: "error", message: "Logo must be 750 KB or smaller." });
      return;
    }
    setLogoSaving(true);
    setLogoNotice(null);
    try {
      const dataUrl = await readFileAsDataUrl(file);
      const updatedUser = await updateProfileLogo(dataUrl);
      setLogoDataUrl(updatedUser.university_logo_data_url ?? null);
      setLogoNotice({ type: "success", message: "University logo updated." });
    } catch (error) {
      setLogoNotice({ type: "error", message: errorMessage(error) });
    } finally {
      setLogoSaving(false);
    }
  }

  async function clearLogo() {
    setLogoSaving(true);
    setLogoNotice(null);
    try {
      await updateProfileLogo(null);
      setLogoDataUrl(null);
      setLogoNotice({ type: "success", message: "University logo removed." });
    } catch (error) {
      setLogoNotice({ type: "error", message: errorMessage(error) });
    } finally {
      setLogoSaving(false);
    }
  }

  async function submitPasswordChange(event: React.FormEvent) {
    event.preventDefault();
    if (newPassword !== confirmNewPassword) {
      setPasswordNotice({ type: "error", message: "New passwords must match." });
      return;
    }
    setPasswordSaving(true);
    setPasswordNotice(null);
    try {
      await changePassword(currentPassword, newPassword, confirmNewPassword);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmNewPassword("");
      setPasswordNotice({ type: "success", message: "Password updated." });
    } catch (error) {
      setPasswordNotice({ type: "error", message: errorMessage(error) });
    } finally {
      setPasswordSaving(false);
    }
  }

  const billingRows = jobs
    .filter((job) => job.status === "completed")
    .map((job) => {
      const pages = pageCountForJob(job);
      const units = billableUnitsForPages(pages);
      return {
        job,
        pages,
        units,
        charge: units * billingRate
      };
    });
  const documentsProcessed = billingRows.length;
  const pagesProcessed = billingRows.reduce((sum, row) => sum + row.pages, 0);
  const billableUnits = billingRows.reduce((sum, row) => sum + row.units, 0);
  const currentCharge = billingRows.reduce((sum, row) => sum + row.charge, 0);
  return (
    <div className="profile-layout">
      <section className="panel billing-ledger">
        <div className="billing-hero">
          <div>
            <p className="eyebrow">Billing</p>
            <h2>Usage ledger</h2>
            <p>Processed documents, page counts, billing units, and current charges.</p>
          </div>
          <div className="current-charge-card">
            <span>Current charge</span>
            <strong>{formatCurrency(currentCharge)}</strong>
          </div>
        </div>

        <div className="billing-metric-grid">
          <BillingMetricCard icon={<FileText size={20} />} label="Documents processed" value={String(documentsProcessed)} />
          <BillingMetricCard icon={<LayoutDashboard size={20} />} label="Pages processed" value={String(pagesProcessed)} />
          <BillingMetricCard icon={<ListChecks size={20} />} label="Billable units" value={String(billableUnits)} />
          <BillingMetricCard icon={<DollarSign size={20} />} label="Rate" value={`${formatRate(billingRate)} per document unit`} highlighted />
        </div>

        <div className="responsive-table billing-table">
          <table>
            <thead>
              <tr>
                <th>Document</th>
                <th>Type</th>
                <th>Pages</th>
                <th>Billable units</th>
                <th>Charge</th>
                <th>Processed at</th>
              </tr>
            </thead>
            <tbody>
              {billingRows.length ? (
                billingRows.map((row) => (
                  <tr key={row.job.job_id}>
                    <td>{row.job.document_name}</td>
                    <td>{humanize(row.job.document_type || fileExtension(row.job.document_name) || "document")}</td>
                    <td>{row.pages}</td>
                    <td>{row.units}</td>
                    <td>{formatCurrency(row.charge)}</td>
                    <td>{formatDateTime(row.job.finished_at ?? row.job.received_at)}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={6}>No completed documents have been billed yet.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel logo-panel">
        <div className="logo-heading">
          <span className="billing-metric-icon">
            <ImageIcon size={20} aria-hidden="true" />
          </span>
          <div>
            <p className="eyebrow">Account image</p>
            <h2>University logo</h2>
            <p>This logo is used as the profile image for your account.</p>
          </div>
        </div>
        {logoNotice ? <NoticeBanner notice={logoNotice} /> : null}
        <div className="logo-workspace">
          <div className={`logo-preview ${logoDataUrl ? "" : "logo-preview-empty"}`}>
            {logoDataUrl ? (
              <img src={logoDataUrl} alt="University logo" />
            ) : (
              <ImageIcon size={34} aria-hidden="true" />
            )}
          </div>
          <div className="logo-actions">
            <label className="secondary-button logo-upload-button">
              <UploadCloud size={16} aria-hidden="true" />
              Upload logo
              <input
                aria-label="Upload university logo"
                type="file"
                accept="image/png,image/jpeg,image/webp"
                disabled={logoSaving}
                onChange={(event) => {
                  void handleLogoSelection(event.currentTarget.files);
                  event.currentTarget.value = "";
                }}
              />
            </label>
            <button className="secondary-button" type="button" onClick={() => void clearLogo()} disabled={logoSaving || !logoDataUrl}>
              <Trash2 size={16} aria-hidden="true" />
              Remove
            </button>
            <span>PNG, JPEG, or WebP up to 750 KB.</span>
          </div>
        </div>
      </section>

      <section className="panel password-panel">
        <div className="password-heading">
          <span className="billing-metric-icon">
            <KeyRound size={20} aria-hidden="true" />
          </span>
          <div>
            <p className="eyebrow">Security</p>
            <h2>Password</h2>
            <p>Confirm the current password before setting a new one.</p>
          </div>
        </div>
        {passwordNotice ? <NoticeBanner notice={passwordNotice} /> : null}
        <form className="password-form" onSubmit={(event) => void submitPasswordChange(event)}>
          <label>
            Current password
            <input
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
              type="password"
              autoComplete="current-password"
              required
            />
          </label>
          <label>
            New password
            <input
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              type="password"
              autoComplete="new-password"
              minLength={8}
              required
            />
          </label>
          <label>
            Confirm new password
            <input
              value={confirmNewPassword}
              onChange={(event) => setConfirmNewPassword(event.target.value)}
              type="password"
              autoComplete="new-password"
              minLength={8}
              required
            />
          </label>
          <div className="password-requirement">
            <strong>Minimum 8 characters</strong>
            <span>Use a unique password for this workspace.</span>
          </div>
          <button className="primary-button password-submit" type="submit" disabled={passwordSaving}>
            <Save size={16} aria-hidden="true" />
            Update password
          </button>
        </form>
      </section>
    </div>
  );
}

function BillingMetricCard({
  icon,
  label,
  value,
  highlighted = false
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  highlighted?: boolean;
}) {
  return (
    <article className={`billing-metric-card ${highlighted ? "billing-metric-card-highlight" : ""}`}>
      <span className="billing-metric-icon">{icon}</span>
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function UsersPage({ user }: { user: AuthUser }) {
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [rateDrafts, setRateDrafts] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<Notice>(null);
  const [savingUserId, setSavingUserId] = useState<string | null>(null);

  useEffect(() => {
    if (user.role !== "admin") {
      return;
    }
    fetchManagedUsers()
      .then((managedUsers) => {
        setUsers(managedUsers);
        setRateDrafts(
          Object.fromEntries(
            managedUsers.map((managedUser) => [managedUser.user_id, String(managedUser.billing_rate_per_unit)])
          )
        );
      })
      .catch((error) => setNotice({ type: "error", message: errorMessage(error) }));
  }, [user.role]);

  async function saveRate(managedUser: ManagedUser) {
    const nextRate = Number(rateDrafts[managedUser.user_id]);
    if (!Number.isFinite(nextRate) || nextRate < 0) {
      setNotice({ type: "error", message: "Enter a valid non-negative rate." });
      return;
    }
    setSavingUserId(managedUser.user_id);
    setNotice(null);
    try {
      const updatedUser = await updateManagedUserBillingRate(managedUser.user_id, nextRate);
      setUsers((current) => current.map((candidate) => (candidate.user_id === updatedUser.user_id ? updatedUser : candidate)));
      setRateDrafts((current) => ({ ...current, [updatedUser.user_id]: String(updatedUser.billing_rate_per_unit) }));
      setNotice({ type: "success", message: `Updated billing rate for ${updatedUser.email}.` });
    } catch (error) {
      setNotice({ type: "error", message: errorMessage(error) });
    } finally {
      setSavingUserId(null);
    }
  }

  if (user.role !== "admin") {
    return (
      <section className="panel empty-panel">
        <AlertTriangle size={22} aria-hidden="true" />
        <h2>Admin access required</h2>
        <p>User administration is limited to admin accounts.</p>
      </section>
    );
  }
  return (
    <div className="users-layout">
      {notice ? <NoticeBanner notice={notice} /> : null}
      <section className="panel user-management-panel">
        <div className="panel-heading user-management-heading">
          <span className="billing-metric-icon">
            <Users size={20} aria-hidden="true" />
          </span>
          <div>
            <p className="eyebrow">Administration</p>
            <h2>User management</h2>
            <p>Review registered users, verification status, usage, rates, and last sign-in activity.</p>
          </div>
        </div>
        <div className="responsive-table user-management-table">
          <table>
            <thead>
              <tr>
                <th>Email</th>
                <th>Role</th>
                <th>Verification</th>
                <th>Documents</th>
                <th>Rubrics</th>
                <th>Rate / unit</th>
                <th>Created</th>
                <th>Last login</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {users.length ? (
                users.map((managedUser) => (
                  <tr key={managedUser.user_id}>
                    <td>{managedUser.email}</td>
                    <td>{displayUserRole(managedUser.role)}</td>
                    <td>{humanize(managedUser.verification_status)}</td>
                    <td>{managedUser.document_count}</td>
                    <td>{managedUser.rubric_count}</td>
                    <td>
                      <input
                        aria-label={`Billing rate for ${managedUser.email}`}
                        className="rate-input"
                        min="0"
                        step="0.01"
                        type="number"
                        value={rateDrafts[managedUser.user_id] ?? String(managedUser.billing_rate_per_unit)}
                        onChange={(event) =>
                          setRateDrafts((current) => ({ ...current, [managedUser.user_id]: event.target.value }))
                        }
                      />
                    </td>
                    <td>{formatDateTime(managedUser.created_at)}</td>
                    <td>{formatDateTime(managedUser.last_login_at)}</td>
                    <td>
                      <button
                        className="secondary-button"
                        type="button"
                        onClick={() => void saveRate(managedUser)}
                        disabled={savingUserId === managedUser.user_id}
                      >
                        <Save size={16} aria-hidden="true" />
                        Save
                      </button>
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={9}>No registered users found.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function NavButton({
  page,
  current,
  onClick,
  icon,
  children
}: {
  page: Page;
  current: Page;
  onClick: (page: Page) => void;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <button className={`nav-item ${page === current ? "nav-item-active" : ""}`} type="button" onClick={() => onClick(page)}>
      {icon}
      {children}
    </button>
  );
}

function StatusPill({ status }: { status: JobStatus }) {
  const Icon = status === "completed" ? CheckCircle2 : status === "failed" ? AlertTriangle : Clock3;
  return (
    <span className={`status-pill status-${status}`}>
      <Icon size={13} aria-hidden="true" />
      {humanize(status)}
    </span>
  );
}

function ProgressBar({ value }: { value: number }) {
  return (
    <div className="progress-bar" aria-label={`Progress ${value}%`}>
      <span style={{ width: `${Math.max(0, Math.min(value, 100))}%` }} />
    </div>
  );
}

function SummaryTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="summary-tile">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function IntegrationCard({ title, status, description }: { title: string; status: string; description: string }) {
  return (
    <article className="integration-card">
      <LayoutDashboard size={22} aria-hidden="true" />
      <div>
        <h2>{title}</h2>
        <StatusText>{status}</StatusText>
        <p>{description}</p>
      </div>
    </article>
  );
}

function StatusText({ children }: { children: React.ReactNode }) {
  return <span className="status-text">{children}</span>;
}

function NoticeBanner({ notice }: { notice: Exclude<Notice, null> }) {
  return <div className={`notice notice-${notice.type}`}>{notice.message}</div>;
}

function useHashPage(): [Page, (page: Page) => void] {
  const [page, setPageState] = useState<Page>(() => parseHash(window.location.hash));
  useEffect(() => {
    const onHashChange = () => setPageState(parseHash(window.location.hash));
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);
  const setPage = useCallback((nextPage: Page) => {
    window.location.hash = `/${nextPage}`;
    setPageState(nextPage);
  }, []);
  return [page, setPage];
}

function parseHash(hash: string): Page {
  const value = hash.replace(/^#\/?/, "").split("/")[0] as Page;
  if (["intake", "integrations", "rubrics", "users", "profile"].includes(value)) {
    return value;
  }
  return "intake";
}

function pageTitle(page: Page): string {
  const titles: Record<Page, string> = {
    intake: "Applicant document intake",
    integrations: "Integrations",
    rubrics: "Rubrics",
    users: "Users",
    profile: "Profile"
  };
  return titles[page];
}

function pageEyebrow(page: Page): string {
  const eyebrows: Record<Page, string> = {
    intake: "Document operations",
    integrations: "Connections",
    rubrics: "Rubric operations",
    users: "Administration",
    profile: "Billing"
  };
  return eyebrows[page];
}

function pageDescription(page: Page): string {
  const descriptions: Record<Page, string> = {
    intake: "Upload applicant materials, track parsing progress, review extracted details, and monitor recent intake activity.",
    integrations: "Connect external document sources and admissions systems as the workspace expands.",
    rubrics: "Create and maintain program scoring templates used during document intake.",
    users: "Admin-only view of accounts created for this workspace.",
    profile: "Review processed-document usage, billable units, and current charges for this account."
  };
  return descriptions[page];
}

function mergeRecentJobs(current: Job[], updates: Job[]): Job[] {
  const byId = new Map(current.map((job) => [job.job_id, job]));
  for (const job of updates) {
    byId.set(job.job_id, job);
  }
  return Array.from(byId.values())
    .sort((left, right) => new Date(right.received_at).getTime() - new Date(left.received_at).getTime())
    .slice(0, 20);
}

function ensureDefaultSections(sections: SectionAnalysis[]): SectionAnalysis[] {
  return fallbackRubrics[0].sections.map((rubricSection) => {
    return (
      sections.find((section) => section.section_id === rubricSection.section_id) ?? {
        section_id: rubricSection.section_id,
        label: rubricSection.name,
        score: 0,
        max_score: rubricSection.max_points,
        evidence: ["Missing or not parsed yet. Human review required."],
        rubric_criteria: [`${rubricSection.name} rubric review`],
        status: "missing"
      }
    );
  });
}

function localId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function fileExtension(filename: string): string {
  return filename.split(".").pop()?.toLowerCase() ?? "";
}

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Logo could not be read."));
    reader.onload = () => {
      if (typeof reader.result === "string") {
        resolve(reader.result);
        return;
      }
      reject(new Error("Logo could not be read."));
    };
    reader.readAsDataURL(file);
  });
}

function pageCountForJob(job: Job): number {
  const extractedRecord = job.extracted_record ?? {};
  const documentRecord = asRecord(extractedRecord.document);
  const metadataRecord = asRecord(extractedRecord.metadata);
  const directCount =
    positiveNumber(job.page_count) ??
    positiveNumber(extractedRecord.page_count) ??
    positiveNumber(documentRecord?.page_count) ??
    positiveNumber(metadataRecord?.page_count);
  if (directCount) {
    return Math.ceil(directCount);
  }
  if (Array.isArray(extractedRecord.pages) && extractedRecord.pages.length) {
    return extractedRecord.pages.length;
  }
  return 1;
}

function billableUnitsForPages(pages: number): number {
  return pages > 0 ? Math.max(1, Math.ceil(pages / 10)) : 0;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function positiveNumber(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
    return null;
  }
  return value;
}

function formatCurrency(value: number): string {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD"
  }).format(value);
}

function formatRate(value: number): string {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: Number.isInteger(value) ? 0 : 2
  }).format(value);
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDateTime(value: string | null): string {
  if (!value) {
    return "Pending";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Unknown";
  }
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit"
  }).format(date);
}

function formatScore(score: number, maxScore: number): string {
  return `${trimNumber(score)} / ${trimNumber(maxScore)}`;
}

function trimNumber(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

function humanize(value: string | null | undefined): string {
  if (!value) {
    return "Pending";
  }
  return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function displayUserRole(role: string): string {
  return role === "admissions_reviewer" ? "User" : humanize(role);
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unexpected error";
}
