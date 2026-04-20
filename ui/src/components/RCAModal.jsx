import { useState } from "react";
import { v1Validate, v1Rca, API_HOST } from "../api";

/* ── Shared constants ───────────────────────────────────── */
const TOOL_OPTIONS = [
  { value: "prometheus",    label: "🔥 Prometheus"     },
  { value: "grafana",       label: "📊 Grafana"        },
  { value: "jaeger",        label: "🔍 Jaeger"         },
  { value: "opensearch",    label: "🔎 OpenSearch"     },
  { value: "elasticsearch", label: "🔎 Elasticsearch"  },
  { value: "alertmanager",  label: "🔔 Alertmanager"   },
  { value: "loki",          label: "📋 Loki"           },
];

const TOOL_ICONS = {
  prometheus: "🔥", grafana: "📊", jaeger: "🔍",
  opensearch: "🔎", elasticsearch: "🔎", alertmanager: "🔔", loki: "📋",
};

const SEVERITY_OPTIONS = ["P1 - Critical", "P2 - High", "P3 - Medium", "P4 - Low"];

let _uid = 0;
const nextId = () => ++_uid;

function triggerDownload(downloadPath) {
  if (!downloadPath) return;
  const url = downloadPath.startsWith("http") ? downloadPath : `${API_HOST}${downloadPath}`;
  const a = document.createElement("a");
  a.href = url; a.download = ""; a.style.display = "none";
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
}

