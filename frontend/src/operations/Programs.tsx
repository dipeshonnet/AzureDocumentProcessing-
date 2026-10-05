import { useState } from "react";
import type { Criterion, Program, RequirementDefinition, Template } from "./api";
import { ops } from "./api";
import { Banner, Empty, Field, Panel, State, useAction, useLoad } from "./shared";

const newRequirement = (): RequirementDefinition => ({ key: "", label: "", capture_type: "document", required: true, min_count: 1, max_count: 1, instructions: "", allowed_extensions: [".pdf", ".docx", ".txt", ".png", ".jpg", ".jpeg"], upload_channels: ["staff", "secure_link"], due_days: null });
const newCriterion = (): Criterion => ({ key: "", label: "", weight: 100, instructions: "", evidence_keys: [] });

export default function Programs({ canManage }: { canManage: boolean }) {
  const load = useLoad<Program[]>("/programs"); const [selected, setSelected] = useState(""); const action = useAction(load.refresh);
  return <><State {...load} /><Banner {...action} />
    {canManage ? <Panel title="Create program"><form className="workflow-form" onSubmit={event => { event.preventDefault(); const form = new FormData(event.currentTarget); void action.run(async () => { const created = await ops<Program>("/programs", "POST", { name: form.get("name"), code: form.get("code") }); setSelected(created.id); }); }}>
      <Field label="Program name"><input name="name" required maxLength={200} /></Field><Field label="Program code"><input name="code" required pattern="[A-Za-z0-9_-]+" maxLength={80} /></Field><button className="primary-button" disabled={action.busy}>Create program</button>
    </form></Panel> : null}
    <Panel title="Programs & criteria"><Field label="Program"><select value={selected} onChange={event => setSelected(event.target.value)}><option value="">Select a program</option>{load.data?.map(program => <option key={program.id} value={program.id}>{program.name} ({program.code}) · {program.active_version_id ? "Published" : "Draft only"}</option>)}</select></Field>{!load.data?.length && !load.loading ? <Empty>Create a program, define its evidence checklist, then publish it.</Empty> : null}</Panel>
    {selected ? <Versions key={selected} programId={selected} canManage={canManage} onPublish={load.refresh} /> : null}
  </>;
}

