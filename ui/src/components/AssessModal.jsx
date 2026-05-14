import { useMemo, useState } from "react";
import { runAssessment, API_HOST } from "../api";

const AI_PROVIDERS = [
  { value: "anthropic", label: "✨ Anthropic (Claude)" },
  { value: "azure", label: "🧠 Azure OpenAI" },
];

const DEFAULT_USAGES = {
  prometheus: ["metrics", "alerts"],
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

function resolveApiUrl(path) {
  if (!path) return null;
  return path.startsWith("http") ? path : `${API_HOST}${path}`;
}

function normalizeValidatedTools(validatedTools = []) {
  return validatedTools.map((tool) => ({
    toolName: tool.tool_name || tool.toolName || tool.name,
    baseUrl: tool.base_url || tool.baseUrl || tool.url,
    authToken: tool.auth_token || tool.authToken || tool.api_key || null,
    validation: tool.validation_result || tool.validation || { reachable: true },

    splunkBaseUrl: tool.splunk_base_url || tool.splunkBaseUrl || null,
    splunkMgmtUrl: tool.splunk_mgmt_url || tool.splunkMgmtUrl || null,
    splunkHecUrl: tool.splunk_hec_url || tool.splunkHecUrl || null,
    splunkHecToken:
      tool.splunk_hec_token ||
      tool.splunkHecToken ||
      tool.auth_token ||
      tool.authToken ||
      tool.api_key ||
      null,
    splunkVerifySsl:
      tool.splunk_verify_ssl ??
      tool.splunkVerifySsl ??
      false,
  }));
}

export default function AssessModal({ onClose, validatedTools = [] }) {
  const [useAi, setUseAi] = useState(false);
  const [aiProvider, setAiProvider] = useState("anthropic");
  const [aiApiKey, setAiApiKey] = useState("");
  const [azureEndpoint, setAzureEndpoint] = useState("");
  const [azureDeployment, setAzureDeployment] = useState("");

  const [assessing, setAssessing] = useState(false);
  const [status, setStatus] = useState(null);
  const [reportLinks, setReportLinks] = useState(null);
  const [previewFailed, setPreviewFailed] = useState(false);

  const tools = useMemo(
    () => normalizeValidatedTools(validatedTools),
    [validatedTools]
  );

  const busy = assessing;

  async function handleAssess() {
    if (tools.length === 0) return;

    if (useAi && !aiApiKey.trim()) {
      setStatus({
        type: "error",
        title: "Missing API key",
        msg: "An AI API key is required when AI scoring is enabled.",
      });
      return;
    }

    if (useAi && aiProvider === "azure" && !azureEndpoint.trim()) {
      setStatus({
        type: "error",
        title: "Missing Azure endpoint",
        msg: "Azure OpenAI endpoint URL is required.",
      });
      return;
    }

    if (useAi && aiProvider === "azure" && !azureDeployment.trim()) {
      setStatus({
        type: "error",
        title: "Missing deployment name",
        msg: "Azure OpenAI deployment name is required.",
      });
      return;
    }

    setAssessing(true);
    setStatus(null);
    setReportLinks(null);
    setPreviewFailed(false);

    try {
      const payload = {
        client: { name: "ObservaScore Hub", environment: "hub" },
        tools: tools.map((tool) => ({
          name: tool.toolName,
          enabled: true,
          usages: DEFAULT_USAGES[tool.toolName] ?? ["metrics"],
          url: tool.baseUrl,
          api_key: tool.authToken ?? null,
          splunk_base_url: tool.splunkBaseUrl ?? null,
          splunk_mgmt_url: tool.splunkMgmtUrl ?? null,
          splunk_hec_url: tool.splunkHecUrl ?? null,
          splunk_hec_token: tool.splunkHecToken ?? tool.authToken ?? null,
          splunk_verify_ssl: tool.splunkVerifySsl ?? false,
        })),
        ai: {
          enabled: useAi,
          provider: useAi ? aiProvider : null,
          api_key: useAi ? aiApiKey : null,
          azure_endpoint:
            useAi && aiProvider === "azure" ? azureEndpoint : null,
          azure_deployment:
            useAi && aiProvider === "azure" ? azureDeployment : null,
        },
      };

      const res = await runAssessment(payload);

      setReportLinks({
        previewUrl: resolveApiUrl(res.data.preview_url),
        downloadUrl: resolveApiUrl(res.data.download_url),
        jsonUrl: resolveApiUrl(res.data.json_url),
      });

      setStatus({
        type: "success",
        title: "Assessment complete",
        msg: res.data.message,
      });
    } catch (err) {
      setReportLinks(null);
      setStatus({
        type: "error",
        title: "Assessment failed",
        msg: err?.response?.data?.detail || err.message,
      });
    } finally {
      setAssessing(false);
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
        aria-label="ObservaScore Assessment"
      >
        <div className="modal-header modal-header-indigo">
          <div className="modal-header-left">
            <span className="modal-icon">🎯</span>
            <div>
              <div className="modal-title">ObservaScore</div>
              <div className="modal-subtitle">
                Run maturity assessment using globally validated observability tools.
              </div>
            </div>
          </div>

          <button className="modal-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>

        <div className="modal-body">
          {tools.length > 0 ? (
            <>
              <div className="modal-alert modal-alert-success animate-in">
                <span className="modal-alert-icon">✓</span>
                <div>
                  <div className="modal-alert-title">
                    {tools.length} validated tool{tools.length !== 1 ? "s" : ""} loaded
                  </div>
                  <div className="modal-alert-msg">
                    These connections were validated from the Hub and will be reused by ObservaScore.
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
              <span className="empty-icon">🎯</span>
              <span className="empty-text">
                No globally validated tools found. Close this modal and validate at least one tool from Tool Connectivity.
              </span>
            </div>
          )}

          <div
            className={`toggle-row${useAi ? " toggle-row-active" : ""}`}
            onClick={() => !busy && setUseAi((value) => !value)}
            style={{ marginTop: 16 }}
          >
            <div className="toggle-label">
              <span className="toggle-emoji">🤖</span>
              <div>
                <div className="toggle-title">Enable AI-Powered Scoring</div>
                <div className="toggle-desc">
                  Enrich results with LLM gap analysis and trend insights.
                </div>
              </div>
            </div>

            <label className="switch" onClick={(e) => e.stopPropagation()}>
              <input
                type="checkbox"
                checked={useAi}
                onChange={(e) => setUseAi(e.target.checked)}
                disabled={busy}
              />
              <span className="switch-track" />
            </label>
          </div>

          {useAi && (
            <div className="modal-ai-fields animate-in">
              <div className="form-grid form-grid-2">
                <div className="form-group">
                  <label className="form-label">AI Provider</label>
                  <select
                    className="form-select"
                    value={aiProvider}
                    onChange={(e) => setAiProvider(e.target.value)}
                    disabled={busy}
                  >
                    {AI_PROVIDERS.map((provider) => (
                      <option key={provider.value} value={provider.value}>
                        {provider.label}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="form-group">
                  <label className="form-label">
                    {aiProvider === "azure" ? "Azure API Key" : "API Key"}
                  </label>
                  <input
                    className="form-input"
                    type="password"
                    value={aiApiKey}
                    onChange={(e) => setAiApiKey(e.target.value)}
                    placeholder={
                      aiProvider === "azure"
                        ? "Azure OpenAI key"
                        : "sk-••••••••••••••••••"
                    }
                    disabled={busy}
                  />
                </div>
              </div>

              {aiProvider === "azure" && (
                <div
                  className="form-grid form-grid-2 animate-in"
                  style={{ marginTop: 12 }}
                >
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
                </div>
              )}
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

          {reportLinks && (
            <div className="report-preview-card animate-in">
              <div className="report-preview-header">
                <div>
                  <div className="report-preview-title">
                    Assessment Report Preview
                  </div>
                  <div className="report-preview-subtitle">
                    The generated HTML report is rendered inline. Download remains available separately.
                  </div>
                </div>

                <div className="report-preview-actions">
                  {reportLinks.downloadUrl && (
                    <button
                      className="btn btn-secondary btn-sm"
                      onClick={() => triggerDownload(reportLinks.downloadUrl)}
                    >
                      Download Report
                    </button>
                  )}

                  {reportLinks.jsonUrl && (
                    <button
                      className="btn btn-secondary btn-sm"
                      onClick={() => triggerDownload(reportLinks.jsonUrl)}
                    >
                      Download JSON
                    </button>
                  )}
                </div>
              </div>

              {previewFailed || !reportLinks.previewUrl ? (
                <div className="report-preview-fallback">
                  <div className="report-preview-fallback-title">
                    Preview unavailable
                  </div>
                  <div className="report-preview-fallback-msg">
                    The assessment finished, but the HTML preview could not be displayed in the UI. You can still download the generated report.
                  </div>
                </div>
              ) : (
                <iframe
                  className="report-preview-frame"
                  title="ObservaScore assessment report"
                  src={reportLinks.previewUrl}
                  onError={() => setPreviewFailed(true)}
                />
              )}
            </div>
          )}
        </div>

        <div className="modal-footer">
          <button className="btn btn-secondary" onClick={onClose} disabled={busy}>
            Cancel
          </button>

          <button
            className="btn btn-primary btn-lg"
            onClick={handleAssess}
            disabled={busy || tools.length === 0}
          >
            {assessing ? (
              <>
                <span className="spinner" />
                {useAi ? "Analysing with AI…" : "Scoring…"}
              </>
            ) : (
              `▶ Execute Assessment${useAi ? " + AI" : ""} (${tools.length} tool${
                tools.length !== 1 ? "s" : ""
              })`
            )}
          </button>
        </div>
      </div>
    </div>
  );
}