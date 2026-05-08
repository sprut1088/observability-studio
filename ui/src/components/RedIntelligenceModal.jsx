import { useState } from "react";
import { API_HOST, runRedIntelligence, v1Validate } from "../api";

const TOOL_OPTIONS = [
  { value: "grafana", label: "📊 Grafana" },
  { value: "splunk", label: "🌊 Splunk" },
  { value: "datadog", label: "🐕 Datadog" },
  { value: "dynatrace", label: "🛡️ Dynatrace" },
  { value: "appdynamics", label: "📱 AppDynamics" },
  { value: "prometheus", label: "🔥 Prometheus" },
  { value: "loki", label: "📋 Loki" },
  { value: "jaeger", label: "🔍 Jaeger" },
  { value: "alertmanager", label: "🔔 Alertmanager" },
  { value: "tempo", label: "⚡ Tempo" },
  { value: "elasticsearch", label: "🔎 Elasticsearch" },
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

let _uid = 0;
const nextId = () => ++_uid;

function resolveApiUrl(path) {
  if (!path) return null;
  return path.startsWith("http") ? path : `${API_HOST}${path}`;
}

function triggerDownload(downloadPath) {
  if (!downloadPath) return;
  const url = downloadPath.startsWith("http") ? downloadPath : `${API_HOST}${downloadPath}`;
  const a = document.createElement("a");
  a.href = url;
  a.download = "";
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

function deriveSplunkUrls(inputUrl) {
  try {
    const parsed = new URL(inputUrl);
    const hostname = parsed.hostname;

    return {
      splunkBaseUrl: `http://${hostname}:8000`,
      splunkMgmtUrl: `https://${hostname}:8089`,
      splunkHecUrl: `http://${hostname}:8088`,
    };
  } catch {
    return null;
  }
}

export default function RedIntelligenceModal({ onClose }) {
  const [addTool, setAddTool] = useState("grafana");
  const [addUrl, setAddUrl] = useState("");
  const [addToken, setAddToken] = useState("");

  const [tools, setTools] = useState([]);

  const [validatingId, setValidatingId] = useState(null);
  const [validatingAll, setValidatingAll] = useState(false);
  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState(null);
  const [reportLinks, setReportLinks] = useState(null);
  const [previewFailed, setPreviewFailed] = useState(false);

  const busy = validatingId !== null || validatingAll || running;

  function handleAdd() {
    if (!addUrl.trim()) return;

    const row = {
      id: nextId(),
      toolName: addTool,
      baseUrl: addUrl.trim(),
      authToken: addToken.trim() || null,
      validation: null,
    };

    if (addTool === "splunk") {
      const derived = deriveSplunkUrls(addUrl.trim());
      if (!derived) {
        setStatus({ type: "error", title: "Invalid URL", msg: "Invalid Splunk URL" });
        return;
      }
      row.splunkBaseUrl = derived.splunkBaseUrl;
      row.splunkMgmtUrl = derived.splunkMgmtUrl;
      row.splunkHecUrl = derived.splunkHecUrl;
      row.splunkHecToken = addToken.trim() || null;
      row.splunkVerifySsl = false;
    }

    setTools((prev) => [...prev, row]);
    setAddUrl("");
    setAddToken("");
    setStatus(null);
  }

  function handleRemove(id) {
    setTools((prev) => prev.filter((tool) => tool.id !== id));
  }

  function setToolValidation(id, result) {
    setTools((prev) => prev.map((tool) => (tool.id === id ? { ...tool, validation: result } : tool)));
  }

  async function handleValidate(tool) {
    setValidatingId(tool.id);
    try {
      const res = await v1Validate({
        tool_name: tool.toolName,
        base_url: tool.baseUrl,
        auth_token: tool.authToken,
        splunk_base_url: tool.splunkBaseUrl ?? null,
        splunk_mgmt_url: tool.splunkMgmtUrl ?? null,
        splunk_hec_url: tool.splunkHecUrl ?? null,
        splunk_hec_token: tool.splunkHecToken ?? tool.authToken ?? null,
        splunk_verify_ssl: tool.splunkVerifySsl ?? false,
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
          splunk_base_url: tool.splunkBaseUrl ?? null,
          splunk_mgmt_url: tool.splunkMgmtUrl ?? null,
          splunk_hec_url: tool.splunkHecUrl ?? null,
          splunk_hec_token: tool.splunkHecToken ?? tool.authToken ?? null,
          splunk_verify_ssl: tool.splunkVerifySsl ?? false,
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

  async function handleRun() {
    if (tools.length === 0) return;

    setRunning(true);
    setStatus(null);
    setReportLinks(null);
    setPreviewFailed(false);

    try {
      const payload = {
        client: { name: "RED Intelligence Hub", environment: "hub" },
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
          enabled: false,
          provider: null,
          model: null,
          api_key: null,
        },
      };

      const res = await runRedIntelligence(payload);
      setReportLinks({
        previewUrl: resolveApiUrl(res.data.preview_url),
        downloadUrl: resolveApiUrl(res.data.download_url),
        jsonUrl: resolveApiUrl(res.data.json_url),
      });
      setStatus({ type: "success", title: "RED analysis complete", msg: res.data.message });
    } catch (err) {
      setReportLinks(null);
      setStatus({ type: "error", title: "RED analysis failed", msg: err?.response?.data?.detail || err.message });
    } finally {
      setRunning(false);
    }
  }

  const reachableCount = tools.filter((tool) => tool.validation?.reachable).length;
  const validatedCount = tools.filter((tool) => tool.validation !== null).length;

  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal modal-wide" role="dialog" aria-modal="true" aria-label="RED Panel Intelligence">
        <div className="modal-header modal-header-rose">
          <div className="modal-header-left">
            <span className="modal-icon">📉</span>
            <div>
              <div className="modal-title">RED Panel Intelligence</div>
              <div className="modal-subtitle">
                Analyze dashboards for Rate, Errors, and Duration coverage across multiple tools
              </div>
            </div>
          </div>
          <button className="modal-close" onClick={onClose} aria-label="Close">✕</button>
        </div>

        <div className="modal-body">
          <div className="mtool-add-bar">
            <div className="form-group mtool-add-tool">
              <label className="form-label">Tool</label>
              <select className="form-select" value={addTool} onChange={(e) => setAddTool(e.target.value)} disabled={busy}>
                {TOOL_OPTIONS.map((tool) => (
                  <option key={tool.value} value={tool.value}>{tool.label}</option>
                ))}
              </select>
            </div>

            <div className="form-group mtool-add-url">
              <label className="form-label">Base URL</label>
              <input
                className="form-input"
                type="url"
                value={addUrl}
                onChange={(e) => setAddUrl(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleAdd()}
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
                onChange={(e) => setAddToken(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleAdd()}
                placeholder="••••••••"
                disabled={busy}
              />
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

          {tools.length > 0 ? (
            <div className="mtool-table-wrap animate-in">
              <div className="mtool-cols mtool-header">
                <span>#</span>
                <span>Tool</span>
                <span>URL</span>
                <span>Auth</span>
                <span>Validation</span>
                <span>Actions</span>
              </div>

              {tools.map((tool, i) => {
                const isValidating = validatingId === tool.id;
                const validation = tool.validation;
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
                      ) : validation ? (
                        <span className={`validation-badge ${validation.reachable ? "ok" : "fail"}`}>
                          {validation.reachable
                            ? `✓ ${validation.latency_ms != null ? validation.latency_ms + " ms" : "Connected"}`
                            : "✗ Failed"}
                        </span>
                      ) : (
                        <span className="mtool-pending">Not validated</span>
                      )}
                    </span>
                    <span className="mtool-actions">
                      <button className="btn btn-secondary btn-sm" onClick={() => handleValidate(tool)} disabled={busy} title="Validate this tool">
                        {isValidating ? "…" : "Validate"}
                      </button>
                      <button className="btn btn-danger btn-sm" onClick={() => handleRemove(tool.id)} disabled={busy} title="Remove">
                        ✕
                      </button>
                    </span>
                  </div>
                );
              })}

              <div className="mtool-summary-bar">
                <span>{tools.length} tool{tools.length !== 1 ? "s" : ""} added</span>
                {validatedCount > 0 && <span>{reachableCount}/{validatedCount} validated reachable</span>}
              </div>
            </div>
          ) : (
            <div className="empty-state">
              <span className="empty-icon">📉</span>
              <span className="empty-text">No tools added yet. Add one or more dashboard-capable tools above.</span>
            </div>
          )}

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
                  <div className="report-preview-title">RED Panel Intelligence Preview</div>
                  <div className="report-preview-subtitle">
                    Review the generated report inline, then download HTML/JSON artifacts.
                  </div>
                </div>
                <div className="report-preview-actions">
                  {reportLinks.downloadUrl && (
                    <button className="btn btn-secondary btn-sm" onClick={() => triggerDownload(reportLinks.downloadUrl)}>
                      Download Report
                    </button>
                  )}
                  {reportLinks.jsonUrl && (
                    <button className="btn btn-secondary btn-sm" onClick={() => triggerDownload(reportLinks.jsonUrl)}>
                      Download JSON
                    </button>
                  )}
                </div>
              </div>

              {previewFailed || !reportLinks.previewUrl ? (
                <div className="report-preview-fallback">
                  <div className="report-preview-fallback-title">Preview unavailable</div>
                  <div className="report-preview-fallback-msg">
                    RED analysis completed, but HTML preview could not be displayed. Download is still available.
                  </div>
                </div>
              ) : (
                <iframe
                  className="report-preview-frame"
                  title="RED panel intelligence report"
                  src={reportLinks.previewUrl}
                  onError={() => setPreviewFailed(true)}
                />
              )}
            </div>
          )}
        </div>

        <div className="modal-footer">
          <button className="btn btn-secondary" onClick={onClose} disabled={busy}>Cancel</button>

          <button
            className="btn btn-secondary"
            onClick={handleValidateAll}
            disabled={busy || tools.length === 0}
            title="Validate all tools sequentially"
          >
            {validatingAll ? (
              <>
                <span className="spinner" style={{ borderTopColor: "var(--accent)" }} /> Validating…
              </>
            ) : (
              "🔌 Validate All"
            )}
          </button>

          <button className="btn btn-rose btn-lg" onClick={handleRun} disabled={busy || tools.length === 0}>
            {running ? (
              <>
                <span className="spinner" /> Running RED analysis…
              </>
            ) : (
              `▶ Run RED Intelligence (${tools.length} tool${tools.length !== 1 ? "s" : ""})`
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