function Versions({ programId, canManage, onPublish }: { programId: string; canManage: boolean; onPublish: () => void }) {
  const load = useLoad<Template[]>(`/programs/${programId}/versions`); const [selected, setSelected] = useState<string | null>(null);
  const [editor, setEditor] = useState<{ name: string; requirements: RequirementDefinition[]; criteria: Criterion[]; revision?: number } | null>(null);
  const action = useAction(() => { load.refresh(); onPublish(); });
  const current = selected ? load.data?.find(version => version.id === selected) : load.data?.[load.data.length - 1];
  const edit = (version?: Template) => { setEditor({ name: version?.name ?? "Program criteria", requirements: version?.requirements.map(item => ({ ...item })) ?? [], criteria: version?.criteria.map(item => ({ ...item })) ?? [], ...(version?.status === "draft" ? { revision: version.revision } : {}) }); };
  const draft = load.data?.find(version => version.status === "draft");
  function updateRequirement(index: number, values: Partial<RequirementDefinition>) { if (editor) setEditor({ ...editor, requirements: editor.requirements.map((r, i) => i === index ? { ...r, ...values } : r) }); }
  function updateCriterion(index: number, values: Partial<Criterion>) { if (editor) setEditor({ ...editor, criteria: editor.criteria.map((r, i) => i === index ? { ...r, ...values } : r) }); }
  return <><State {...load} /><Banner {...action} />
    <Panel title="Criteria versions"><div className="workflow-toolbar">{load.data?.map(version => <button key={version.id} className="secondary-button" onClick={() => { setSelected(version.id); setEditor(null); }}>v{version.version} · {version.status}</button>)}{canManage ? <button className="primary-button" onClick={() => edit(draft ?? current)}>{draft ? "Edit draft" : "Create next draft"}</button> : null}</div>
      {current && !editor ? <><h3>{current.name} · version {current.version}</h3><p>Applications stay pinned to this version. Published versions cannot be edited.</p><div className="workflow-grid"><div><h3>Candidate checklist</h3>{current.requirements.map(r => <p key={r.key}><strong>{r.label}</strong> · {r.required ? "Required" : "Optional"} · {r.min_count}–{r.max_count} {r.capture_type} item(s)</p>)}</div><div><h3>Reviewer form</h3>{current.criteria.map(c => <p key={c.key}>{c.label} · {c.weight} points · evidence: {c.evidence_keys.join(", ") || "None mapped"}</p>)}</div></div></> : null}
    </Panel>
    {editor ? <Panel title="Criteria draft"><form onSubmit={event => { event.preventDefault(); void action.run(async () => { const saved = await ops<Template>(`/programs/${programId}/draft`, "POST", editor); setSelected(saved.id); setEditor(null); }, "Draft saved. Publish it when ready."); }}>
      <Field label="Template name"><input value={editor.name} required onChange={event => setEditor({ ...editor, name: event.target.value })} /></Field>
      <h3>Application requirements</h3><p>Collection requirements are independent of numeric scoring.</p>
      {editor.requirements.map((requirement, index) => <fieldset className="workflow-card" key={index}><legend>Requirement {index + 1}</legend><div className="workflow-grid">
        <Field label="Requirement label"><input required value={requirement.label} onChange={event => updateRequirement(index, { label: event.target.value })} /></Field>
        <Field label="Stable requirement key"><input required pattern="[a-z][a-z0-9_]*" value={requirement.key} onChange={event => updateRequirement(index, { key: event.target.value })} /></Field>
        <Field label="Evidence type"><select value={requirement.capture_type} onChange={event => updateRequirement(index, { capture_type: event.target.value as RequirementDefinition["capture_type"] })}>{["document", "number", "text", "external"].map(type => <option key={type}>{type}</option>)}</select></Field>
        <Field label="Minimum items"><input type="number" min={requirement.required ? 1 : 0} max={20} value={requirement.min_count} onChange={event => updateRequirement(index, { min_count: Number(event.target.value) })} /></Field>
        <Field label="Maximum items"><input type="number" min={1} max={20} value={requirement.max_count} onChange={event => updateRequirement(index, { max_count: Number(event.target.value) })} /></Field>
        <Field label="Due after days"><input type="number" min={0} value={requirement.due_days ?? ""} onChange={event => updateRequirement(index, { due_days: event.target.value === "" ? null : Number(event.target.value) })} /></Field>
      </div><label className="workflow-check"><input type="checkbox" checked={requirement.required} onChange={event => updateRequirement(index, { required: event.target.checked, min_count: event.target.checked ? Math.max(1, requirement.min_count) : 0 })} />Required evidence</label>
      <Field label="Requirement instructions"><textarea value={requirement.instructions} onChange={event => updateRequirement(index, { instructions: event.target.value })} /></Field>
      {requirement.capture_type === "document" ? <><p>Allowed file formats</p><div className="workflow-toolbar">{[".pdf", ".docx", ".txt", ".png", ".jpg", ".jpeg"].map(extension => <label key={extension}><input type="checkbox" checked={requirement.allowed_extensions.includes(extension)} onChange={event => updateRequirement(index, { allowed_extensions: event.target.checked ? [...requirement.allowed_extensions, extension] : requirement.allowed_extensions.filter(e => e !== extension) })} />{extension}</label>)}</div><div className="workflow-toolbar">{(["staff", "secure_link"] as const).map(channel => <label key={channel}><input type="checkbox" checked={requirement.upload_channels.includes(channel)} onChange={event => updateRequirement(index, { upload_channels: event.target.checked ? [...requirement.upload_channels, channel] : requirement.upload_channels.filter(c => c !== channel) })} />{channel === "staff" ? "Staff upload" : "Secure applicant link"}</label>)}</div></> : null}
      <button type="button" className="ghost-button" onClick={() => setEditor({ ...editor, requirements: editor.requirements.filter((_, i) => i !== index) })}>Remove requirement</button></fieldset>)}
      <button type="button" className="secondary-button" onClick={() => setEditor({ ...editor, requirements: [...editor.requirements, newRequirement()] })}>Add requirement</button>
      <h3>Scoring criteria</h3><p>Optional. If configured, weights must total 100. Current total: {editor.criteria.reduce((sum, c) => sum + c.weight, 0)}.</p>
      {editor.criteria.map((criterion, index) => <fieldset className="workflow-card" key={index}><legend>Criterion {index + 1}</legend><div className="workflow-grid"><Field label="Criterion label"><input required value={criterion.label} onChange={event => updateCriterion(index, { label: event.target.value })} /></Field><Field label="Stable criterion key"><input required pattern="[a-z][a-z0-9_]*" value={criterion.key} onChange={event => updateCriterion(index, { key: event.target.value })} /></Field><Field label="Weight / maximum score"><input type="number" min={0.01} max={100} step="any" value={criterion.weight} onChange={event => updateCriterion(index, { weight: Number(event.target.value) })} /></Field></div>
        <Field label="Reviewer instructions"><textarea value={criterion.instructions} onChange={event => updateCriterion(index, { instructions: event.target.value })} /></Field><Field label="Mapped evidence requirements"><select multiple value={criterion.evidence_keys} onChange={event => updateCriterion(index, { evidence_keys: Array.from(event.target.selectedOptions, option => option.value) })}>{editor.requirements.filter(r => r.key).map(r => <option key={r.key} value={r.key}>{r.label || r.key}</option>)}</select></Field><button type="button" className="ghost-button" onClick={() => setEditor({ ...editor, criteria: editor.criteria.filter((_, i) => i !== index) })}>Remove criterion</button>
      </fieldset>)}
      <button type="button" className="secondary-button" onClick={() => setEditor({ ...editor, criteria: [...editor.criteria, newCriterion()] })}>Add criterion</button>
      <div className="workflow-toolbar"><button className="primary-button" disabled={action.busy}>Save draft</button><button className="secondary-button" type="button" onClick={() => setEditor(null)}>Cancel</button></div>
    </form></Panel> : null}
    {current?.status === "draft" && !editor && canManage ? <Panel title="Publish criteria"><p>Publishing applies this version to new applications. Existing applications retain their original version.</p><button className="primary-button" disabled={action.busy || !current.requirements.length} onClick={() => void action.run(() => ops(`/versions/${current.id}/publish`, "POST", { revision: current.revision, reason: "Publish program criteria" }), "Criteria published.")}>Publish version {current.version}</button></Panel> : null}
  </>;
}
