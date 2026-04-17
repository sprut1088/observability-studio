import { useState } from "react";
import { v1Assess } from "../api";
import { API_HOST } from "../api";

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

/* ══════════════════════════════════════════════════════════
   AssessModal — ObservaScore tile modal
══════════════════════════════════════════════════════════ */
export default function AssessModal({ onClose }) {
  const [toolSource,   setToolSource]   = useState("prometheus");
  const [apiEndpoint,  setApiEndpoint]  = useState("");
  const [authToken,    setAuthToken]    = useState("");

  const [useAi,        setUseAi]        = useState(false);
  const [aiProvider,   setAiProvider]   = useState("anthropic");
  const [aiApiKey,     setAiApiKey]     = useState("");

  const [loading,      setLoading]      = useState(false);
  const [status,       setStatus]       = useState(null);  // { type, title, msg }

  /* ── Helpers ──────────────────────────────────────────── */
  function setMsg(type, title, msg) {
    setStatus({ type, title, msg });
  }

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

  /* ── Execute Assessment ───────────────────────────────── */
  async function handleAssess() {
    if (!apiEndpoint.trim()) {
      setMsg("error", "Missing endpoint", "API Endpoint is required.");
      return;
    }
    if (useAi && !aiApiKey.trim()) {
      setMsg("error", "Missing API key", "An AI API key is required when AI scoring is enabled.");
      return;
    }

    setLoading(true);
    setStatus(null);

    try {
      const payload = {
        tool_source:  toolSource,
        api_endpoint: apiEndpoint,
        auth_token:   authToken || null,
        use_ai:       useAi,
        ai_provider:  useAi ? aiProvider : null,
        ai_api_key:   useAi ? aiApiKey   : null,
      };
      const res = await v1Assess(payload);
      const { message, download_url } = res.data;
      setMsg("success", "Assessment complete", message);
      triggerDownload(download_url);
    } catch (err) {
      setMsg("error", "Assessment failed", err?.response?.data?.detail || err.message);
    } finally {
      setLoading(false);
    }
  }

  /* ── Render ───────────────────────────────────────────── */
  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="ObservaScore Assessment">

        {/* Header */}
        <div className="modal-header modal-header-indigo">
          <div className="modal-header-left">
            <span className="modal-icon">🎯</span>
            <div>
              <div className="modal-title">ObservaScore</div>
              <div className="modal-subtitle">Observability maturity assessment</div>
            </div>
          </div>
          <button className="modal-close" onClick={onClose} aria-label="Close">✕</button>
        </div>

        {/* Body */}
        <div className="modal-body">

          {/* Tool source */}
          <div className="form-group">
            <label className="form-label">Tool Source</label>
            <select
              className="form-select"
              value={toolSource}
              onChange={(e) => setToolSource(e.target.value)}
              disabled={loading}
            >
              {TOOL_OPTIONS.map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </select>
          </div>

          {/* API endpoint */}
          <div className="form-group">
            <label className="form-label">API Endpoint</label>
            <input
              className="form-input"
              type="url"
              value={apiEndpoint}
              onChange={(e) => setApiEndpoint(e.target.value)}
              placeholder="https://tool.example.com"
              disabled={loading}
            />
          </div>

          {/* Auth token */}
          <div className="form-group">
            <label className="form-label">Auth Token <span className="form-label-opt">(optional)</span></label>
            <input
              className="form-input"
              type="password"
              value={authToken}
              onChange={(e) => setAuthToken(e.target.value)}
              placeholder="Bearer token or API key"
              disabled={loading}
            />
          </div>

          {/* AI toggle */}
          <div
            className={`toggle-row${useAi ? " toggle-row-active" : ""}`}
            onClick={() => !loading && setUseAi((v) => !v)}
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
            <label className="switch" onClick={(e) => e.stopPropagation()}>
              <input
                type="checkbox"
                checked={useAi}
                onChange={(e) => setUseAi(e.target.checked)}
                disabled={loading}
              />
              <span className="switch-track" />
            </label>
          </div>

          {/* AI config (conditional) */}
          {useAi && (
            <div className="modal-ai-fields animate-in">
              <div className="form-grid form-grid-2">
                <div className="form-group">
                  <label className="form-label">AI Provider</label>
                  <select
                    className="form-select"
                    value={aiProvider}
                    onChange={(e) => setAiProvider(e.target.value)}
                    disabled={loading}
                  >
                    {AI_PROVIDERS.map((p) => (
                      <option key={p.value} value={p.value}>{p.label}</option>
                    ))}
                  </select>
                </div>
                <div className="form-group">
                  <label className="form-label">AI API Key</label>
                  <input
                    className="form-input"
                    type="password"
                    value={aiApiKey}
                    onChange={(e) => setAiApiKey(e.target.value)}
                    placeholder="sk-••••••••••••••••••"
                    disabled={loading}
                  />
                </div>
              </div>
            </div>
          )}

          {/* Status alert */}
          {status && (
            <div className={`modal-alert modal-alert-${status.type}`}>
              <span className="modal-alert-icon">
                {status.type === "success" ? "✓" : "✗"}
              </span>
              <div>
                <div className="modal-alert-title">{status.title}</div>
                <div className="modal-alert-msg">{status.msg}</div>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="modal-footer">
          <button className="btn btn-secondary" onClick={onClose} disabled={loading}>
            Cancel
          </button>

          <button
            className="btn btn-primary btn-lg"
            onClick={handleAssess}
            disabled={loading || !apiEndpoint.trim()}
          >
            {loading
              ? <><span className="spinner" /> {useAi ? "Analysing with AI…" : "Scoring…"}</>
              : `▶ Execute Assessment${useAi ? " + AI" : ""}`}
          </button>
        </div>

      </div>
    </div>
  );
}
