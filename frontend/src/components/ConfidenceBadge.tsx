import { formatPercent, confidenceTone } from "../lib/format";

type ConfidenceBadgeProps = {
  value: number | null | undefined;
  label?: string;
};

export function ConfidenceBadge({ value, label = "Confidence" }: ConfidenceBadgeProps) {
  const tone = confidenceTone(value);
  return (
    <span className={`confidence confidence-${tone}`} aria-label={`${label}: ${formatPercent(value)}`}>
      <span>{label}</span>
      <strong>{formatPercent(value)}</strong>
    </span>
  );
}
