import { useMemo, useState } from "react";
import { runAyosaInvestigation, generateAyosaRunbook } from "../api";

const DEFAULT_USAGES = {
  prometheus: ["metrics"],
  grafana: ["dashboards", "alerts"],
  loki: ["logs"],
  jaeger: ["traces"],
  alertmanager: ["alerts"],
  tempo: ["traces"],
  elasticsearch: ["logs"],
  dynatrace: ["metrics", "traces", "logs", "dashboards", "alerts"],
  datadog: ["metrics", "traces", "logs", "dashboards", "alerts"],
  appdynamics: ["metrics", "traces", "dashboards", "alerts"],
  splunk: ["logs", "alerts", "dashboards"],
};

const TOOL_ICONS = {
  prometheus: "🔥",
  grafana: "📊",
  loki: "📋",
  jaeger: "🔍",
  alertmanager: "🔔",
  tempo: "⚡",
  elasticsearch: "🔎",
  dynatrace: "🛡️",
  datadog: "🐕",
  appdynamics: "📱",
  splunk: "🌊",
};

function normalizeValidatedTools(validatedTools = []) {
  return validatedTools
    .map((tool) => ({
      toolName: tool.tool_name || tool.toolName || tool.name || tool.tool,
      baseUrl: tool.base_url || tool.baseUrl || tool.url || tool.endpoint,
      authToken: tool.auth_token || tool.authToken || tool.api_key || tool.token || null,
      validation: tool.validation_result || tool.validation || { reachable: true },
    }))
    .filter((tool) => tool.toolName && tool.baseUrl && DEFAULT_USAGES[tool.toolName]);
}

