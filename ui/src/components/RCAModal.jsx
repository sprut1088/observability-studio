import { useMemo, useState } from "react";
import { v1Rca, API_HOST } from "../api";

const RCA_SUPPORTED_TOOLS = [
  "prometheus",
  "grafana",
  "jaeger",
  "opensearch",
  "elasticsearch",
  "alertmanager",
  "loki",
];

const TOOL_ICONS = {
  prometheus: "🔥",
  grafana: "📊",
  jaeger: "🔍",
  opensearch: "🔎",
  elasticsearch: "🔎",
  alertmanager: "🔔",
  loki: "📋",
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
    .map((tool) => ({
      toolName: tool.tool_name || tool.toolName || tool.name,
      baseUrl: tool.base_url || tool.baseUrl || tool.url,
      authToken: tool.auth_token || tool.authToken || tool.api_key || null,
      validation: tool.validation_result || tool.validation || { reachable: true },
    }))
    .filter(
      (tool) =>
        tool.toolName &&
        tool.baseUrl &&
        RCA_SUPPORTED_TOOLS.includes(tool.toolName)
    );
}

export default function RCAModal({ onClose, validatedTools = [] }) {
  const tools = useMemo(
    () => normalizeValidatedTools(validatedTools),
    [validatedTools]
  );

  const [service, setService] = useState("");
  const [alertName, setAlertName] = useState("");
  const [description, setDescription] = useState("");
  const [timeWindow, setTimeWindow] = useState(15);

  const [useAI, setUseAI] = useState(false);
  const [aiProvider, setAiProvider] = useState("anthropic");
  const [apiKey, setApiKey] = useState("");
  const [azureEndpoint, setAzureEndpoint] = useState("");
  const [azureDeployment, setAzureDeployment] = useState("");
  const [azureApiVersion, setAzureApiVersion] = useState("");

  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState(null);

  const busy = running;

  async function handleRunRCA() {
    if (tools.length === 0) {
      setStatus({
        type: "error",
        title: "Validation error",
        msg: "No RCA-compatible globally validated tools found. Validate Prometheus, Grafana, Jaeger, OpenSearch, Elasticsearch, Alertmanager, or Loki from Tool Connectivity.",
        stats: [],
        url: null,
      });
      return;
    }

    if (useAI && !apiKey.trim()) {
      setStatus({
        type: "error",
        title: "Missing API key",
        msg: "An API key is required when AI narrative is enabled.",
        stats: [],
        url: null,
      });
      return;
    }

    if (useAI && aiProvider === "azure" && !azureEndpoint.trim()) {
      setStatus({
        type: "error",
        title: "Missing Azure endpoint",
        msg: "Azure OpenAI endpoint URL is required.",
        stats: [],
        url: null,
      });
      return;
    }

    if (useAI && aiProvider === "azure" && !azureDeployment.trim()) {
      setStatus({
        type: "error",
        title: "Missing deployment name",
        msg: "Azure OpenAI deployment name is required.",
        stats: [],
        url: null,
      });
      return;
    }

    setRunning(true);
    setStatus(null);

    try {
      const payload = {
        tools: tools.map((tool) => ({
          tool_name: tool.toolName,
          base_url: tool.baseUrl,
          auth_token: tool.authToken ?? null,
        })),
        incident: {
          service: service.trim() || "all",
          alert_name: alertName.trim() || "Incident Investigation",
          description: description.trim(),
          time_window_minutes: Number(timeWindow),
        },
        ai_provider: useAI ? aiProvider : "anthropic",
        ai_api_key: useAI ? apiKey.trim() : null,
        ai_model: "claude-sonnet-4-6",
        azure_endpoint:
          useAI && aiProvider === "azure" ? azureEndpoint.trim() : null,
        azure_deployment:
          useAI && aiProvider === "azure" ? azureDeployment.trim() : null,
        azure_api_version:
          useAI && aiProvider === "azure" && azureApiVersion.trim()
            ? azureApiVersion.trim()
            : null,
      };

      const res = await v1Rca(payload);
      const data = res.data;

      const statLines = [
        data.anomaly_count != null ? `${data.anomaly_count} anomaly(ies)` : null,
        data.firing_alert_count != null
          ? `${data.firing_alert_count} firing alert(s)`
          : null,
        data.error_log_count != null
          ? `${data.error_log_count} error log(s)`
          : null,
        data.blast_radius != null
          ? `blast radius: ${data.blast_radius} service(s)`
          : null,
      ].filter(Boolean);

      setStatus({
        type: data.success ? "success" : "error",
        title: data.success ? "RCA report ready" : "RCA encountered errors",
        msg: data.message,
        stats: statLines,
        url: data.download_url,
      });

      if (data.download_url) {
        triggerDownload(data.download_url);
      }
    } catch (err) {
      setStatus({
        type: "error",
        title: "RCA failed",
        msg: err?.response?.data?.detail || err.message,
        stats: [],
        url: null,
      });
    } finally {
      setRunning(false);
    }
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
        aria-label="RCA Agent"
      >
        <div className="modal-header modal-header-amber">
          <div className="modal-header-left">
            <span className="modal-icon">🔍</span>
            <div>
              <div className="modal-title">RCA Agent</div>
              <div className="modal-subtitle">
                Reuse globally validated tools, describe the incident, and generate a Root Cause Analysis report.
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
            <span>Validated RCA tools</span>
          </div>

          {tools.length > 0 ? (
            <>
              <div className="modal-alert modal-alert-success animate-in">
                <span className="modal-alert-icon">✓</span>
                <div>
                  <div className="modal-alert-title">
                    {tools.length} RCA-compatible tool{tools.length !== 1 ? "s" : ""} loaded
                  </div>
                  <div className="modal-alert-msg">
                    These connections were validated from the Hub and will be reused by RCA Agent.
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
                No RCA-compatible globally validated tools found. Validate Prometheus, Grafana, Jaeger, OpenSearch, Elasticsearch, Alertmanager, or Loki from Tool Connectivity.
              </span>
            </div>
          )}

          <div className="rca-step-label" style={{ marginTop: "1.25rem" }}>
            <span className="rca-step-num">2</span>
            <span>Describe the incident</span>
          </div>

          <div className="rca-incident-grid">
            <div className="form-group">
              <label className="form-label">Service / Component</label>
              <input
                className="form-input"
                type="text"
                value={service}
                onChange={(e) => setService(e.target.value)}
                placeholder="e.g. PaymentService, checkout-api"
                disabled={busy}
              />
            </div>

            <div className="form-group">
              <label className="form-label">Alert / Issue Name</label>
              <input
                className="form-input"
                type="text"
                value={alertName}
                onChange={(e) => setAlertName(e.target.value)}
                placeholder="e.g. HighLatencyP99"
                disabled={busy}
              />
            </div>

            <div className="form-group">
              <label className="form-label">Look-back Window (minutes)</label>
              <input
                className="form-input"
                type="number"
                min={1}
                max={120}
                value={timeWindow}
                onChange={(e) => setTimeWindow(e.target.value)}
                disabled={busy}
              />
            </div>
          </div>

          <div className="form-group" style={{ marginTop: ".75rem" }}>
            <label className="form-label">Incident Description</label>
            <textarea
              className="form-input"
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Describe the symptoms and context…"
              disabled={busy}
              style={{ resize: "vertical", fontFamily: "inherit" }}
            />
          </div>

          <div className="rca-step-label" style={{ marginTop: "1.25rem" }}>
            <span className="rca-step-num">3</span>
            <span>AI-powered narrative</span>
            <span
              style={{
                marginLeft: "auto",
                fontSize: ".75rem",
                color: "var(--text-muted)",
              }}
            >
              optional
            </span>
          </div>

          <div className="rca-ai-row">
            <label className="toggle-label">
              <span
                className={`toggle-switch ${useAI ? "active" : ""}`}
                onClick={() => !busy && setUseAI((value) => !value)}
                role="switch"
                aria-checked={useAI}
                tabIndex={0}
                onKeyDown={(e) =>
                  e.key === " " && !busy && setUseAI((value) => !value)
                }
              >
                <span className="toggle-thumb" />
              </span>
              <span className="toggle-text">
                Enable AI narrative &amp; recommendations
              </span>
            </label>
          </div>

          {useAI && (
            <div
              className="modal-ai-fields animate-in"
              style={{ marginTop: ".6rem" }}
            >
              <div className="form-group">
                <label className="form-label">AI Provider</label>
                <select
                  className="form-select"
                  value={aiProvider}
                  onChange={(e) => setAiProvider(e.target.value)}
                  disabled={busy}
                >
                  <option value="anthropic">Anthropic (Claude)</option>
                  <option value="azure">Azure OpenAI</option>
                </select>
              </div>

              <div className="form-group">
                <label className="form-label">
                  {aiProvider === "azure" ? "Azure API Key" : "Anthropic API Key"}
                </label>
                <input
                  className="form-input"
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={aiProvider === "azure" ? "Azure API key" : "sk-ant-…"}
                  disabled={busy}
                  autoComplete="off"
                />
              </div>

              {aiProvider === "azure" && (
                <>
                  <div className="form-group">
                    <label className="form-label">Azure Endpoint URL</label>
                    <input
                      className="form-input"
                      type="url"
                      value={azureEndpoint}
                      onChange={(e) => setAzureEndpoint(e.target.value)}
                      placeholder="https://your-resource.openai.azure.com/"
                      disabled={busy}
                    />
                  </div>

                  <div className="form-group">
                    <label className="form-label">Deployment Name</label>
                    <input
                      className="form-input"
                      type="text"
                      value={azureDeployment}
                      onChange={(e) => setAzureDeployment(e.target.value)}
                      placeholder="e.g. gpt-4o"
                      disabled={busy}
                    />
                  </div>

                  <div className="form-group">
                    <label className="form-label">API Version</label>
                    <input
                      className="form-input"
                      type="text"
                      value={azureApiVersion}
                      onChange={(e) => setAzureApiVersion(e.target.value)}
                      placeholder="2024-02-01"
                      disabled={busy}
                    />
                  </div>
                </>
              )}
            </div>
          )}

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

                {status.url && (
                  <button
                    className="btn btn-secondary btn-sm"
                    style={{ marginTop: ".5rem" }}
                    onClick={() => triggerDownload(status.url)}
                  >
                    ⬇ Re-download Report
                  </button>
                )}
              </div>
            </div>
          )}
        </div>

        <div className="modal-footer">
          <button className="btn btn-secondary" onClick={onClose} disabled={busy}>
            Cancel
          </button>

          <button
            className="btn btn-amber"
            onClick={handleRunRCA}
            disabled={busy || tools.length === 0}
          >
            {running ? (
              <>
                <span className="spinner" /> Analysing…
              </>
            ) : (
              `🔍 Run RCA (${tools.length} tool${tools.length !== 1 ? "s" : ""})`
            )}
          </button>
        </div>
      </div>
    </div>
  );
}