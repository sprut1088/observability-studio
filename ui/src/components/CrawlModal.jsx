import { useState } from "react";
import { v1Validate, v1Crawl } from "../api";
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

/* ══════════════════════════════════════════════════════════
   CrawlModal — ObsCrawl tile modal
══════════════════════════════════════════════════════════ */
export default function CrawlModal({ onClose }) {
  const [toolName,   setToolName]   = useState("prometheus");
  const [baseUrl,    setBaseUrl]    = useState("");
  const [authToken,  setAuthToken]  = useState("");

  const [validating, setValidating] = useState(false);
  const [validated,  setValidated]  = useState(false);   // gate for Generate Report
  const [crawling,   setCrawling]   = useState(false);

  const [status, setStatus] = useState(null);  // { type, title, msg }

  /* ── Helpers ──────────────────────────────────────────── */
  const loading = validating || crawling;

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

  /* ── Validate Connection ──────────────────────────────── */
  async function handleValidate() {
    if (!baseUrl.trim()) {
      setMsg("error", "Missing URL", "Base URL is required before validating.");
      return;
    }
    setValidating(true);
    setValidated(false);
    setStatus(null);
    try {
      const res = await v1Validate({ tool_name: toolName, base_url: baseUrl, auth_token: authToken || null });
      const { reachable, message, latency_ms } = res.data;
      if (reachable) {
        setValidated(true);
        setMsg(
          "success",
          "Connection established",
          `${message}${latency_ms != null ? ` — ${latency_ms} ms` : ""}`,
        );
      } else {
        setMsg("error", "Unreachable", message);
      }
    } catch (err) {
      setMsg("error", "Validation error", err?.response?.data?.detail || err.message);
    } finally {
      setValidating(false);
    }
  }

  /* ── Generate Report ──────────────────────────────────── */
  async function handleCrawl() {
    setCrawling(true);
    setStatus(null);
    try {
      const res = await v1Crawl({ tool_name: toolName, base_url: baseUrl, auth_token: authToken || null });
      const { message, download_url } = res.data;
      setMsg("success", "Report ready", message);
      triggerDownload(download_url);
    } catch (err) {
      setMsg("error", "Crawl failed", err?.response?.data?.detail || err.message);
    } finally {
      setCrawling(false);
    }
  }

  /* ── Render ───────────────────────────────────────────── */
  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="ObsCrawl">

        {/* Header */}
        <div className="modal-header modal-header-teal">
          <div className="modal-header-left">
            <span className="modal-icon">🕷️</span>
            <div>
              <div className="modal-title">ObsCrawl</div>
              <div className="modal-subtitle">Extract & export tool telemetry</div>
            </div>
          </div>
          <button className="modal-close" onClick={onClose} aria-label="Close">✕</button>
        </div>

        {/* Body */}
        <div className="modal-body">

          {/* Tool selector */}
          <div className="form-group">
            <label className="form-label">Tool Name</label>
            <select
              className="form-select"
              value={toolName}
              onChange={(e) => { setToolName(e.target.value); setValidated(false); setStatus(null); }}
              disabled={loading}
            >
              {TOOL_OPTIONS.map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </select>
          </div>

          {/* Base URL */}
          <div className="form-group">
            <label className="form-label">Base URL</label>
            <input
              className="form-input"
              type="url"
              value={baseUrl}
              onChange={(e) => { setBaseUrl(e.target.value); setValidated(false); setStatus(null); }}
              placeholder="https://prometheus.example.com"
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

        {/* Footer actions */}
        <div className="modal-footer">
          <button className="btn btn-secondary" onClick={onClose} disabled={loading}>
            Cancel
          </button>

          <button
            className="btn btn-teal"
            onClick={handleValidate}
            disabled={loading || !baseUrl.trim()}
          >
            {validating
              ? <><span className="spinner" /> Validating…</>
              : "🔌 Validate Connection"}
          </button>

          <button
            className="btn btn-primary"
            onClick={handleCrawl}
            disabled={!validated || loading}
            title={!validated ? "Validate the connection first" : undefined}
          >
            {crawling
              ? <><span className="spinner" /> Generating…</>
              : "⬇ Generate Report"}
          </button>
        </div>

      </div>
    </div>
  );
}
