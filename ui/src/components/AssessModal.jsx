import { useState } from "react";
import { v1Validate, runAssessment, API_HOST } from "../api";

/* ── Shared constants ───────────────────────────────────── */
const TOOL_OPTIONS = [
  { value: "prometheus",    label: "🔥 Prometheus"    },
  { value: "grafana",       label: "📊 Grafana"       },
  { value: "loki",          label: "📋 Loki"          },
  { value: "jaeger",        label: "🔍 Jaeger"        },
  { value: "alertmanager",  label: "🔔 Alertmanager"  },
  { value: "tempo",         label: "⚡ Tempo"         },
  { value: "elasticsearch", label: "🔎 Elasticsearch" },
  { value: "dynatrace",     label: "🛡️ Dynatrace"    },
  { value: "datadog",       label: "🐕 Datadog"       },
  { value: "appdynamics",   label: "📱 AppDynamics"   },
  { value: "splunk",        label: "🌊 Splunk"        },
];

const AI_PROVIDERS = [
  { value: "anthropic", label: "✨ Anthropic (Claude)" },
  { value: "azure",     label: "🧠 Azure OpenAI"       },
];

const DEFAULT_USAGES = {
  prometheus:    ["metrics", "alerts"],
  grafana:       ["dashboards", "alerts"],
  loki:          ["logs"],
  jaeger:        ["traces"],
  alertmanager:  ["alerts"],
  tempo:         ["traces"],
  elasticsearch: ["logs"],
  dynatrace:     ["metrics", "traces", "logs", "dashboards", "alerts"],
  datadog:       ["metrics", "traces", "logs", "dashboards", "alerts"],
  appdynamics:   ["metrics", "traces", "dashboards", "alerts"],
  splunk:        ["logs", "alerts", "dashboards"],
};

const TOOL_ICONS = {
  prometheus: "🔥", grafana: "📊", loki: "📋",
  jaeger: "🔍", alertmanager: "🔔", tempo: "⚡",
  elasticsearch: "🔎", dynatrace: "🛡️", datadog: "🐕",
  appdynamics: "📱", splunk: "🌊",
};

let _uid = 0;
const nextId = () => ++_uid;

function triggerDownload(downloadPath) {
  if (!downloadPath) return;
  const url = downloadPath.startsWith("http") ? downloadPath : `${API_HOST}${downloadPath}`;
  const a = document.createElement("a");
  a.href = url; a.download = ""; a.style.display = "none";
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
}

function resolveApiUrl(path) {
  if (!path) return null;
  return path.startsWith("http") ? path : `${API_HOST}${path}`;
}

function deriveSplunkUrls(inputUrl) {
  try {
    const parsed = new URL(inputUrl);

    const protocol = parsed.protocol;
    const hostname = parsed.hostname;

    return {
      splunkBaseUrl: `${protocol}//${hostname}:8000`,
      splunkMgmtUrl: `${protocol}//${hostname}:8089`,
      splunkHecUrl: `${protocol}//${hostname}:8088`,
    };
  } catch {
    return null;
  }
}

