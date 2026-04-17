import { useState, useEffect } from "react";
import { validateTool, exportExcel, runAssessment, API_HOST } from "./api";
import HubPage from "./components/HubPage";
import "./styles.css";

/* ── Tool catalogue ─────────────────────────────────── */
const toolOptions = [
  "prometheus", "grafana", "loki", "jaeger", "alertmanager",
  "tempo", "elasticsearch", "dynatrace", "datadog", "appdynamics", "splunk",
];

const toolMeta = {
  prometheus:     { icon: "🔥", color: "#e6522c" },
  grafana:        { icon: "📊", color: "#f46800" },
  loki:           { icon: "📋", color: "#8ab4f8" },
  jaeger:         { icon: "🔍", color: "#60b4f5" },
  alertmanager:   { icon: "🔔", color: "#e6ac1d" },
  tempo:          { icon: "⚡", color: "#c792ea" },
  elasticsearch:  { icon: "🔎", color: "#00bfb3" },
  dynatrace:      { icon: "🛡️", color: "#6fcfed" },
  datadog:        { icon: "🐕", color: "#632ca6" },
  appdynamics:    { icon: "📱", color: "#4db8ff" },
  splunk:         { icon: "🌊", color: "#59c14a" },
};

/* ── Usage types ────────────────────────────────────── */
const usageOptions = [
  { id: "metrics",    icon: "📈", label: "Metrics"    },
  { id: "traces",     icon: "🔀", label: "Traces"     },
  { id: "logs",       icon: "📝", label: "Logs"       },
  { id: "dashboards", icon: "📊", label: "Dashboards" },
  { id: "alerts",     icon: "🚨", label: "Alerts"     },
];

/* ── LLM options ────────────────────────────────────── */
const llmOptions = [
  { id: "gpt-4o-mini", label: "GPT-4o Mini", icon: "⚡" },
  { id: "gpt-4.1",     label: "GPT-4.1",     icon: "🧠" },
  { id: "claude",      label: "Claude",       icon: "✨" },
];

/* ── Helpers ────────────────────────────────────────── */
function alertType(msg) {
  if (!msg) return "info";
  const l = msg.toLowerCase();
  if (l.includes("fail") || l.includes("error") || l.includes("required")) return "error";
  if (l.includes("success") || l.includes("added") || l.includes("updated") || l.includes("completed") || l.includes("export")) return "success";
  if (l.includes("warn")) return "warning";
  return "info";
}

/* Resolves a root-relative path like /api/download/... to a full URL
   and triggers a browser file download without navigating away. */
