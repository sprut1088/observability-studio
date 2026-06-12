import { useMemo, useState } from "react";
import { v1SloStudio, API_HOST } from "../api";

const SLO_SUPPORTED_TOOLS = [
  "prometheus",
  "alertmanager",
  "grafana",
  "jaeger",
  "tempo",
];

const TOOL_ICONS = {
  prometheus: "🔥",
  alertmanager: "🔔",
  grafana: "📊",
  jaeger: "🔍",
  tempo: "⏱️",
};

function triggerDownload(downloadPath) {
  if (!downloadPath) return;

  const url = downloadPath.startsWith("http")
    ? downloadPath
    : `${API_HOST}${downloadPath}`;

  const a = document.createElement("a");
  a.href = url;
  a.download = "";
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

function normalizeValidatedTools(validatedTools = []) {
  return validatedTools
    .map((tool) => {
      const toolName = (
        tool.tool_name ||
        tool.toolName ||
        tool.name ||
        tool.tool ||
        ""
      ).toLowerCase();

      return {
        toolName,
        baseUrl: tool.base_url || tool.baseUrl || tool.url,
        authToken: tool.auth_token || tool.authToken || tool.api_key || null,
        validation: tool.validation_result || tool.validation || { reachable: true },
      };
    })
    .filter(
      (tool) =>
        tool.toolName &&
        tool.baseUrl &&
        SLO_SUPPORTED_TOOLS.includes(tool.toolName)
    );
}

export default function SLOStudioModal({ onClose, validatedTools = [] }) {
  const tools = useMemo(
    () => normalizeValidatedTools(validatedTools),
    [validatedTools]
  );

  const [service, setService] = useState("");
  const [objective, setObjective] = useState(99.9);
  const [windowDays, setWindowDays] = useState(30);
  const [includeYaml, setIncludeYaml] = useState(true);

  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState(null);

  const busy = running;

  async function handleRunSLOStudio() {
    if (tools.length === 0) {
      setStatus({
        type: "error",
        title: "Validation error",
        msg: "No SLO-compatible globally validated tools found. Validate Prometheus first from Tool Connectivity.",
        stats: [],
        reportUrl: null,
        jsonUrl: null,
        yamlUrl: null,
      });
      return;
    }

    setRunning(true);
    setStatus(null);

    try {
      const payload = {
        service: service.trim() || null,
        environment: null,
        objective: Number(objective),
        window_days: Number(windowDays),
        include_yaml: includeYaml,
        include_ai: false,
        tools: tools.map((tool) => ({
          name: tool.toolName,
          tool: tool.toolName,
          url: tool.baseUrl,
          tool_name: tool.toolName,
          base_url: tool.baseUrl,
          auth_token: tool.authToken ?? null,
        })),
      };

      const res = await v1SloStudio(payload);
      const data = res.data;

      const summary = data.summary || {};
      const statLines = [
        summary.service_count != null ? `${summary.service_count} service(s)` : null,
        summary.existing_slo_count != null ? `${summary.existing_slo_count} existing SLO(s)` : null,
        summary.recommended_slo_count != null ? `${summary.recommended_slo_count} recommended SLO(s)` : null,
        summary.finding_count != null ? `${summary.finding_count} finding(s)` : null,
      ].filter(Boolean);

      setStatus({
        type: data.success ? "success" : "error",
        title: data.success ? "SLO Studio report ready" : "SLO Studio failed",
        msg: data.success
          ? "SLO discovery, coverage analysis, and Sloth YAML generation completed."
          : data.error || "SLO Studio failed.",
        stats: statLines,
        reportUrl: data.report_url || null,
        jsonUrl: data.json_url || null,
        yamlUrl: data.yaml_url || null,
      });
    } catch (err) {
      setStatus({
        type: "error",
        title: "SLO Studio failed",
        msg: err?.response?.data?.detail || err?.response?.data?.error || err.message,
        stats: [],
        reportUrl: null,
        jsonUrl: null,
        yamlUrl: null,
      });
    } finally {
      setRunning(false);
    }
  }

  const reportPreviewUrl = status?.reportUrl
    ? status.reportUrl.startsWith("http")
      ? status.reportUrl
      : `${API_HOST}${status.reportUrl}`
    : null;

  return (
    <div
      className="modal-overlay"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        className="modal modal-wide"
        role="dialog"
        aria-modal="true"
        aria-label="SLO Studio"
      >
        <div className="modal-header modal-header-emerald">
          <div className="modal-header-left">
            <span className="modal-icon">📏</span>
            <div>
              <div className="modal-title">SLO Studio</div>
              <div className="modal-subtitle">
                Discover SLO coverage, recommend objectives, and generate Sloth-compatible rules.
              </div>
            </div>
          </div>

          <button className="modal-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>

        <div className="modal-body">
          <div className="rca-step-label">
            <span className="rca-step-num">1</span>
            <span>Validated SLO tools</span>
          </div>

          {tools.length > 0 ? (
            <>
              <div className="modal-alert modal-alert-success animate-in">
                <span className="modal-alert-icon">✓</span>
                <div>
                  <div className="modal-alert-title">
                    {tools.length} SLO-compatible tool{tools.length !== 1 ? "s" : ""} loaded
                  </div>
                  <div className="modal-alert-msg">
                    These connections were validated from the Hub and will be reused by SLO Studio.
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
              <span className="empty-icon">🔌</span>
              <span className="empty-text">
                No SLO-compatible globally validated tools found. Validate Prometheus from Tool Connectivity.
              </span>
            </div>
          )}

          <div className="rca-step-label" style={{ marginTop: "1.25rem" }}>
            <span className="rca-step-num">2</span>
            <span>SLO generation options</span>
          </div>

          <div className="rca-incident-grid">
            <div className="form-group">
              <label className="form-label">Service / Component</label>
              <input
                className="form-input"
                type="text"
                value={service}
                onChange={(e) => setService(e.target.value)}
                placeholder="Optional, e.g. cart"
                disabled={busy}
              />
            </div>

            <div className="form-group">
              <label className="form-label">Default Objective %</label>
              <input
                className="form-input"
                type="number"
                min={90}
                max={99.999}
                step={0.01}
                value={objective}
                onChange={(e) => setObjective(e.target.value)}
                disabled={busy}
              />
            </div>

            <div className="form-group">
              <label className="form-label">Window Days</label>
              <input
                className="form-input"
                type="number"
                min={1}
                max={90}
                value={windowDays}
                onChange={(e) => setWindowDays(e.target.value)}
                disabled={busy}
              />
            </div>
          </div>

          <div className="rca-ai-row" style={{ marginTop: ".75rem" }}>
            <label className="toggle-label">
              <span
                className={`toggle-switch ${includeYaml ? "active" : ""}`}
                onClick={() => !busy && setIncludeYaml((value) => !value)}
                role="switch"
                aria-checked={includeYaml}
                tabIndex={0}
                onKeyDown={(e) =>
                  e.key === " " && !busy && setIncludeYaml((value) => !value)
                }
              >
                <span className="toggle-thumb" />
              </span>
              <span className="toggle-text">
                Generate Sloth / Prometheus YAML
              </span>
            </label>
          </div>

          {status && (
            <div
              className={`modal-alert modal-alert-${status.type} animate-in`}
              style={{ marginTop: "1rem" }}
            >
              <span className="modal-alert-icon">
                {status.type === "success" ? "✓" : "✗"}
              </span>

              <div style={{ flex: 1 }}>
                <div className="modal-alert-title">{status.title}</div>
                <div className="modal-alert-msg">{status.msg}</div>

                {status.stats && status.stats.length > 0 && (
                  <div
                    style={{
                      marginTop: ".35rem",
                      display: "flex",
                      flexWrap: "wrap",
                      gap: ".4rem",
                    }}
                  >
                    {status.stats.map((item, index) => (
                      <span
                        key={index}
                        style={{
                          display: "inline-block",
                          padding: ".15rem .55rem",
                          borderRadius: "12px",
                          fontSize: ".72rem",
                          fontWeight: 600,
                          background: "rgba(0,0,0,.07)",
                          color: "inherit",
                        }}
                      >
                        {item}
                      </span>
                    ))}
                  </div>
                )}

                <div
                  style={{
                    marginTop: ".5rem",
                    display: "flex",
                    gap: ".5rem",
                    flexWrap: "wrap",
                  }}
                >
                  {status.reportUrl && (
                    <button
                      className="btn btn-secondary btn-sm"
                      onClick={() => triggerDownload(status.reportUrl)}
                    >
                      ⬇ Download Report
                    </button>
                  )}

                  {status.jsonUrl && (
                    <button
                      className="btn btn-secondary btn-sm"
                      onClick={() => triggerDownload(status.jsonUrl)}
                    >
                      ⬇ Download JSON
                    </button>
                  )}

                  {status.yamlUrl && (
                    <button
                      className="btn btn-secondary btn-sm"
                      onClick={() => triggerDownload(status.yamlUrl)}
                    >
                      ⬇ Download YAML
                    </button>
                  )}
                </div>
              </div>
            </div>
          )}

          {reportPreviewUrl && (
            <div className="report-preview-card animate-in">
              <div className="report-preview-header">
                <div>
                  <div className="report-preview-title">
                    SLO Studio Report Preview
                  </div>
                  <div className="report-preview-subtitle">
                    The generated HTML report is rendered inline. Downloads remain available separately.
                  </div>
                </div>
              </div>

              <iframe
                className="report-preview-frame"
                title="SLO Studio report"
                src={reportPreviewUrl}
              />
            </div>
          )}
        </div>

        <div className="modal-footer">
          <button className="btn btn-secondary" onClick={onClose} disabled={busy}>
            Cancel
          </button>

          <button
            className="btn btn-emerald"
            onClick={handleRunSLOStudio}
            disabled={busy || tools.length === 0}
          >
            {running ? (
              <>
                <span className="spinner" /> Analysing SLOs…
              </>
            ) : (
              `📏 Run SLO Studio (${tools.length} tool${tools.length !== 1 ? "s" : ""})`
            )}
          </button>
        </div>
      </div>
    </div>
  );
}