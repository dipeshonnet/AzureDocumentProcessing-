export function applicantName(firstName: string, lastName: string): string {
  return `${firstName} ${lastName}`.trim();
}

export function formatDate(value: string | null | undefined): string {
  if (!value) {
    return "Not submitted";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Unknown date";
  }
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric"
  }).format(date);
}

export function formatScore(value: number | null | undefined, max?: number | null): string {
  if (value === null || value === undefined) {
    return "Not scored";
  }
  const rounded = Number.isInteger(value) ? value.toString() : value.toFixed(2);
  if (max === null || max === undefined) {
    return rounded;
  }
  const maxValue = Number.isInteger(max) ? max.toString() : max.toFixed(2);
  return `${rounded} / ${maxValue}`;
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return "Unknown";
  }
  return `${Math.round(value * 100)}%`;
}

export function humanize(value: string | null | undefined): string {
  if (!value) {
    return "Unknown";
  }
  return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function confidenceTone(value: number | null | undefined): "low" | "medium" | "high" | "unknown" {
  if (value === null || value === undefined) {
    return "unknown";
  }
  if (value < 0.65) {
    return "low";
  }
  if (value < 0.85) {
    return "medium";
  }
  return "high";
}
