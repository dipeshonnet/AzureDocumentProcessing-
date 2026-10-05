import { useCallback, useEffect, useState } from "react";
import { ops } from "./api";

export function useLoad<T>(path: string | null) {
  const [data, setData] = useState<T>(); const [error, setError] = useState(""); const [loading, setLoading] = useState(true);
  const [generation, setGeneration] = useState(0);
  const refresh = useCallback(() => setGeneration(value => value + 1), []);
  useEffect(() => {
    if (!path) { setLoading(false); setData(undefined); return; }
    let active = true; setLoading(true); setError("");
    ops<T>(path).then(value => { if (active) setData(value); }).catch(reason => { if (active) setError(message(reason)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [path, generation]);
  return { data, error, loading, refresh };
}

export function useAction(onSuccess?: () => void) {
  const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const [notice, setNotice] = useState("");
  async function run(action: () => Promise<unknown>, success = "Saved.") {
    if (busy) return false;
    setBusy(true); setError(""); setNotice("");
    try { await action(); setNotice(success); onSuccess?.(); return true; }
    catch (reason) { setError(message(reason)); return false; }
    finally { setBusy(false); }
  }
  return { busy, error, notice, run };
}

export function message(error: unknown): string { return error instanceof Error ? error.message : "The operation failed."; }
export function human(value: string): string { return value.replace(/_/g, " "); }
export function money(cents: number, currency: string): string { return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(cents / 100); }
export function date(value: string | null | undefined): string { return value ? new Date(value).toLocaleString() : "—"; }
export function Banner({ error, notice }: { error?: string; notice?: string }) {
  return <>{error ? <p role="alert" className="notice notice-error">{error}</p> : null}{notice ? <p role="status" className="notice notice-success">{notice}</p> : null}</>;
}
export function State({ loading, error }: { loading: boolean; error: string }) { return <>{loading ? <p role="status">Loading workspace records…</p> : null}<Banner error={error} /></>; }
export function Field({ label, children }: { label: string; children: React.ReactNode }) { return <label className="workflow-field"><span>{label}</span>{children}</label>; }
export function Panel({ title, children }: { title: string; children: React.ReactNode }) { return <section className="panel workflow-panel"><h2>{title}</h2>{children}</section>; }
export function Empty({ children }: { children: React.ReactNode }) { return <p className="empty-state">{children}</p>; }
export function Progress({ value }: { value: number }) { return <span className="workflow-progress"><progress max="100" value={value} aria-label="Required evidence completeness" />{value}%</span>; }
export function go(section: string) { window.location.hash = `#/operations/${section}`; }
export function Table({ headings, children }: { headings: string[]; children: React.ReactNode }) {
  return <div className="responsive-table"><table><thead><tr>{headings.map(heading => <th key={heading}>{heading}</th>)}</tr></thead><tbody>{children}</tbody></table></div>;
}