/* ══════════════════════════════════════════════════════════
   RCAModal — multi-tool Root Cause Analysis modal
══════════════════════════════════════════════════════════ */
export default function RCAModal({ onClose }) {

  /* ── Tool add-form state ───────────────────────────────── */
  const [addTool,  setAddTool]  = useState("prometheus");
  const [addUrl,   setAddUrl]   = useState("");
  const [addToken, setAddToken] = useState("");

  /* ── Tools table state ─────────────────────────────────── */
  const [tools, setTools] = useState([]);

  /* ── Incident context ──────────────────────────────────── */
  const [service,     setService]     = useState("");
  const [alertName,   setAlertName]   = useState("");
  const [description, setDescription] = useState("");
  const [timeWindow,  setTimeWindow]  = useState(15);

  /* ── AI config ─────────────────────────────────────────── */
  const [useAI,            setUseAI]            = useState(false);
  const [aiProvider,       setAiProvider]       = useState("anthropic");
  const [apiKey,           setApiKey]           = useState("");
  const [azureEndpoint,    setAzureEndpoint]    = useState("");
  const [azureDeployment,  setAzureDeployment]  = useState("");
  const [azureApiVersion,  setAzureApiVersion]  = useState("");

  /* ── Operation state ───────────────────────────────────── */
  const [validatingId,  setValidatingId]  = useState(null);
  const [validatingAll, setValidatingAll] = useState(false);
  const [running,       setRunning]       = useState(false);
  const [status,        setStatus]        = useState(null); // { type, title, msg, stats }

  const busy = validatingId !== null || validatingAll || running;

  /* ── Add tool ──────────────────────────────────────────── */
  function handleAdd() {
    if (!addUrl.trim()) return;
    setTools(prev => [...prev, {
      id: nextId(),
      toolName: addTool,
      baseUrl:  addUrl.trim(),
      authToken: addToken.trim() || null,
      validation: null,
    }]);
    setAddUrl(""); setAddToken("");
    setStatus(null);
  }

  function handleRemove(id) {
    setTools(prev => prev.filter(t => t.id !== id));
  }

  function setToolValidation(id, result) {
    setTools(prev => prev.map(t => t.id === id ? { ...t, validation: result } : t));
  }

  /* ── Validate single tool ──────────────────────────────── */
  async function handleValidate(tool) {
    setValidatingId(tool.id);
    try {
      const res = await v1Validate({
        tool_name:  tool.toolName,
        base_url:   tool.baseUrl,
        auth_token: tool.authToken,
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

  /* ── Validate all (sequential) ─────────────────────────── */
  async function handleValidateAll() {
    setValidatingAll(true);
    setStatus(null);
    for (const tool of tools) {
      setValidatingId(tool.id);
      try {
        const res = await v1Validate({
          tool_name:  tool.toolName,
          base_url:   tool.baseUrl,
          auth_token: tool.authToken,
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

  /* ── Run RCA ────────────────────────────────────────────── */
  async function handleRunRCA() {
    if (tools.length === 0) return;

    // Client-side validation for AI fields
    if (useAI && !apiKey.trim()) {
      setStatus({ type: "error", title: "Missing API key",
        msg: "An API key is required when AI narrative is enabled." });
      return;
    }
    if (useAI && aiProvider === "azure" && !azureEndpoint.trim()) {
      setStatus({ type: "error", title: "Missing Azure endpoint",
        msg: "Azure OpenAI endpoint URL is required (e.g. https://your-resource.openai.azure.com/)." });
      return;
    }
    if (useAI && aiProvider === "azure" && !azureDeployment.trim()) {
      setStatus({ type: "error", title: "Missing deployment name",
        msg: "Azure OpenAI deployment name is required (e.g. gpt-4o)." });
      return;
    }

    setRunning(true);
    setStatus(null);
    try {
      const payload = {
        tools: tools.map(t => ({
          tool_name:  t.toolName,
          base_url:   t.baseUrl,
          auth_token: t.authToken ?? null,
        })),
        incident: {
          service:             service.trim() || "all",
          alert_name:          alertName.trim() || "Incident Investigation",
          description:         description.trim(),
          time_window_minutes: Number(timeWindow),
        },
        ai_provider:       useAI ? aiProvider : "anthropic",
        ai_api_key:        useAI ? apiKey.trim() : null,
        ai_model:          "claude-sonnet-4-6",
        azure_endpoint:    useAI && aiProvider === "azure" ? azureEndpoint.trim()   : null,
        azure_deployment:  useAI && aiProvider === "azure" ? azureDeployment.trim() : null,
        azure_api_version: useAI && aiProvider === "azure" && azureApiVersion.trim()
                             ? azureApiVersion.trim() : null,
      };

      const res  = await v1Rca(payload);
      const data = res.data;

      const statLines = [
        data.anomaly_count      != null ? `${data.anomaly_count} anomaly(ies)` : null,
        data.firing_alert_count != null ? `${data.firing_alert_count} firing alert(s)` : null,
        data.error_log_count    != null ? `${data.error_log_count} error log(s)` : null,
        data.blast_radius       != null ? `blast radius: ${data.blast_radius} service(s)` : null,
      ].filter(Boolean);

      setStatus({
        type:  data.success ? "success" : "error",
        title: data.success ? "RCA report ready" : "RCA encountered errors",
        msg:   data.message,
        stats: statLines,
        url:   data.download_url,
      });

      if (data.download_url) {
        triggerDownload(data.download_url);
      }
    } catch (err) {
      setStatus({
        type:  "error",
        title: "RCA failed",
        msg:   err?.response?.data?.detail || err.message,
        stats: [],
        url:   null,
      });
    } finally {
      setRunning(false);
    }
  }

  /* ── Derived ────────────────────────────────────────────── */
  const reachableCount = tools.filter(t => t.validation?.reachable).length;
  const validatedCount = tools.filter(t => t.validation !== null).length;

  /* ── Render ─────────────────────────────────────────────── */
  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal modal-wide" role="dialog" aria-modal="true" aria-label="RCA Agent">

        {/* ── Header ── */}
        <div className="modal-header modal-header-amber">
          <div className="modal-header-left">
            <span className="modal-icon">🔍</span>
            <div>
              <div className="modal-title">RCA Agent</div>
              <div className="modal-subtitle">
                Connect your observability tools, describe the incident, and let the agent correlate signals into a full Root Cause Analysis report
              </div>
            </div>
          </div>
          <button className="modal-close" onClick={onClose} aria-label="Close">✕</button>
        </div>

        {/* ── Body ── */}
        <div className="modal-body">

          {/* ── Step 1: Tool endpoints ── */}
          <div className="rca-step-label">
            <span className="rca-step-num">1</span>
            <span>Connect observability tools</span>
          </div>

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
            </div>

            <button
              className="btn btn-amber mtool-add-btn"
              onClick={handleAdd}
              disabled={busy || !addUrl.trim()}
            >
              + Add
            </button>
          </div>

          {/* ── Tools table ── */}
          {tools.length > 0 ? (
            <div className="mtool-table-wrap animate-in">
              <div className="mtool-cols mtool-header">
                <span>#</span><span>Tool</span><span>URL</span>
                <span>Auth</span><span>Validation</span><span>Actions</span>
              </div>

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
                          <span className="spinner" style={{ width: 12, height: 12, borderTopColor: "var(--amber, #f59e0b)" }} />
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
                      >
                        {isValidating ? "…" : "Validate"}
                      </button>
                      <button
                        className="btn btn-danger btn-sm"
                        onClick={() => handleRemove(tool.id)}
                        disabled={busy}
                      >
                        ✕
                      </button>
                    </span>
                  </div>
                );
              })}

              <div className="mtool-summary-bar">
                <span>{tools.length} tool{tools.length !== 1 ? "s" : ""} added</span>
                {validatedCount > 0 && (
                  <span>{reachableCount}/{validatedCount} validated reachable</span>
                )}
              </div>
            </div>
          ) : (
            <div className="empty-state">
              <span className="empty-icon">🔌</span>
              <span className="empty-text">
                Add at least one tool above — Prometheus, Grafana, Jaeger, or OpenSearch.
              </span>
            </div>
          )}

          {/* ── Step 2: Incident context ── */}
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
                onChange={e => setService(e.target.value)}
                placeholder="e.g. PaymentService, checkout-api  (leave blank for all)"
                disabled={busy}
              />
            </div>

            <div className="form-group">
              <label className="form-label">Alert / Issue Name</label>
              <input
                className="form-input"
                type="text"
                value={alertName}
                onChange={e => setAlertName(e.target.value)}
                placeholder="e.g. HighLatencyP99, DatabaseConnectionsExhausted"
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
                onChange={e => setTimeWindow(e.target.value)}
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
              onChange={e => setDescription(e.target.value)}
              placeholder="Describe the symptoms, what you observed, and any context that may help the analysis…"
              disabled={busy}
              style={{ resize: "vertical", fontFamily: "inherit" }}
            />
          </div>

          {/* ── Step 3: AI config ── */}
          <div className="rca-step-label" style={{ marginTop: "1.25rem" }}>
            <span className="rca-step-num">3</span>
            <span>AI-powered narrative</span>
            <span style={{ marginLeft: "auto", fontSize: ".75rem", color: "var(--text-muted)" }}>optional</span>
          </div>

          <div className="rca-ai-row">
            <label className="toggle-label">
              <span className={`toggle-switch ${useAI ? "active" : ""}`}
                    onClick={() => !busy && setUseAI(v => !v)}
                    role="switch"
                    aria-checked={useAI}
                    tabIndex={0}
                    onKeyDown={e => e.key === " " && !busy && setUseAI(v => !v)}>
                <span className="toggle-thumb" />
              </span>
              <span className="toggle-text">
                Enable AI narrative &amp; recommendations
              </span>
            </label>
          </div>

          {useAI && (
            <div className="modal-ai-fields animate-in" style={{ marginTop: ".6rem" }}>

              {/* Provider selector */}
              <div className="form-group">
                <label className="form-label">AI Provider</label>
                <select
                  className="form-select"
                  value={aiProvider}
                  onChange={e => setAiProvider(e.target.value)}
                  disabled={busy}
                >
                  <option value="anthropic">Anthropic (Claude)</option>
                  <option value="azure">Azure OpenAI</option>
                </select>
              </div>

              {/* API Key — label changes per provider */}
              <div className="form-group">
                <label className="form-label">
                  {aiProvider === "azure" ? "Azure API Key" : "Anthropic API Key"}
                  <span className="form-label-opt" style={{ marginLeft: ".4rem" }}>
                    (stored only for this request)
                  </span>
                </label>
                <input
                  className="form-input"
                  type="password"
                  value={apiKey}
                  onChange={e => setApiKey(e.target.value)}
                  placeholder={aiProvider === "azure" ? "Azure API key" : "sk-ant-…"}
                  disabled={busy}
                  autoComplete="off"
                />
              </div>

              {/* Azure-specific fields */}
              {aiProvider === "azure" && (
                <>
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

                  <div className="form-group">
                    <label className="form-label">
                      API Version
                      <span className="form-label-opt" style={{ marginLeft: ".4rem" }}>(opt, default: 2024-02-01)</span>
                    </label>
                    <input
                      className="form-input"
                      type="text"
                      value={azureApiVersion}
                      onChange={e => setAzureApiVersion(e.target.value)}
                      placeholder="2024-02-01"
                      disabled={busy}
                    />
                  </div>
                </>
              )}

            </div>
          )}

          {/* ── Status alert ── */}
          {status && (
            <div className={`modal-alert modal-alert-${status.type} animate-in`} style={{ marginTop: "1rem" }}>
              <span className="modal-alert-icon">{status.type === "success" ? "✓" : "✗"}</span>
              <div style={{ flex: 1 }}>
                <div className="modal-alert-title">{status.title}</div>
                <div className="modal-alert-msg">{status.msg}</div>
                {status.stats && status.stats.length > 0 && (
                  <div style={{ marginTop: ".35rem", display: "flex", flexWrap: "wrap", gap: ".4rem" }}>
                    {status.stats.map((s, i) => (
                      <span key={i} style={{
                        display: "inline-block", padding: ".15rem .55rem",
                        borderRadius: "12px", fontSize: ".72rem", fontWeight: 600,
                        background: "rgba(0,0,0,.07)", color: "inherit",
                      }}>{s}</span>
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

        {/* ── Footer ── */}
        <div className="modal-footer">
          <button className="btn btn-secondary" onClick={onClose} disabled={busy}>
            Cancel
          </button>

          <button
            className="btn btn-secondary"
            onClick={handleValidateAll}
            disabled={busy || tools.length === 0}
          >
            {validatingAll
              ? <><span className="spinner" style={{ borderTopColor: "var(--accent)" }} /> Validating…</>
              : "🔌 Validate All"}
          </button>

          <button
            className="btn btn-amber"
            onClick={handleRunRCA}
            disabled={busy || tools.length === 0}
          >
            {running
              ? <><span className="spinner" /> Analysing…</>
              : `🔍 Run RCA (${tools.length} tool${tools.length !== 1 ? "s" : ""})`}
          </button>
        </div>

      </div>
    </div>
  );
}
