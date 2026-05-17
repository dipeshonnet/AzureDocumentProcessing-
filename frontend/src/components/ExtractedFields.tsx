import { AlertTriangle } from "lucide-react";

type ExtractedFieldsProps = {
  value: unknown;
  depth?: number;
};

export function ExtractedFields({ value, depth = 0 }: ExtractedFieldsProps) {
  if (value === null || value === undefined || value === "") {
    return <span className="muted">Not found</span>;
  }

  if (Array.isArray(value)) {
    if (!value.length) {
      return <span className="muted">None</span>;
    }
    return (
      <ul className={`field-list depth-${depth}`}>
        {value.map((item, index) => (
          <li key={index}>
            <ExtractedFields value={item} depth={depth + 1} />
          </li>
        ))}
      </ul>
    );
  }

  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    if (typeof record.value !== "undefined" || record.uncertain === true) {
      return <ExtractedValueField value={record} />;
    }
    const entries = Object.entries(record).filter(([key]) => key !== "evidence");
    if (!entries.length) {
      return <span className="muted">None</span>;
    }
    return (
      <dl className={`field-grid depth-${depth}`}>
        {entries.map(([key, item]) => (
          <div key={key} className="field-row">
            <dt>{humanizeKey(key)}</dt>
            <dd>
              <ExtractedFields value={item} depth={depth + 1} />
            </dd>
          </div>
        ))}
      </dl>
    );
  }

  return <span>{String(value)}</span>;
}

function ExtractedValueField({ value }: { value: Record<string, unknown> }) {
  const uncertain = value.uncertain === true;
  return (
    <span className={uncertain ? "extracted-value uncertain" : "extracted-value"}>
      {uncertain ? <AlertTriangle size={14} aria-hidden="true" /> : null}
      <span>{renderScalar(value.value)}</span>
      {uncertain && typeof value.explanation === "string" ? (
        <small>{value.explanation}</small>
      ) : null}
    </span>
  );
}

function renderScalar(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "Uncertain";
  }
  if (Array.isArray(value)) {
    return value.map(renderScalar).join(", ");
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function humanizeKey(value: string): string {
  return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}