export default function AYOSAModal({ onClose, validatedTools = [] }) {
  const [message, setMessage] = useState("Investigate checkout latency and errors");
  const [service, setService] = useState("checkout");
  const [timeRange, setTimeRange] = useState("30m");

  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState(null);
  const [result, setResult] = useState(null);

  const [runbook, setRunbook] = useState(null);
  const [runbookBusy, setRunbookBusy] = useState(false);

  const tools = useMemo(
    () => normalizeValidatedTools(validatedTools),
    [validatedTools]
  );

  const busy = running;

  async function handleRun() {
    if (tools.length === 0) {
      setStatus({
        type: "error",
        title: "Validation error",
        msg: "No globally validated tools found. Close this modal and validate at least one tool from Tool Connectivity.",
      });
      return;
    }

    setRunning(true);
    setStatus(null);
    setResult(null);

    try {
      const payload = {
        message: message.trim() || "Investigate service health",
        service: service.trim() || null,
        time_range: timeRange.trim() || "30m",
        tools: tools.map((tool) => ({
          tool: tool.toolName,
          base_url: tool.baseUrl,
          auth_token: tool.authToken ?? null,
        })),
      };

      const res = await runAyosaInvestigation(payload);
      setResult(res.data);
      setRunbook(null);

      setStatus({
        type: "success",
        title: "AYOSA investigation complete",
        msg: `Investigation completed with ${Math.round((res.data.confidence || 0) * 100)}% confidence.`,
      });
    } catch (err) {
      setStatus({
        type: "error",
        title: "AYOSA investigation failed",
        msg: err?.response?.data?.detail || err.message,
      });
    } finally {
      setRunning(false);
    }
  }

  async function handleGenerateRunbook() {
    try {
      setRunbookBusy(true);

      const payload = {
        message,
        service,
        time_range: timeRange,
        tools: tools.map((tool) => ({
          tool: tool.toolName,
          base_url: tool.baseUrl,
          auth_token: tool.authToken ?? null,
        })),
      };

      const response = await generateAyosaRunbook(payload);
      setRunbook(response.data.generated_runbook);
    } catch (err) {
      setStatus({
        type: "error",
        title: "Runbook generation failed",
        msg: err?.response?.data?.detail || err.message,
      });
    } finally {
      setRunbookBusy(false);
    }
  }

  async function handleCopyRunbook() {
    try {
      await navigator.clipboard.writeText(runbook || "");
      setStatus({
        type: "success",
        title: "Runbook copied",
        msg: "AYOSA runbook copied to clipboard.",
      });
    } catch (err) {
      setStatus({
        type: "error",
        title: "Copy failed",
        msg: err.message,
      });
    }
  }

  function downloadRunbook(filename, contentType) {
    if (!runbook) return;

    const blob = new Blob([runbook], { type: contentType });
    const url = URL.createObjectURL(blob);

    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();

    URL.revokeObjectURL(url);
  }

  function handleDownloadMarkdown() {
    downloadRunbook(
      `ayosa-runbook-${service || "incident"}.md`,
      "text/markdown"
    );
  }

  function handleDownloadText() {
    downloadRunbook(
      `ayosa-runbook-${service || "incident"}.txt`,
      "text/plain"
    );
  }

  return (
    <div
      className="modal-overlay"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        className="modal modal-wide"
        role="dialog"
        aria-modal="true"
        aria-label="AYOSA"
      >
        <div className="modal-header modal-header-violet">
          <div className="modal-header-left">
            <span className="modal-icon">🧠</span>
            <div>
              <div className="modal-title">AYOSA</div>
              <div className="modal-subtitle">
                Ask Your Observability Stack Anything using globally validated tools.
              </div>
            </div>
          </div>

          <button className="modal-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>

        <div className="modal-body">
          <div className="mtool-add-bar" style={{ marginBottom: 12 }}>
            <div className="form-group mtool-add-url">
              <label className="form-label">Question</label>
              <input
                className="form-input"
                type="text"
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                placeholder="Investigate checkout latency and errors"
                disabled={busy}
              />
            </div>

            <div className="form-group mtool-add-tool">
              <label className="form-label">Service</label>
              <input
                className="form-input"
                type="text"
                value={service}
                onChange={(e) => setService(e.target.value)}
                placeholder="checkout"
                disabled={busy}
              />
            </div>

            <div className="form-group mtool-add-tool">
              <label className="form-label">Time Range</label>
              <input
                className="form-input"
                type="text"
                value={timeRange}
                onChange={(e) => setTimeRange(e.target.value)}
                placeholder="30m"
                disabled={busy}
              />
            </div>
          </div>

          {tools.length > 0 ? (
            <>
              <div className="modal-alert modal-alert-success animate-in">
                <span className="modal-alert-icon">✓</span>
                <div>
                  <div className="modal-alert-title">
                    {tools.length} validated tool{tools.length !== 1 ? "s" : ""} loaded
                  </div>
                  <div className="modal-alert-msg">
                    AYOSA will query these live observability connections for RCA evidence.
                  </div>
                </div>
              </div>

              <div className="mtool-table-wrap animate-in">
                <div className="mtool-cols mtool-cols-global mtool-header">
                  <span>#</span>
                  <span>Tool</span>
                  <span>URL</span>
                  <span>Auth</span>
                  <span>Status</span>
                </div>

                {tools.map((tool, index) => (
                  <div
                    key={`${tool.toolName}-${tool.baseUrl}`}
                    className="mtool-cols mtool-cols-global mtool-row"
                  >
                    <span className="mtool-num">{index + 1}</span>

                    <span className="mtool-name">
                      <span>{TOOL_ICONS[tool.toolName] ?? "🔧"}</span>
                      {tool.toolName}
                    </span>

                    <span className="mtool-url" title={tool.baseUrl}>
                      {tool.baseUrl}
                    </span>

                    <span className="mtool-auth">
                      {tool.authToken ? "•••••" : <span className="mtool-none">—</span>}
                    </span>

                    <span className="mtool-status">
                      <span className="validation-badge ok">✓ Global</span>
                    </span>
                  </div>
                ))}

                <div className="mtool-summary-bar">
                  <span>{tools.length} tool{tools.length !== 1 ? "s" : ""} ready</span>
                  <span>Source: Hub connectivity</span>
                </div>
              </div>
            </>
          ) : (
            <div className="empty-state">
              <span className="empty-icon">🧠</span>
              <span className="empty-text">
                No globally validated tools found. Close this modal and validate at least one observability tool from Tool Connectivity.
              </span>
            </div>
          )}

          {status && (
            <div className={`modal-alert modal-alert-${status.type} animate-in`}>
              <span className="modal-alert-icon">
                {status.type === "success" ? "✓" : "✗"}
              </span>
              <div>
                <div className="modal-alert-title">{status.title}</div>
                <div className="modal-alert-msg">{status.msg}</div>
              </div>
            </div>
          )}

          {result && (
            <div className="ayosa-result-stack animate-in">
              <div className="report-preview-card">
                <div className="report-preview-header">
                  <div>
                    <div className="report-preview-title">
                      AYOSA Investigation Summary
                    </div>
                    <div className="report-preview-subtitle">
                      Confidence: {Math.round((result.confidence || 0) * 100)}%
                    </div>
                  </div>
                </div>

                <div className="ayosa-result-card">
                  <div className="ayosa-result-label">Signal Coverage</div>

                  <div className="ayosa-signal-grid">
                    {Object.entries(result.signal_coverage || {}).map(([signal, providers]) => (
                      <div
                        key={signal}
                        className={`ayosa-signal-pill ${
                          providers.length ? "available" : "missing"
                        }`}
                      >
                        <strong>{signal}</strong>
                        <span>
                          {providers.length ? providers.join(", ") : "missing"}
                        </span>
                      </div>
                    ))}
                  </div>

                  {(result.missing_signals || []).length > 0 && (
                    <p className="ayosa-missing-note">
                      AYOSA could not query {result.missing_signals.join(", ")} because no matching validated tools were provided.
                    </p>
                  )}
                </div>

                <div className="ayosa-summary-grid">
                  <div className="ayosa-result-card ayosa-result-card-primary">
                    <div className="ayosa-result-label">Probable Root Cause</div>
                    <p>{result.probable_root_cause}</p>
                  </div>

                  <div className="ayosa-result-card">
                    <div className="ayosa-result-label">Impact</div>
                    <p>{result.impact}</p>
                  </div>
                </div>

                <div className="ayosa-result-card">
                  <div className="ayosa-result-label">Executive Summary</div>
                  <p>{result.answer}</p>
                </div>

                <div className="ayosa-result-card">
                  <div className="ayosa-result-label">Detected Patterns</div>
                  <div className="ayosa-pattern-list">
                    {(result.detected_patterns || []).map((pattern) => (
                      <span key={pattern}>{pattern}</span>
                    ))}
                  </div>
                </div>

                <div className="ayosa-result-card">
                  <div className="ayosa-result-label">Timeline</div>
                  <div className="ayosa-timeline">
                    {(result.timeline || []).map((item, index) => (
                      <div className="ayosa-timeline-item" key={`${item.timestamp}-${index}`}>
                        <div className="ayosa-timeline-top">
                          <strong>{item.source}</strong>
                          <span>{item.severity || "event"}</span>
                        </div>
                        <small>{item.timestamp}</small>
                        <p>{item.event}</p>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="ayosa-result-card">
                  <div className="ayosa-result-label">Suggested Actions</div>
                  <ul className="ayosa-action-list">
                    {(result.suggested_actions || []).map((action) => (
                      <li key={action}>{action}</li>
                    ))}
                  </ul>
                </div>

                <div className="ayosa-result-card">
                  <div className="ayosa-result-label">Runbook Generation</div>

                  <button
                    className="btn btn-violet"
                    onClick={handleGenerateRunbook}
                    disabled={runbookBusy}
                  >
                    {runbookBusy ? "Generating..." : "Generate Incident Runbook"}
                  </button>

                  {runbook && (
                    <>
                      <div className="ayosa-runbook-actions">
                        <button
                          className="btn btn-secondary btn-sm"
                          onClick={handleCopyRunbook}
                        >
                          Copy
                        </button>

                        <button
                          className="btn btn-secondary btn-sm"
                          onClick={handleDownloadMarkdown}
                        >
                          Download .md
                        </button>

                        <button
                          className="btn btn-secondary btn-sm"
                          onClick={handleDownloadText}
                        >
                          Download .txt
                        </button>
                      </div>

                      <pre className="ayosa-runbook">
                        {runbook}
                      </pre>
                    </>
                  )}
                </div>

                <div className="ayosa-result-card">
                  <div className="ayosa-result-label">Evidence</div>
                  {(result.evidence || []).map((item, index) => (
                    <details className="ayosa-evidence" key={`${item.source}-${index}`}>
                      <summary>
                        {item.source} · {item.signal} · {item.status}
                      </summary>
                      <p>{item.finding}</p>
                      {item.query && <pre>{item.query}</pre>}
                    </details>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>

        <div className="modal-footer">
          <button className="btn btn-secondary" onClick={onClose} disabled={busy}>
            Cancel
          </button>

          <button
            className="btn btn-violet btn-lg"
            onClick={handleRun}
            disabled={busy || tools.length === 0}
          >
            {running ? (
              <>
                <span className="spinner" /> Running AYOSA…
              </>
            ) : (
              `▶ Run AYOSA Investigation (${tools.length} tool${tools.length !== 1 ? "s" : ""})`
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

