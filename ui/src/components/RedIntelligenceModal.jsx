import { useMemo, useState } from "react";
import { API_HOST, runRedIntelligence } from "../api";

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

function resolveApiUrl(path) {
  if (!path) return null;
  return path.startsWith("http") ? path : `${API_HOST}${path}`;
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

function normalizeValidatedTools(validatedTools = []) {
  return validatedTools
    .map((tool) => ({
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
    }))
    .filter((tool) => tool.toolName && tool.baseUrl && DEFAULT_USAGES[tool.toolName]);
}

export default function RedIntelligenceModal({ onClose, validatedTools = [] }) {
  const [applicationName, setApplicationName] = useState("");
  const [environment, setEnvironment] = useState("prod");
  const [canonicalInput, setCanonicalInput] = useState("");
  const [autoDiscoverServices, setAutoDiscoverServices] = useState(false);

  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState(null);
  const [reportLinks, setReportLinks] = useState(null);
  const [previewFailed, setPreviewFailed] = useState(false);

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

    const canonicalServices = canonicalInput
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);

    setRunning(true);
    setStatus(null);
    setReportLinks(null);
    setPreviewFailed(false);

    try {
      const payload = {
        application_name: applicationName.trim() || "RED Intelligence Hub",
        environment: environment.trim() || "prod",
        canonical_services: canonicalServices,
        auto_discover_services: autoDiscoverServices,
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
      };

      const res = await runRedIntelligence(payload);

      setReportLinks({
        previewUrl: resolveApiUrl(res.data.preview_url),
        downloadUrl: resolveApiUrl(res.data.download_url),
        jsonUrl: resolveApiUrl(res.data.json_url),
      });

      setStatus({
        type: "success",
        title: "RED analysis complete",
        msg: res.data.message,
      });
    } catch (err) {
      setReportLinks(null);
      setStatus({
        type: "error",
        title: "RED analysis failed",
        msg: err?.response?.data?.detail || err.message,
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
        aria-label="RED Panel Intelligence"
      >
        <div className="modal-header modal-header-rose">
          <div className="modal-header-left">
            <span className="modal-icon">📉</span>
            <div>
              <div className="modal-title">RED Panel Intelligence</div>
              <div className="modal-subtitle">
                Measure service-centric RED coverage using globally validated tools.
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
              <label className="form-label">Application Name</label>
              <input
                className="form-input"
                type="text"
                value={applicationName}
                onChange={(e) => setApplicationName(e.target.value)}
                placeholder="payments-platform"
                disabled={busy}
              />
            </div>

            <div className="form-group mtool-add-tool">
              <label className="form-label">Environment</label>
              <input
                className="form-input"
                type="text"
                value={environment}
                onChange={(e) => setEnvironment(e.target.value)}
                placeholder="prod"
                disabled={busy}
              />
            </div>
          </div>

          <div className="form-group" style={{ marginBottom: 12 }}>
            <label className="form-label">
              Canonical Services (comma-separated)
            </label>
            <input
              className="form-input"
              type="text"
              value={canonicalInput}
              onChange={(e) => setCanonicalInput(e.target.value)}
              placeholder="checkout, payment, catalog"
              disabled={busy}
            />
          </div>

          <label
            className="checkbox"
            style={{
              marginBottom: 12,
              display: "inline-flex",
              alignItems: "center",
              gap: 8,
            }}
          >
            <input
              type="checkbox"
              checked={autoDiscoverServices}
              onChange={(e) => setAutoDiscoverServices(e.target.checked)}
              disabled={busy}
            />
            Include auto-discovered services in the coverage scope
          </label>

          {tools.length > 0 ? (
            <>
              <div className="modal-alert modal-alert-success animate-in">
                <span className="modal-alert-icon">✓</span>
                <div>
                  <div className="modal-alert-title">
                    {tools.length} validated tool{tools.length !== 1 ? "s" : ""} loaded
                  </div>
                  <div className="modal-alert-msg">
                    These connections were validated from the Hub and will be reused by RED Intelligence.
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
              <span className="empty-icon">📉</span>
              <span className="empty-text">
                No globally validated tools found. Close this modal and validate at least one dashboard-capable tool from Tool Connectivity.
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

          {reportLinks && (
            <div className="report-preview-card animate-in">
              <div className="report-preview-header">
                <div>
                  <div className="report-preview-title">
                    RED Panel Intelligence Preview
                  </div>
                  <div className="report-preview-subtitle">
                    Review the generated report inline, then download HTML/JSON artifacts.
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
          <button className="btn btn-secondary" onClick={onClose} disabled={busy}>
            Cancel
          </button>

          <button
            className="btn btn-rose btn-lg"
            onClick={handleRun}
            disabled={busy || tools.length === 0}
          >
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