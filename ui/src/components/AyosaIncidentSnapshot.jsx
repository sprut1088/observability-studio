/**
 * AyosaIncidentSnapshot — structured incident summary card.
 * Always rendered at the bottom of the chat workspace.
 * Contains runbook generation (moved from the main modal footer).
 */
export default function AyosaIncidentSnapshot({
  snapshot,
  onGenerateRunbook,
  runbookBusy,
  runbook,
  onCopyRunbook,
  onDownloadMarkdown,
  onDownloadText,
}) {
  if (!snapshot) return null;

  const confidencePct = Math.round((snapshot.confidence || 0) * 100);

  return (
    <div className="ayosa-snapshot">
      {/* Header */}
      <div className="ayosa-snapshot-header">
        <span style={{ fontSize: 22, lineHeight: 1 }}>🔵</span>
        <div>
          <div className="ayosa-snapshot-title">Incident Snapshot</div>
          <div className="ayosa-snapshot-meta">Confidence: {confidencePct}%</div>
        </div>
      </div>

      {/* Confidence bar */}
      <div className="ayosa-snapshot-confidence-bar">
        <div style={{ width: `${confidencePct}%` }} />
      </div>

      {/* Root Cause + Impact */}
      <div className="ayosa-snapshot-grid">
        <div className="ayosa-result-card ayosa-result-card-primary">
          <div className="ayosa-result-label">Root Cause</div>
          <p>{snapshot.root_cause || "—"}</p>
        </div>
        <div className="ayosa-result-card">
          <div className="ayosa-result-label">Impact</div>
          <p>{snapshot.impact || "—"}</p>
        </div>
      </div>

      {/* Signal Coverage */}
      {Object.keys(snapshot.coverage || {}).length > 0 && (
        <div className="ayosa-result-card">
          <div className="ayosa-result-label">Coverage</div>
          <div className="ayosa-signal-grid">
            {Object.entries(snapshot.coverage).map(([signal, providers]) => (
              <div
                key={signal}
                className={`ayosa-signal-pill ${providers.length ? "available" : "missing"}`}
              >
                <strong>{signal}</strong>
                <span>{providers.length ? providers.join(", ") : "missing"}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Top Findings */}
      {(snapshot.top_findings || []).length > 0 && (
        <div className="ayosa-result-card">
          <div className="ayosa-result-label">Top Findings</div>
          <ul className="ayosa-action-list">
            {snapshot.top_findings.map((f, i) => (
              <li key={i}>{f}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Recommended Actions */}
      {(snapshot.recommended_actions || []).length > 0 && (
        <div className="ayosa-result-card">
          <div className="ayosa-result-label">Recommended Actions</div>
          <ul className="ayosa-action-list">
            {snapshot.recommended_actions.map((a, i) => (
              <li key={i}>{a}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Timeline Summary */}
      {(snapshot.timeline_summary || []).length > 0 && (
        <div className="ayosa-result-card">
          <div className="ayosa-result-label">Timeline Summary</div>
          <div className="ayosa-timeline">
            {snapshot.timeline_summary.map((item, i) => (
              <div className="ayosa-timeline-item" key={i}>
                <div className="ayosa-timeline-top">
                  <strong>{item.source}</strong>
                  {item.severity && <span>{item.severity}</span>}
                </div>
                {item.timestamp && <small>{item.timestamp}</small>}
                <p>{item.event}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Runbook Generation */}
      <div className="ayosa-snapshot-runbook">
        <button
          className="btn btn-violet"
          onClick={onGenerateRunbook}
          disabled={runbookBusy}
        >
          {runbookBusy ? (
            <>
              <span className="spinner" /> Generating…
            </>
          ) : (
            "📋 Generate Incident Runbook"
          )}
        </button>

        {runbook && (
          <>
            <div className="ayosa-runbook-actions">
              <button className="btn btn-secondary btn-sm" onClick={onCopyRunbook}>
                Copy
              </button>
              <button className="btn btn-secondary btn-sm" onClick={onDownloadMarkdown}>
                Download .md
              </button>
              <button className="btn btn-secondary btn-sm" onClick={onDownloadText}>
                Download .txt
              </button>
            </div>
            <pre className="ayosa-runbook">{runbook}</pre>
          </>
        )}
      </div>
    </div>
  );
}
