/**
 * AyosaEvidenceCard — collapsible evidence item for AYOSA investigation results.
 */
export default function AyosaEvidenceCard({ item }) {
  const dotClass =
    item.status === "ok"
      ? "ayosa-ev-dot ayosa-ev-dot-ok"
      : item.status === "error"
      ? "ayosa-ev-dot ayosa-ev-dot-error"
      : "ayosa-ev-dot ayosa-ev-dot-other";

  return (
    <details className="ayosa-evidence">
      <summary>
        <span className={dotClass} aria-hidden="true" />
        <strong>{item.source}</strong>
        <span className="ayosa-ev-signal"> · {item.signal} · {item.status}</span>
      </summary>
      <p style={{ marginTop: 8, color: "var(--text-secondary)" }}>{item.finding}</p>
      {item.query && <pre className="ayosa-evidence-pre">{item.query}</pre>}
    </details>
  );
}