/* ══════════════════════════════════════════════════════════
   AssessModal — multi-tool ObservaScore assessment modal
══════════════════════════════════════════════════════════ */
export default function AssessModal({ onClose }) {

  /* ── Add-form state ────────────────────────────────────── */
  const [addTool,  setAddTool]  = useState("prometheus");
  const [addUrl,   setAddUrl]   = useState("");
  const [addToken, setAddToken] = useState("");

  const [addUsername, setAddUsername] = useState("");
  const [addPassword, setAddPassword] = useState("");
  const [addSplunkHecToken, setAddSplunkHecToken] = useState("");
  const [addSplunkApp, setAddSplunkApp] = useState("search");

  /* ── Tools table state ─────────────────────────────────── */
  // [{ id, toolName, baseUrl, authToken, validation: null | {reachable, message, latency_ms} }]
  const [tools, setTools] = useState([]);

  /* ── AI config state ───────────────────────────────────── */
  const [useAi,           setUseAi]           = useState(false);
  const [aiProvider,      setAiProvider]      = useState("anthropic");
  const [aiApiKey,        setAiApiKey]        = useState("");
  const [azureEndpoint,   setAzureEndpoint]   = useState("");
  const [azureDeployment, setAzureDeployment] = useState("");

  /* ── Operation state ───────────────────────────────────── */
  const [validatingId,  setValidatingId]  = useState(null);
  const [validatingAll, setValidatingAll] = useState(false);
  const [assessing,     setAssessing]     = useState(false);
  const [status,        setStatus]        = useState(null); // { type, title, msg }
  const [reportLinks,   setReportLinks]   = useState(null); // { previewUrl, downloadUrl, jsonUrl }
  const [previewFailed, setPreviewFailed] = useState(false);

  const busy = validatingId !== null || validatingAll || assessing;

  /* ── Add tool row ──────────────────────────────────────── */
  function handleAdd() {
    if (!addUrl.trim()) return;

    const row = {
      id: nextId(),
      toolName: addTool,
      baseUrl: addUrl.trim(),
      authToken: addToken.trim() || null,
      username: addUsername.trim() || null,
      password: addPassword.trim() || null,
      validation: null,
    };

    if (addTool === "splunk") {
      const derived = deriveSplunkUrls(addUrl.trim());

      if (!derived) {
        setStatus({ type: "error", text: "Invalid Splunk URL" });
        return;
      }

      row.splunkBaseUrl = derived.splunkBaseUrl;
      row.splunkMgmtUrl = derived.splunkMgmtUrl;
      row.splunkHecUrl = derived.splunkHecUrl;
      row.splunkHecToken = addSplunkHecToken.trim() || null;
      row.splunkApp = addSplunkApp.trim() || "search";
      row.splunkVerifySsl = false;
    }

    setTools(prev => [...prev, row]);

    setAddUrl("");
    setAddToken("");
    setAddUsername("");
    setAddPassword("");
    setAddSplunkHecToken("");
    setAddSplunkApp("search");
    setStatus(null);
  }

  /* ── Remove tool row ───────────────────────────────────── */
  function handleRemove(id) {
    setTools(prev => prev.filter(t => t.id !== id));
  }

  /* ── Update a single tool's validation result ──────────── */
  function setToolValidation(id, result) {
    setTools(prev => prev.map(t => t.id === id ? { ...t, validation: result } : t));
  }

  /* ── Validate single tool ──────────────────────────────── */
  async function handleValidate(tool) {
    setValidatingId(tool.id);
    try {
      const res = await v1Validate({
        tool_name: tool.toolName,
        base_url: tool.baseUrl,
        auth_token: tool.authToken,
        username: tool.username,
        password: tool.password,
        splunk_base_url: tool.splunkBaseUrl,
        splunk_mgmt_url: tool.splunkMgmtUrl,
        splunk_hec_url: tool.splunkHecUrl,
        splunk_hec_token: tool.splunkHecToken,
        splunk_app: tool.splunkApp,
        splunk_verify_ssl: tool.splunkVerifySsl,
      });
      setToolValidation(tool.id, res.data);
    } catch (err) {
      setToolValidation(tool.id, {
        reachable: false,
        message: err?.response?.data?.detail || err.message,
      });
    } finally {
      setValidatingId(null);
    }
  }

  /* ── Validate all tools (sequential) ──────────────────── */
  async function handleValidateAll() {
    setValidatingAll(true);
    setStatus(null);
    for (const tool of tools) {
      setValidatingId(tool.id);
      try {
        const res = await v1Validate({
          tool_name: tool.toolName,
          base_url: tool.baseUrl,
          auth_token: tool.authToken,
          username: tool.username,
          password: tool.password,
          splunk_base_url: tool.splunkBaseUrl,
          splunk_mgmt_url: tool.splunkMgmtUrl,
          splunk_hec_url: tool.splunkHecUrl,
          splunk_hec_token: tool.splunkHecToken,
          splunk_app: tool.splunkApp,
          splunk_verify_ssl: tool.splunkVerifySsl,
        });
        setToolValidation(tool.id, res.data);
      } catch (err) {
        setToolValidation(tool.id, {
          reachable: false,
          message: err?.response?.data?.detail || err.message,
        });
      }
    }
    setValidatingId(null);
    setValidatingAll(false);
  }

  /* ── Execute Assessment (all tools → /api/assess) ──────── */
  async function handleAssess() {
    if (tools.length === 0) return;
    if (useAi && !aiApiKey.trim()) {
      setStatus({ type: "error", title: "Missing API key", msg: "An AI API key is required when AI scoring is enabled." });
      return;
    }
    if (useAi && aiProvider === "azure" && !azureEndpoint.trim()) {
      setStatus({ type: "error", title: "Missing Azure endpoint", msg: "Azure OpenAI endpoint URL is required (e.g. https://your-resource.openai.azure.com/)." });
      return;
    }
    if (useAi && aiProvider === "azure" && !azureDeployment.trim()) {
      setStatus({ type: "error", title: "Missing deployment name", msg: "Azure OpenAI deployment name is required (e.g. gpt-4o)." });
      return;
    }
    setAssessing(true);
    setStatus(null);
    setReportLinks(null);
    setPreviewFailed(false);
    try {
      const payload = {
        client: { name: "ObservaScore Hub", environment: "hub" },
        tools: tools.map(t => ({
        name: t.toolName,
        enabled: true,
        usages: DEFAULT_USAGES[t.toolName] ?? ["metrics"],
        url: t.baseUrl,
        api_key: t.authToken ?? null,
        username: t.username ?? null,
        password: t.password ?? null,
        splunk_base_url: t.splunkBaseUrl ?? null,
        splunk_mgmt_url: t.splunkMgmtUrl ?? null,
        splunk_hec_url: t.splunkHecUrl ?? null,
        splunk_hec_token: t.splunkHecToken ?? null,
        splunk_app: t.splunkApp ?? "search",
        splunk_verify_ssl: t.splunkVerifySsl ?? false,
      })),
        ai: {
          enabled:           useAi,
          provider:          useAi ? aiProvider                              : null,
          api_key:           useAi ? aiApiKey                                : null,
          azure_endpoint:    useAi && aiProvider === "azure" ? azureEndpoint    : null,
          azure_deployment:  useAi && aiProvider === "azure" ? azureDeployment  : null,
        },
      };
      const res = await runAssessment(payload);
      setReportLinks({
        previewUrl: resolveApiUrl(res.data.preview_url),
        downloadUrl: resolveApiUrl(res.data.download_url),
        jsonUrl: resolveApiUrl(res.data.json_url),
      });
      setStatus({ type: "success", title: "Assessment complete", msg: res.data.message });
    } catch (err) {
      setReportLinks(null);
      setStatus({ type: "error", title: "Assessment failed", msg: err?.response?.data?.detail || err.message });
    } finally {
      setAssessing(false);
    }
  }

  /* ── Derived ────────────────────────────────────────────── */
  const reachableCount = tools.filter(t => t.validation?.reachable).length;
  const validatedCount = tools.filter(t => t.validation !== null).length;

  /* ── Render ─────────────────────────────────────────────── */
  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal modal-wide" role="dialog" aria-modal="true" aria-label="ObservaScore Assessment">

        {/* ── Header ── */}
        <div className="modal-header modal-header-indigo">
          <div className="modal-header-left">
            <span className="modal-icon">🎯</span>
            <div>
              <div className="modal-title">ObservaScore</div>
              <div className="modal-subtitle">
                Add tools below, validate connections, then execute a combined maturity assessment
              </div>
            </div>
          </div>
          <button className="modal-close" onClick={onClose} aria-label="Close">✕</button>
        </div>

        {/* ── Body ── */}
        <div className="modal-body">

          {/* ── Add-tool bar ── */}
          <div className="mtool-add-bar">
            <div className="form-group mtool-add-tool">
              <label className="form-label">Tool</label>
              <select
                className="form-select"
                value={addTool}
                onChange={e => setAddTool(e.target.value)}
                disabled={busy}
              >
                {TOOL_OPTIONS.map(t => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </select>
            </div>

            <div className="form-group mtool-add-url">
              <label className="form-label">Base URL</label>
              <input
                className="form-input"
                type="url"
                value={addUrl}
                onChange={e => setAddUrl(e.target.value)}
                onKeyDown={e => e.key === "Enter" && handleAdd()}
                placeholder="https://host:port"
                disabled={busy}
              />
            </div>

            <div className="form-group mtool-add-token">
              <label className="form-label">
                Auth Token <span className="form-label-opt">(opt)</span>
              </label>
              <input
                className="form-input"
                type="password"
                value={addToken}
                onChange={e => setAddToken(e.target.value)}
                onKeyDown={e => e.key === "Enter" && handleAdd()}
                placeholder="••••••••"
                disabled={busy}
              />

              {addTool === "splunk" && (
                <>
                  <label>
                    Username
                    <input
                      value={addUsername}
                      onChange={e => setAddUsername(e.target.value)}
                      placeholder="Splunk username"
                      disabled={busy}
                    />
                  </label>

                  <label>
                    Password
                    <input
                      type="password"
                      value={addPassword}
                      onChange={e => setAddPassword(e.target.value)}
                      placeholder="Splunk password"
                      disabled={busy}
                    />
                  </label>

                  <label>
                    Splunk HEC Token
                    <input
                      type="password"
                      value={addSplunkHecToken}
                      onChange={e => setAddSplunkHecToken(e.target.value)}
                      placeholder="HEC token"
                      disabled={busy}
                    />
                  </label>

                  <label>
                    Splunk App
                    <input
                      value={addSplunkApp}
                      onChange={e => setAddSplunkApp(e.target.value)}
                      placeholder="search"
                      disabled={busy}
                    />
                  </label>
                </>
              )}

            </div>

            <button
              className="btn btn-primary mtool-add-btn"
              onClick={handleAdd}
              disabled={busy || !addUrl.trim()}
              title="Add tool to list"
            >
              + Add
            </button>
          </div>

          {/* ── Tools table ── */}
          {tools.length > 0 ? (
            <div className="mtool-table-wrap animate-in">
              {/* Header row */}
              <div className="mtool-cols mtool-header">
                <span>#</span>
                <span>Tool</span>
                <span>URL</span>
                <span>Auth</span>
                <span>Validation</span>
                <span>Actions</span>
              </div>

              {/* Data rows */}
              {tools.map((tool, i) => {
                const isValidating = validatingId === tool.id;
                const v = tool.validation;
                return (
                  <div key={tool.id} className="mtool-cols mtool-row">
                    <span className="mtool-num">{i + 1}</span>

                    <span className="mtool-name">
                      <span>{TOOL_ICONS[tool.toolName] ?? "🔧"}</span>
                      {tool.toolName}
                    </span>

                    <span className="mtool-url" title={tool.baseUrl}>{tool.baseUrl}</span>

                    <span className="mtool-auth">
                      {tool.authToken ? "•••••" : <span className="mtool-none">—</span>}
                    </span>

                    <span className="mtool-status">
                      {isValidating ? (
                        <span className="mtool-validating">
                          <span className="spinner" style={{ width: 12, height: 12, borderTopColor: "var(--accent)" }} />
                          Checking…
                        </span>
                      ) : v ? (
                        <span className={`validation-badge ${v.reachable ? "ok" : "fail"}`}>
                          {v.reachable
                            ? `✓ ${v.latency_ms != null ? v.latency_ms + " ms" : "Connected"}`
                            : "✗ Failed"}
                        </span>
                      ) : (
                        <span className="mtool-pending">Not validated</span>
                      )}
                    </span>

                    <span className="mtool-actions">
                      <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => handleValidate(tool)}
                        disabled={busy}
                        title="Validate this tool"
                      >
                        {isValidating ? "…" : "Validate"}
                      </button>
                      <button
                        className="btn btn-danger btn-sm"
                        onClick={() => handleRemove(tool.id)}
                        disabled={busy}
                        title="Remove"
                      >
                        ✕
                      </button>
                    </span>
                  </div>
                );
              })}

              {/* Summary bar */}
              <div className="mtool-summary-bar">
                <span>{tools.length} tool{tools.length !== 1 ? "s" : ""} added</span>
                {validatedCount > 0 && (
                  <span>
                    {reachableCount}/{validatedCount} validated reachable
                  </span>
                )}
              </div>
            </div>
          ) : (
            <div className="empty-state">
              <span className="empty-icon">🎯</span>
              <span className="empty-text">
                No tools added yet — use the form above to add one or more tools.
              </span>
            </div>
          )}

          {/* ── AI toggle ── */}
          <div
            className={`toggle-row${useAi ? " toggle-row-active" : ""}`}
            onClick={() => !busy && setUseAi(v => !v)}
            style={{ marginTop: 16 }}
          >
            <div className="toggle-label">
              <span className="toggle-emoji">🤖</span>
              <div>
                <div className="toggle-title">Enable AI-Powered Scoring</div>
                <div className="toggle-desc">
                  Enrich results with LLM gap analysis and trend insights
                </div>
              </div>
            </div>
            <label className="switch" onClick={e => e.stopPropagation()}>
              <input
                type="checkbox"
                checked={useAi}
                onChange={e => setUseAi(e.target.checked)}
                disabled={busy}
              />
              <span className="switch-track" />
            </label>
          </div>

          {/* ── AI config (conditional) ── */}
          {useAi && (
            <div className="modal-ai-fields animate-in">
              <div className="form-grid form-grid-2">
                <div className="form-group">
                  <label className="form-label">AI Provider</label>
                  <select
                    className="form-select"
                    value={aiProvider}
                    onChange={e => setAiProvider(e.target.value)}
                    disabled={busy}
                  >
                    {AI_PROVIDERS.map(p => (
                      <option key={p.value} value={p.value}>{p.label}</option>
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
                    onChange={e => setAiApiKey(e.target.value)}
                    placeholder={aiProvider === "azure" ? "Azure OpenAI key" : "sk-••••••••••••••••••"}
                    disabled={busy}
                  />
                </div>
              </div>

              {/* Azure-specific fields */}
              {aiProvider === "azure" && (
                <div className="form-grid form-grid-2 animate-in" style={{ marginTop: 12 }}>
                  <div className="form-group">
                    <label className="form-label">Azure Endpoint URL</label>
                    <input
                      className="form-input"
                      type="url"
                      value={azureEndpoint}
                      onChange={e => setAzureEndpoint(e.target.value)}
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
                      onChange={e => setAzureDeployment(e.target.value)}
                      placeholder="e.g. gpt-4o"
                      disabled={busy}
                    />
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Status alert */}
          {status && (
            <div className={`modal-alert modal-alert-${status.type} animate-in`}>
              <span className="modal-alert-icon">{status.type === "success" ? "✓" : "✗"}</span>
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
                  <div className="report-preview-title">Assessment Report Preview</div>
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
                  <div className="report-preview-fallback-title">Preview unavailable</div>
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

        {/* ── Footer ── */}
        <div className="modal-footer">
          <button className="btn btn-secondary" onClick={onClose} disabled={busy}>
            Cancel
          </button>

          <button
            className="btn btn-secondary"
            onClick={handleValidateAll}
            disabled={busy || tools.length === 0}
            title="Validate all tools sequentially"
          >
            {validatingAll
              ? <><span className="spinner" style={{ borderTopColor: "var(--accent)" }} /> Validating…</>
              : "🔌 Validate All"}
          </button>

          <button
            className="btn btn-primary btn-lg"
            onClick={handleAssess}
            disabled={busy || tools.length === 0}
          >
            {assessing
              ? <><span className="spinner" /> {useAi ? "Analysing with AI…" : "Scoring…"}</>
              : `▶ Execute Assessment${useAi ? " + AI" : ""} (${tools.length} tool${tools.length !== 1 ? "s" : ""})`}
          </button>
        </div>

      </div>
    </div>
  );
}

export function ReportDisplay({ htmlContent }) {
  const [reportPopup, setReportPopup] = useState(null);

  function handleReportDisplay(htmlContent) {
    setReportPopup(
      <div className="modal-overlay">
        <div className="modal">
          <div className="modal-header">
            <h2>Assessment Report</h2>
            <button onClick={() => setReportPopup(null)}>Close</button>
          </div>
          <div className="modal-body">
            <iframe srcDoc={htmlContent} style={{ width: "100%", height: "400px" }} />
          </div>
          <div className="modal-footer">
            <button onClick={() => triggerDownload(htmlContent)}>Download HTML</button>
            <button onClick={() => setReportPopup(null)}>Close</button>
          </div>
        </div>
      </div>
    );
  }

  async function handleAssessment() {
    const result = await runAssessment(tools);
    handleReportDisplay(result.htmlContent);
  }

  return (
    <div>
      {reportPopup}
      <button onClick={handleAssessment}>Run Assessment</button>
    </div>
  );
}
