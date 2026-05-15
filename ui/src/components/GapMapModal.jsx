import { useMemo, useState } from "react";
import { API_HOST, runObservabilityGapMap } from "../api";

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
  prometheus: "PR",
  grafana: "GF",
  loki: "LK",
  jaeger: "JG",
  alertmanager: "AM",
  tempo: "TP",
  elasticsearch: "ES",
  dynatrace: "DT",
  datadog: "DD",
  appdynamics: "AD",
  splunk: "SP",
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

  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "";
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
}

function parseServiceList(rawText) {
  const tokens = rawText
    .split(/[\n,]/)
    .map((item) => item.trim().toLowerCase())
    .filter(Boolean);

  return [...new Set(tokens)];
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

export default function GapMapModal({ onClose, validatedTools = [] }) {
  const [applicationName, setApplicationName] = useState("");
  const [environment, setEnvironment] = useState("prod");
  const [serviceText, setServiceText] = useState("");
  const [includeAutoDiscovered, setIncludeAutoDiscovered] = useState(false);

  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState(null);
  const [reportLinks, setReportLinks] = useState(null);
  const [previewFailed, setPreviewFailed] = useState(false);

  const services = useMemo(() => parseServiceList(serviceText), [serviceText]);

  const tools = useMemo(
    () => normalizeValidatedTools(validatedTools),
    [validatedTools]
  );

  const busy = running;

  async function handleRun() {
    if (!applicationName.trim()) {
      setStatus({
        type: "error",
        title: "Validation error",
        msg: "Application name is required.",
      });
      return;
    }

    if (tools.length === 0) {
      setStatus({
        type: "error",
        title: "Validation error",
        msg: "No globally validated tools found. Close this modal and validate at least one tool from Tool Connectivity.",
      });
      return;
    }

    setRunning(true);
    setStatus(null);
    setReportLinks(null);
    setPreviewFailed(false);

    try {
      const payload = {
        application: {
          name: applicationName.trim(),
          environment: environment.trim() || "prod",
          services,
          include_auto_discovered: includeAutoDiscovered,
        },
        client: {
          name: applicationName.trim(),
          environment: environment.trim() || "prod",
        },
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

      const res = await runObservabilityGapMap(payload);

      setReportLinks({
        previewUrl: resolveApiUrl(res.data.preview_url),
        downloadUrl: resolveApiUrl(res.data.download_url),
        jsonUrl: resolveApiUrl(res.data.json_url),
      });

      setStatus({
        type: "success",
        title: "Gap map generated",
        msg: !services.length
          ? "No canonical services provided; report used sanitized auto-discovery mode."
          : res.data.message,
      });
    } catch (err) {
      setReportLinks(null);
      setStatus({
        type: "error",
        title: "Gap map failed",
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
        aria-label="Observability Gap Map"
      >
        <div className="modal-header modal-header-cyan">
          <div className="modal-header-left">
            <span className="modal-icon">GM</span>
            <div>
              <div className="modal-title">Observability Gap Map</div>
              <div className="modal-subtitle">
                Map observability gaps using globally validated observability tools.
              </div>
            </div>
          </div>

          <button className="modal-close" onClick={onClose} aria-label="Close">
            X
          </button>
        </div>

        <div className="modal-body">
          <div className="mtool-add-bar" style={{ gridTemplateColumns: "1fr 1fr" }}>
            <div className="form-group">
              <label className="form-label">Application Name</label>
              <input
                className="form-input"
                value={applicationName}
                onChange={(e) => setApplicationName(e.target.value)}
                placeholder="Astronomy Shop"
                disabled={busy}
              />
            </div>

            <div className="form-group">
              <label className="form-label">Environment</label>
              <select
                className="form-select"
                value={environment}
                onChange={(e) => setEnvironment(e.target.value)}
                disabled={busy}
              >
                <option value="prod">prod</option>
                <option value="staging">staging</option>
                <option value="dev">dev</option>
                <option value="test">test</option>
              </select>
            </div>

            <div className="form-group" style={{ gridColumn: "1 / span 2" }}>
              <label className="form-label">Canonical Service List</label>
              <textarea
                className="form-input"
                rows={5}
                value={serviceText}
                onChange={(e) => setServiceText(e.target.value)}
                placeholder={
                  "frontend\ncart\ncheckout\npayment\nproduct-catalog\nrecommendation\nshipping\ncurrency\nemail\nad\nquote"
                }
                disabled={busy}
              />
              <div className="form-help" style={{ marginTop: 6 }}>
                One service per line, or comma-separated.
              </div>
            </div>

            <div className="form-group" style={{ gridColumn: "1 / span 2" }}>
              <label
                className="checkbox-inline"
                style={{ display: "flex", gap: 8, alignItems: "center" }}
              >
                <input
                  type="checkbox"
                  checked={includeAutoDiscovered}
                  onChange={(e) => setIncludeAutoDiscovered(e.target.checked)}
                  disabled={busy}
                />
                Include sanitized auto-discovered candidates in report rows
              </label>
            </div>
          </div>

          {!services.length && (
            <div
              className="modal-alert modal-alert-warning animate-in"
              style={{ marginBottom: 12 }}
            >
              <span className="modal-alert-icon">!</span>
              <div>
                <div className="modal-alert-title">Discovery mode warning</div>
                <div className="modal-alert-msg">
                  No canonical services provided; report will use sanitized auto-discovery mode.
                </div>
              </div>
            </div>
          )}

          {tools.length > 0 ? (
            <>
              <div className="modal-alert modal-alert-success animate-in">
                <span className="modal-alert-icon">✓</span>
                <div>
                  <div className="modal-alert-title">
                    {tools.length} validated tool{tools.length !== 1 ? "s" : ""} loaded
                  </div>
                  <div className="modal-alert-msg">
                    These connections were validated from the Hub and will be reused by Gap Map.
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
                      <span>{TOOL_ICONS[tool.toolName] ?? "TL"}</span>
                      {tool.toolName}
                    </span>

                    <span className="mtool-url" title={tool.baseUrl}>
                      {tool.baseUrl}
                    </span>

                    <span className="mtool-auth">
                      {tool.authToken ? "*****" : <span className="mtool-none">-</span>}
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
              <span className="empty-icon">GM</span>
              <span className="empty-text">
                No globally validated tools found. Close this modal and validate at least one tool from Tool Connectivity.
              </span>
            </div>
          )}

          {status && (
            <div className={`modal-alert modal-alert-${status.type} animate-in`}>
              <span className="modal-alert-icon">
                {status.type === "success" ? "OK" : "!"}
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
                    Observability Gap Map Preview
                  </div>
                  <div className="report-preview-subtitle">
                    Review the generated interactive report inline, then download HTML/JSON artifacts.
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
                    Gap map generation completed, but HTML preview could not be displayed. Download is still available.
                  </div>
                </div>
              ) : (
                <iframe
                  className="report-preview-frame"
                  title="Observability gap map report"
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
            className="btn btn-cyan btn-lg"
            onClick={handleRun}
            disabled={busy || tools.length === 0 || !applicationName.trim()}
          >
            {running ? (
              <>
                <span className="spinner" /> Generating Gap Map...
              </>
            ) : (
              `Generate Gap Map (${tools.length} tool${tools.length !== 1 ? "s" : ""})`
            )}
          </button>
        </div>
      </div>
    </div>
  );
}