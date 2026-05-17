import { humanize } from "../lib/format";

type StatusPillProps = {
  value: string | null | undefined;
  tone?: "neutral" | "warning" | "success" | "danger";
};

export function StatusPill({ value, tone = "neutral" }: StatusPillProps) {
  return <span className={`status-pill status-${tone}`}>{humanize(value)}</span>;
}
