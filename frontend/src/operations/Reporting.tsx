import { useState } from "react";
import { setWorkspaceId } from "../api/client";
import type { Billing, Invoice, Platform, Reports } from "./api";
import { download, ops } from "./api";
import { Banner, Empty, Field, Panel, State, Table, date, go, human, money, useAction, useLoad } from "./shared";

export function ReportsPage() {
  const load = useLoad<Reports>("/reports");
  return <><State {...load} />{load.data ? <>
    <div className="workflow-metrics"><span><strong>{load.data.late_versions}</strong> late document versions</span><span><strong>{load.data.reopened_cases}</strong> cases needing rechecking</span></div>
    <Panel title="Completeness by program"><Table headings={["Program", "Applications", "Average completeness", "Ready for review"]}>{load.data.programs.map(program => <tr key={program.program}><td>{program.program}</td><td>{program.applications}</td><td>{program.average_completeness}%</td><td>{program.ready}</td></tr>)}</Table></Panel>
    <div className="workflow-grid"><Panel title="Frequently missing evidence">{Object.entries(load.data.missing).map(([label, count]) => <p key={label}>{label}: {count}</p>)}</Panel><Panel title="Processing health">{Object.entries(load.data.processing).map(([status, count]) => <p key={status}>{human(status)}: {count}</p>)}</Panel></div>
    <Panel title="Reviewer throughput"><Table headings={["Reviewer", "Assigned", "Signed revisions", "Average score", "Average turnaround (hours)"]}>{load.data.reviewers.map(reviewer => <tr key={reviewer.reviewer}><td>{reviewer.reviewer}</td><td>{reviewer.assigned}</td><td>{reviewer.signed_revisions}</td><td>{reviewer.average_score ?? "—"}</td><td>{reviewer.average_turnaround_hours ?? "—"}</td></tr>)}</Table></Panel>
    <Panel title="Workflow stages">{Object.entries(load.data.states).map(([status, count]) => <p key={status}>{human(status)}: {count}</p>)}</Panel>
  </> : null}</>;
}

export function BillingPage({ platformAdmin }: { platformAdmin: boolean }) {
  const load = useLoad<Billing>("/billing"); const action = useAction(load.refresh);
  return <><State {...load} /><Banner {...action} /><Panel title="University billing"><p>Successful documents retain their original unit rule and price. Each document version is rounded independently; failed processing is not billed.</p><button className="secondary-button" disabled={action.busy} onClick={() => void action.run(() => download("/billing/usage.csv", "university-document-usage.csv"), "Usage downloaded.")}>Export usage CSV</button>
    <Table headings={["Document", "Pages", "Unit rule", "Units", "Charge", "Processed"]}>{load.data?.usage.map(item => <tr key={item.id}><td>{item.filename}</td><td>{item.page_count}</td><td>{item.contract_snapshot.pages_per_unit} pages/unit</td><td>{item.units}</td><td>{money(item.charge_cents, item.currency)}</td><td>{date(item.occurred_at)}</td></tr>)}</Table>{!load.data?.usage.length ? <Empty>No successful billable documents yet.</Empty> : null}
  </Panel>
    {platformAdmin ? <Panel title="Publish contract version"><form className="workflow-form" onSubmit={event => { event.preventDefault(); const form = new FormData(event.currentTarget); void action.run(() => ops("/billing/contracts", "POST", { pages_per_unit: Number(form.get("pages")), price_per_unit_cents: Number(form.get("price")), currency: form.get("currency"), billing_timezone: form.get("timezone"), invoice_terms_days: Number(form.get("terms")) }), "Contract version published for subsequent processing."); }}>
      <Field label="Pages per billable unit"><input type="number" name="pages" min={1} defaultValue={10} required /></Field><Field label="Price per unit (cents / paise)"><input type="number" name="price" min={0} defaultValue={100} required /></Field><Field label="Currency"><input name="currency" pattern="[A-Z]{3}" defaultValue="USD" required /></Field><Field label="Billing timezone"><input name="timezone" defaultValue="Asia/Kolkata" required /></Field><Field label="Invoice terms days"><input type="number" name="terms" min={0} defaultValue={30} required /></Field><button className="primary-button" disabled={action.busy}>Publish commercial terms</button>
    </form></Panel> : null}
    <Panel title="Contract history"><Table headings={["Version", "Effective from", "Unit rule", "Price", "Terms"]}>{load.data?.contracts.map(contract => <tr key={contract.id}><td>{contract.version}</td><td>{date(contract.effective_from)}</td><td>{contract.pages_per_unit} pages</td><td>{money(contract.price_per_unit_cents, contract.currency)}</td><td>{contract.invoice_terms_days} days · {contract.billing_timezone}</td></tr>)}</Table></Panel>
    <Panel title="Invoices">{platformAdmin ? <button className="primary-button" disabled={action.busy || !load.data?.usage.some(item => !item.invoice_id)} onClick={() => void action.run(() => ops("/billing/invoices", "POST"), "Draft invoices generated from unbilled usage.")}>Generate invoice drafts</button> : null}{load.data?.invoices.map(invoice => <InvoiceCard key={`${invoice.id}:${invoice.revision}`} invoice={invoice} platformAdmin={platformAdmin} refresh={load.refresh} />)}{!load.data?.invoices.length ? <Empty>No invoices generated.</Empty> : null}</Panel>
  </>;
}

