import { useMemo, useState } from "react";
import { exportExcel, API_HOST } from "../api";

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

export default function CrawlModal({ onClose, validatedTools = [] }) {
  const [crawling, setCrawling] = useState(false);
  const [status, setStatus] = useState(null);

  const tools = useMemo(
    () => normalizeValidatedTools(validatedTools),
    [validatedTools]
  );

  const busy = crawling;

  async function handleCrawl() {
    if (tools.length === 0) return;

    setCrawling(true);
    setStatus(null);

    try {
      const payload = {
        client: { name: "ObsCrawl Hub", environment: "hub" },
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
        ai: { enabled: false, provider: null, model: null, api_key: null },
      };

      const res = await exportExcel(payload);

      setStatus({
        type: "success",
        title: "Report ready",
        msg: res.data.message,
      });

      triggerDownload(res.data.download_url);
    } catch (err) {
      setStatus({
        type: "error",
        title: "Crawl failed",
        msg: err?.response?.data?.detail || err.message,
      });
    } finally {
      setCrawling(false);
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
        aria-label="ObsCrawl"
      >
        <div className="modal-header modal-header-teal">
          <div className="modal-header-left">
            <span className="modal-icon">🕷️</span>
            <div>
              <div className="modal-title">ObsCrawl</div>
              <div className="modal-subtitle">
                Generate a telemetry estate workbook using globally validated tools.
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
                    These connections were validated from the Hub and will be reused by ObsCrawl.
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
              <span className="empty-icon">📡</span>
              <span className="empty-text">
                No globally validated tools found. Close this modal and validate at least one tool from Tool Connectivity.
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
        </div>

        <div className="modal-footer">
          <button className="btn btn-secondary" onClick={onClose} disabled={busy}>
            Cancel
          </button>

          <button
            className="btn btn-teal"
            onClick={handleCrawl}
            disabled={busy || tools.length === 0}
          >
            {crawling ? (
              <>
                <span className="spinner" /> Generating…
              </>
            ) : (
              `⬇ Generate Report (${tools.length} tool${tools.length !== 1 ? "s" : ""})`
            )}
          </button>
        </div>
      </div>
    </div>
  );
}