function triggerDownload(downloadPath) {
  if (!downloadPath) return;
  const url = downloadPath.startsWith("http")
    ? downloadPath
    : `${API_HOST}${downloadPath}`;
  const a = document.createElement("a");
  a.href = url;
  a.download = "";          // browser derives filename from the URL
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

const alertMeta = {
  success: { icon: "✓", label: "Success" },
  error:   { icon: "✗", label: "Error"   },
  warning: { icon: "⚠", label: "Warning" },
  info:    { icon: "ℹ", label: "Status"  },
};

/* ═══════════════════════════════════════════════════════
   APP
═══════════════════════════════════════════════════════ */
export default function App() {
  const [theme, setTheme] = useState(
    () => localStorage.getItem("observascore-theme") || "light"
  );

  // "hub" = landing tile view  |  "advanced" = full multi-tool form
  const [view, setView] = useState("hub");

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("observascore-theme", theme);
  }, [theme]);

  const toggleTheme = () => setTheme((t) => (t === "light" ? "dark" : "light"));

  const [clientName,        setClientName]        = useState("MVP Client");
  const [environment,       setEnvironment]       = useState("dev");
  const [selectedTool,      setSelectedTool]      = useState("prometheus");
  const [toolUrl,           setToolUrl]           = useState("");
  const [apiKey,            setApiKey]            = useState("");
  const [usages,            setUsages]            = useState(["metrics"]);
  const [tools,             setTools]             = useState([]);
  const [validationResults, setValidationResults] = useState({});
  const [validatingTool,    setValidatingTool]    = useState(null);
  const [aiEnabled,         setAiEnabled]         = useState(false);
  const [selectedLlm,       setSelectedLlm]       = useState("gpt-4o-mini");
  const [aiApiKey,          setAiApiKey]          = useState("");
  const [message,           setMessage]           = useState("");
  const [loading,           setLoading]           = useState(false);

  /* active step for the progress indicator */
  const activeStep = tools.length === 0 ? 1 : !message ? 2 : 3;

  /* ── Handlers ───────────────────────────────────── */
  const toggleUsage = (id) =>
    setUsages((p) => p.includes(id) ? p.filter((u) => u !== id) : [...p, id]);

  const addTool = () => {
    if (!toolUrl.trim())       { setMessage("Tool URL is required.");              return; }
    if (usages.length === 0)   { setMessage("Select at least one usage type.");    return; }

    const t = { name: selectedTool, enabled: true, usages, url: toolUrl, api_key: apiKey || null };
    setTools((p) => [...p.filter((x) => x.name !== selectedTool), t]);
    setMessage(`${selectedTool} added/updated successfully.`);
    setToolUrl(""); setApiKey(""); setUsages(["metrics"]);
  };

  const removeTool = (name) => {
    setTools((p) => p.filter((t) => t.name !== name));
    setValidationResults((p) => { const n = { ...p }; delete n[name]; return n; });
  };

  const validateSingleTool = async (tool) => {
    setValidatingTool(tool.name);
    try {
      const res = await validateTool(tool);
      setValidationResults((p) => ({ ...p, [tool.name]: res.data }));
    } catch (err) {
      const msg = err?.response?.data?.detail || err?.message || "Validation failed";
      setValidationResults((p) => ({ ...p, [tool.name]: { reachable: false, message: msg } }));
    } finally {
      setValidatingTool(null);
    }
  };

  const buildPayload = () => ({
    client: { name: clientName, environment },
    tools,
    ai: {
      enabled: aiEnabled,
      provider:  aiEnabled ? selectedLlm : null,
      model:     aiEnabled ? selectedLlm : null,
      api_key:   aiEnabled ? aiApiKey    : null,
    },
  });

  const handleExport = async () => {
    if (tools.length === 0) { setMessage("Add at least one tool before running crawler."); return; }
    try {
      setLoading(true);
      const res = await exportExcel(buildPayload());
      setMessage(`Export completed: ${res.data.message}${res.data.download_url ? " — " + res.data.download_url : ""}`);
      triggerDownload(res.data.download_url);
    } catch (err) {
      setMessage(err?.response?.data?.detail || "Export failed.");
    } finally { setLoading(false); }
  };

  const handleAssess = async () => {
    if (tools.length === 0)              { setMessage("Add at least one tool before assessment.");               return; }
    if (aiEnabled && !aiApiKey.trim())   { setMessage("AI API key is required when AI evaluation is enabled."); return; }
    try {
      setLoading(true);
      const res = await runAssessment(buildPayload());
      setMessage(`Assessment completed: ${res.data.message}${res.data.download_url ? " — " + res.data.download_url : ""}`);
      triggerDownload(res.data.download_url);
    } catch (err) {
      setMessage(err?.response?.data?.detail || "Assessment failed.");
    } finally { setLoading(false); }
  };

  const msgType = alertType(message);
  const { icon: alertIcon, label: alertLabel } = alertMeta[msgType];

  /* ── Render ─────────────────────────────────────── */
  return (
    <>
      {/* ════════════ HERO ════════════ */}
      <header className="hero">
        <button
          className="theme-toggle"
          onClick={toggleTheme}
          title={theme === "light" ? "Switch to dark mode" : "Switch to light mode"}
        >
          {theme === "light" ? "🌙 Dark" : "☀️ Light"}
        </button>

        <div className="hero-badge">
          <span className="hero-badge-dot" />
          SRE Accelerator
        </div>

        <h1 className="hero-title">Observability Studio</h1>

        <p className="hero-subtitle">
          Assess and score your observability maturity across tools, dimensions,
          and best practices - powered by AI.
        </p>

        {/* View switcher */}
        <div className="view-switcher">
          <button
            className={`view-switch-btn${view === "hub" ? " active" : ""}`}
            onClick={() => setView("hub")}
          >
            🏠 Hub
          </button>
          <button
            className={`view-switch-btn${view === "advanced" ? " active" : ""}`}
            onClick={() => setView("advanced")}
          >
            ⚙️ Advanced
          </button>
        </div>

        <div className="hero-stats">
          <div className="hero-stat">
            <span className="hero-stat-value">35+</span>
            <span className="hero-stat-label">Scoring Rules</span>
          </div>
          <div className="hero-stat">
            <span className="hero-stat-value">10</span>
            <span className="hero-stat-label">Dimensions</span>
          </div>
          <div className="hero-stat">
            <span className="hero-stat-value">11</span>
            <span className="hero-stat-label">Integrations</span>
          </div>
        </div>
      </header>

      <main className="page">

        {/* ════════════ HUB VIEW ════════════ */}
        {view === "hub" && <HubPage />}

        {/* ════════════ ADVANCED VIEW ════════════ */}
        {view === "advanced" && <>

        {/* Step indicator */}
        <div className="steps">
          <Step n={1} label="Client Setup"     activeStep={activeStep} />
          <div className="step-connector" />
          <Step n={2} label="Configure Tools"  activeStep={activeStep} />
          <div className="step-connector" />
          <Step n={3} label="Run Assessment"   activeStep={activeStep} />
        </div>

        {/* ════════════ CARD 1 — CLIENT ════════════ */}
        <section className="card animate-in">
          <div className="card-header">
            <div className="card-icon">👤</div>
            <div>
              <div className="card-title">Client Details</div>
              <div className="card-subtitle">Identify the client and target environment</div>
            </div>
          </div>

          <div className="form-grid form-grid-2">
            <div className="form-group">
              <label className="form-label">Client Name</label>
              <input
                className="form-input"
                type="text"
                value={clientName}
                onChange={(e) => setClientName(e.target.value)}
                placeholder="e.g. Acme Corp"
              />
            </div>
            <div className="form-group">
              <label className="form-label">Environment</label>
              <input
                className="form-input"
                type="text"
                value={environment}
                onChange={(e) => setEnvironment(e.target.value)}
                placeholder="e.g. production"
              />
            </div>
          </div>
        </section>

        {/* ════════════ CARD 2 — TOOLS ════════════ */}
        <section className="card animate-in">
          <div className="card-header">
            <div className="card-icon">🛠️</div>
            <div>
              <div className="card-title">Observability Tools</div>
              <div className="card-subtitle">Register each tool endpoint you want to crawl and analyse</div>
            </div>
          </div>

          {/* Tool config inputs */}
          <div className="form-grid form-grid-3">
            <div className="form-group">
              <label className="form-label">Tool</label>
              <select
                className="form-select"
                value={selectedTool}
                onChange={(e) => setSelectedTool(e.target.value)}
              >
                {toolOptions.map((t) => (
                  <option key={t} value={t}>
                    {toolMeta[t]?.icon} {t}
                  </option>
                ))}
              </select>
            </div>

            <div className="form-group">
              <label className="form-label">Tool URL</label>
              <input
                className="form-input"
                type="text"
                value={toolUrl}
                onChange={(e) => setToolUrl(e.target.value)}
                placeholder="https://tool.example.com"
              />
            </div>

            <div className="form-group">
              <label className="form-label">API Key (optional)</label>
              <input
                className="form-input"
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="••••••••••••"
              />
            </div>
          </div>

          {/* Usage chips */}
          <div className="form-group" style={{ marginBottom: 22 }}>
            <label className="form-label" style={{ marginBottom: 8 }}>Usage Types</label>
            <div className="usage-chips">
              {usageOptions.map(({ id, icon, label }) => (
                <span
                  key={id}
                  className={`usage-chip${usages.includes(id) ? " selected" : ""}`}
                  onClick={() => toggleUsage(id)}
                >
                  {icon} {label}
                </span>
              ))}
            </div>
          </div>

          <button className="btn btn-secondary" onClick={addTool}>
            ＋ Add / Update Tool
          </button>

          {/* Configured tool list */}
          <div className="section-divider">
            <div className="divider-line" />
            <span className="divider-label">Configured Tools ({tools.length})</span>
            <div className="divider-line" />
          </div>

          <div className="tool-list">
            {tools.length === 0 ? (
              <div className="empty-state">
                <span className="empty-icon">📡</span>
                <span className="empty-text">No tools added yet. Configure a tool above to get started.</span>
              </div>
            ) : (
              tools.map((tool) => {
                const result   = validationResults[tool.name];
                const spinning = validatingTool === tool.name;
                const meta     = toolMeta[tool.name] ?? { icon: "🔧" };
                return (
                  <div key={tool.name} className="tool-card animate-in">
                    <div className="tool-icon">{meta.icon}</div>

                    <div className="tool-info">
                      <div className="tool-name">{tool.name}</div>
                      <div className="tool-url">{tool.url}</div>
                      <div className="tool-usages">
                        {tool.usages.map((u) => (
                          <span key={u} className="usage-badge">{u}</span>
                        ))}
                      </div>
                    </div>

                    <div className="tool-actions">
                      {result && (
                        <span className={`validation-badge ${result.reachable ? "ok" : "fail"}`}>
                          {result.reachable ? "✓ Connected" : "✗ Failed"}
                        </span>
                      )}
                      <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => validateSingleTool(tool)}
                        disabled={spinning}
                      >
                        {spinning
                          ? <span className="spinner" style={{ width: 12, height: 12 }} />
                          : "Validate"}
                      </button>
                      <button
                        className="btn btn-danger btn-sm"
                        onClick={() => removeTool(tool.name)}
                        title="Remove tool"
                      >
                        ✕
                      </button>
                    </div>
                  </div>
                );
              })
            )}
          </div>

          {/* Crawler action */}
          <div className="actions-row">
            <button className="btn btn-teal btn-lg" onClick={handleExport} disabled={loading}>
              {loading
                ? <><span className="spinner" /> Running crawler…</>
                : "⬇  Run Crawler & Export Excel"}
            </button>
          </div>
        </section>

        {/* ════════════ CARD 3 — ASSESSMENT ════════════ */}
        <section className="card animate-in">
          <div className="card-header">
            <div className="card-icon">🎯</div>
            <div>
              <div className="card-title">Run Assessment</div>
              <div className="card-subtitle">Score observability maturity with optional AI-powered analysis</div>
            </div>
          </div>

          {/* AI toggle */}
          <div className="toggle-row" onClick={() => setAiEnabled((v) => !v)}>
            <div className="toggle-label">
              <span className="toggle-emoji">🤖</span>
              <div>
                <div className="toggle-title">AI-Powered Evaluation</div>
                <div className="toggle-desc">
                  Use an LLM to provide deeper insights and remediation recommendations
                </div>
              </div>
            </div>
            <label className="switch" onClick={(e) => e.stopPropagation()}>
              <input
                type="checkbox"
                checked={aiEnabled}
                onChange={(e) => setAiEnabled(e.target.checked)}
              />
              <span className="switch-track" />
            </label>
          </div>

          {aiEnabled && (
            <div className="form-grid form-grid-2 animate-in" style={{ marginBottom: 22 }}>
              <div className="form-group">
                <label className="form-label">LLM Provider</label>
                <select
                  className="form-select"
                  value={selectedLlm}
                  onChange={(e) => setSelectedLlm(e.target.value)}
                >
                  {llmOptions.map(({ id, icon, label }) => (
                    <option key={id} value={id}>{icon} {label}</option>
                  ))}
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">LLM API Key</label>
                <input
                  className="form-input"
                  type="password"
                  value={aiApiKey}
                  onChange={(e) => setAiApiKey(e.target.value)}
                  placeholder="sk-••••••••••••••••••••"
                />
              </div>
            </div>
          )}

          <div className="actions-row">
            <button className="btn btn-primary btn-lg" onClick={handleAssess} disabled={loading}>
              {loading
                ? <><span className="spinner" /> Analysing…</>
                : "▶  Run Assessment"}
            </button>
          </div>
        </section>

        {/* ════════════ STATUS MESSAGE ════════════ */}
        {message && (
          <section className="card animate-in">
            <div className={`alert alert-${msgType}`}>
              <span className="alert-icon">{alertIcon}</span>
              <div className="alert-body">
                <div className="alert-title">{alertLabel}</div>
                <div className="alert-msg">{message}</div>
              </div>
            </div>
          </section>
        )}

        </>} {/* end advanced view */}

      </main>

      {/* ════════════ FOOTER ════════════ */}
      <footer className="footer">
        <span className="footer-brand">ObservaScore</span> &nbsp;·&nbsp; SRE Accelerator Platform &nbsp;·&nbsp; v0.2.0
      </footer>
    </>
  );
}

/* ── Step sub-component ───────────────────────────────── */
function Step({ n, label, activeStep }) {
  const done   = activeStep > n;
  const active = activeStep === n;
  return (
    <div className={`step${active ? " active" : ""}${done ? " done" : ""}`}>
      <div className="step-number">{done ? "✓" : n}</div>
      <span className="step-label">{label}</span>
    </div>
  );
}