function InvoiceCard({ invoice, platformAdmin, refresh }: { invoice: Invoice; platformAdmin: boolean; refresh: () => void }) {
  const action = useAction(refresh); const [status, setStatus] = useState(invoice.status);
  const next: Record<string, string[]> = { draft: ["issued", "void"], issued: ["paid", "overdue", "void"], overdue: ["paid", "void"], paid: ["credited"], void: [], credited: [] };
  return <article className="workflow-card"><h3>{invoice.invoice_number} · {money(invoice.total_cents, invoice.currency)} · {invoice.status}</h3><p>Usage {date(invoice.period_start)}–{date(invoice.period_end)} · Due {invoice.due_at}</p><Banner {...action} /><details><summary>Invoice lines</summary><Table headings={["Document", "Units", "Charge"]}>{invoice.lines.map(line => <tr key={line.id}><td>{line.filename}</td><td>{line.units}</td><td>{money(line.charge_cents, line.currency)}</td></tr>)}</Table></details>
    {platformAdmin && next[invoice.status].length ? <form className="workflow-form" onSubmit={event => { event.preventDefault(); void action.run(() => ops(`/billing/invoices/${invoice.id}`, "PUT", { status })); }}><Field label="Invoice status"><select value={status} onChange={event => setStatus(event.target.value)}>{[invoice.status, ...next[invoice.status]].map(value => <option key={value}>{value}</option>)}</select></Field><button className="secondary-button" disabled={action.busy || status === invoice.status}>Update status</button></form> : null}
  </article>;
}

export function PlatformPage() {
  const load = useLoad<Platform>("/platform");
  return <><State {...load} /><Panel title="University account health"><Table headings={["University", "Members", "Applications", "Documents / units", "Usage", "Activation", "Action"]}>{load.data?.universities.map(university => <tr key={university.id}><td>{university.name}</td><td>{university.members}</td><td>{university.applications}</td><td>{university.documents} / {university.units}</td><td>{Object.entries(university.spend).map(([currency, cents]) => money(cents, currency)).join(", ") || "—"}</td><td>{Object.entries(university.onboarding).map(([step, ready]) => `${ready ? "✓" : "○"} ${human(step)}`).join(" · ")} · {university.contract_versions} contracts</td><td><button className="secondary-button" onClick={() => { setWorkspaceId(university.id); go("billing"); window.location.reload(); }}>Open workspace billing</button></td></tr>)}</Table></Panel>
    <Panel title="Recent platform activity"><Table headings={["When", "Actor", "Action"]}>{load.data?.activity.map((item, index) => <tr key={index}><td>{date(item.timestamp)}</td><td>{item.actor}</td><td>{human(item.action)}</td></tr>)}</Table></Panel>
  </>;
}
