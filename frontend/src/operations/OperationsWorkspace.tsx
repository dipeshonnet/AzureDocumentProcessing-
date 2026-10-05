import { useEffect, useState } from "react";
import type { Context } from "./api";
import { setWorkspaceId } from "../api/client";
import Programs from "./Programs";
import CaseDetail from "./CaseDetail";
import { ApplicationsPage, DashboardPage } from "./Cases";
import { ExchangePage, PeoplePage, SettingsPage, TrainingPage } from "./Administration";
import { BillingPage, PlatformPage, ReportsPage } from "./Reporting";
import { Banner, Field, State, go, useLoad } from "./shared";
import "./operations.css";

export default function OperationsWorkspace() {
  const load = useLoad<Context>("/context"); const [route, setRoute] = useState(() => window.location.hash.split("/").slice(2));
  useEffect(() => { const update = () => setRoute(window.location.hash.split("/").slice(2)); window.addEventListener("hashchange", update); return () => window.removeEventListener("hashchange", update); }, []);
  const context = load.data; const manager = context?.role === "university_owner" || context?.role === "admissions_manager";
  const finance = context?.role === "finance_viewer"; const reviewer = context?.role === "reviewer"; const auditor = context?.role === "auditor";
  const section = route[0] || (finance ? "billing" : "dashboard");
  const pages = context ? [
    ...(!finance ? [["dashboard", "Cases"], ["applications", "Applications"], ["reviews", "Review queue"]] : []),
    ...(manager ? [["programs", "Programs & Criteria"], ["people", "People"], ["exchange", "Data exchange"], ["settings", "Institution setup"]] : []),
    ...(manager || finance || auditor ? [["reports", "Reports"]] : []),
    ...(manager || finance ? [["billing", "Billing"]] : []),
    ...(context.platform_admin ? [["platform", "Platform overview"]] : []),
    ...(manager && context.settings.fictional_example_enabled ? [["training", "Training example"]] : [])
  ] : [];
  const accessible = pages.some(([id]) => id === section) || (section === "case" && !finance);
  return <div className="workflow-workspace" style={{ "--workflow-accent": context?.settings.brand_color ?? "#216b58" } as React.CSSProperties}><State {...load} />{context ? <>
    <div className="workflow-toolbar"><h2>{context.settings.brand_name}</h2><Field label="Active university workspace"><select value={context.university_id} onChange={event => { setWorkspaceId(event.target.value); window.location.hash = "#/operations"; window.location.reload(); }}>{context.memberships.map(member => <option key={member.id} value={member.id}>{member.name}</option>)}</select></Field><span>{context.role.replace(/_/g, " ")}</span></div>
    <nav className="workflow-tabs" aria-label="Admissions workspace">{pages.map(([id, label]) => <button className={section === id ? "active" : ""} key={id} onClick={() => go(id)}>{label}</button>)}</nav>
    {!accessible ? <Banner error="Your workspace role cannot access this section." /> : <>
      {section === "dashboard" ? <DashboardPage canManage={manager} /> : null}
      {section === "reviews" ? <DashboardPage canManage={manager} reviewQueue /> : null}
      {section === "applications" ? <ApplicationsPage canManage={manager} /> : null}
      {section === "case" && route[1] ? <CaseDetail key={route[1]} id={route[1]} tab={route[2] || "overview"} canManage={manager} reviewer={reviewer} /> : null}
      {section === "programs" ? <Programs canManage={manager} /> : null}
      {section === "people" ? <PeoplePage owner={context.role === "university_owner"} /> : null}
      {section === "settings" ? <SettingsPage key={context.university_id} context={context} refresh={load.refresh} /> : null}
      {section === "exchange" ? <ExchangePage /> : null}
      {section === "reports" ? <ReportsPage /> : null}
      {section === "billing" ? <BillingPage platformAdmin={context.platform_admin} /> : null}
      {section === "platform" ? <PlatformPage /> : null}
      {section === "training" ? <TrainingPage /> : null}
    </>}
  </> : null}</div>;
}
