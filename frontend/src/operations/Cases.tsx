import { useState } from "react";
import type { Case, Dashboard, People, Program } from "./api";
import { ops } from "./api";
import { Banner, Empty, Field, Panel, Progress, State, Table, date, go, human, useAction, useLoad } from "./shared";

export function CaseRows({ cases }: { cases: Case[] }) {
  return <Table headings={["Applicant", "Program / intake", "Evidence", "Reviewer", "Due", "Next action"]}>{cases.map(item => <tr key={item.id}>
    <td><a href={`#/operations/case/${item.id}/overview`}>{item.applicant_name}</a><small>{item.external_candidate_id || item.candidate_id}</small></td>
    <td>{item.program_name}<small>{item.intake_term}</small></td><td><Progress value={item.completeness} /><small>{human(item.status)} · {item.missing_requirements} missing</small></td>
    <td>{item.assignments.map(a => a.reviewer_email).join(", ") || "Unassigned"}</td><td>{item.assignments.map(a => a.due_at || "—").join(", ") || "—"}</td>
    <td><a href={`#/operations/case/${item.id}/${item.completeness === 100 ? "evaluation" : "requirements"}`}>{item.completeness === 100 ? "Review case" : "Complete evidence"}</a></td>
  </tr>)}</Table>;
}

export function DashboardPage({ canManage, reviewQueue = false }: { canManage: boolean; reviewQueue?: boolean }) {
  const load = useLoad<Dashboard>("/dashboard");
  const cases = load.data?.cases.filter(item => !reviewQueue || ["in_review", "ready_for_review", "evidence_changed"].includes(item.status));
  return <><State {...load} />{load.data ? <>
    {canManage && Object.values(load.data.setup).some(value => !value) ? <Panel title="Launch checklist"><div className="workflow-toolbar">{Object.entries(load.data.setup).map(([key, done]) => <button key={key} className="secondary-button" onClick={() => go(key === "published_program" ? "programs" : key === "reviewer" ? "people" : "exchange")}>{done ? "✓" : "○"} {human(key)}</button>)}</div></Panel> : null}
    <div className="workflow-metrics"><span><strong>{load.data.action_count}</strong> need action</span><span><strong>{load.data.blocked_count}</strong> waiting for evidence</span><button className="secondary-button" onClick={load.refresh}>Refresh queue</button></div>
    <Panel title={reviewQueue ? "Review queue" : "Needs my action"}>{cases?.length ? <CaseRows cases={cases} /> : <Empty>No live cases need attention.</Empty>}</Panel>
  </> : null}</>;
}

export function ApplicationsPage({ canManage }: { canManage: boolean }) {
  const [filters, setFilters] = useState({ q: "", program_id: "", status: "", completeness: "" });
  const [query, setQuery] = useState(""); const load = useLoad<Case[]>(`/cases${query}`); const programs = useLoad<Program[]>("/programs"); const people = useLoad<People>(canManage ? "/people" : null);
  const action = useAction(load.refresh); const [createOpen, setCreateOpen] = useState(false);
  return <><State {...load} /><Banner {...action} /><Panel title="Applications">
    <form className="workflow-form" onSubmit={event => { event.preventDefault(); setQuery(`?${new URLSearchParams(filters)}`); }}>
      <Field label="Find a candidate"><input type="search" placeholder="Name, candidate UUID or external ID" value={filters.q} onChange={event => setFilters({ ...filters, q: event.target.value })} /></Field>
      <Field label="Program"><select value={filters.program_id} onChange={event => setFilters({ ...filters, program_id: event.target.value })}><option value="">All programs</option>{programs.data?.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></Field>
      <Field label="Completeness"><select value={filters.completeness} onChange={event => setFilters({ ...filters, completeness: event.target.value })}><option value="">Any completeness</option><option value="complete">Complete</option><option value="incomplete">Missing evidence</option></select></Field>
      <Field label="Workflow"><select value={filters.status} onChange={event => setFilters({ ...filters, status: event.target.value })}><option value="">All active stages</option>{["awaiting_documents", "ready_for_review", "in_review", "review_complete", "evidence_changed", "withdrawn", "legacy"].map(status => <option key={status} value={status}>{human(status)}</option>)}</select></Field><button className="secondary-button">Apply filters</button>
    </form>{load.data?.length ? <CaseRows cases={load.data} /> : !load.loading ? <Empty>No applications match these filters.</Empty> : null}
  </Panel>{canManage ? <><button className="primary-button" onClick={() => setCreateOpen(value => !value)}>Create application</button>{createOpen ? <Panel title="New program application"><form className="workflow-form" onSubmit={event => { event.preventDefault(); const form = new FormData(event.currentTarget); void action.run(async () => {
    const item = await ops<Case>("/cases", "POST", { applicant_name: form.get("applicant_name"), external_candidate_id: form.get("external_candidate_id"), external_application_id: form.get("external_application_id"), source: form.get("source"), intake_term: form.get("intake_term"), program_id: form.get("program_id"), department_id: form.get("department_id") || null, candidate_id: form.get("candidate_id") || null }); go(`case/${item.id}/requirements`);
  }); }}>
    <Field label="Applicant name"><input name="applicant_name" required maxLength={200} /></Field><Field label="University candidate ID"><input name="external_candidate_id" required maxLength={120} /></Field><Field label="ID source"><select name="source"><option value="sis">SIS</option><option value="crm">CRM</option><option value="portal">Portal</option></select></Field>
    <Field label="External application ID"><input name="external_application_id" maxLength={120} /></Field><Field label="Existing candidate UUID (optional)"><input name="candidate_id" /></Field><Field label="Program"><select name="program_id" required defaultValue=""><option value="" disabled>Select a published program</option>{programs.data?.filter(p => p.active_version_id).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></Field><Field label="Intake / cycle"><input name="intake_term" required maxLength={80} /></Field><Field label="Department queue"><select name="department_id"><option value="">No department</option>{people.data?.departments.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}</select></Field><button className="primary-button" disabled={action.busy}>Create case</button>
  </form></Panel> : null}</> : null}</>;
}
