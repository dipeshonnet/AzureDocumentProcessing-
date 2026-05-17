import type { EvidenceSnippet } from "../api/types";

type EvidenceListProps = {
  evidence: EvidenceSnippet[];
  emptyLabel?: string;
};

export function EvidenceList({ evidence, emptyLabel = "No evidence snippets available." }: EvidenceListProps) {
  if (!evidence.length) {
    return <p className="muted compact">{emptyLabel}</p>;
  }
  return (
    <ul className="evidence-list">
      {evidence.map((item, index) => (
        <li key={`${item.snippet}-${index}`}>
          <blockquote>{item.snippet}</blockquote>
          <span>
            {item.criterion_name ? `${item.criterion_name} | ` : ""}
            {item.page_number ? `Page ${item.page_number}` : "Page unknown"}
            {item.source ? ` | ${item.source}` : ""}
          </span>
        </li>
      ))}
    </ul>
  );
}
