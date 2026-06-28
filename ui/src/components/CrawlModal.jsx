import { useMemo, useState } from "react";
import { exportExcel, API_HOST } from "../api";
import ExecutionLoader from "./ExecutionLoader";

const DEFAULT_USAGES = {
  prometheus: ["metrics", "alerts"],
  grafana: ["dashboards", "alerts"],
  loki: ["logs"],
  jaeger: ["traces"],
  tempo: ["traces"],
  elasticsearch: ["logs"],
  dynatrace: ["metrics", "traces", "logs", "dashboards", "alerts"],
  datadog: ["metrics", "traces", "logs", "dashboards", "alerts"],
  appdynamics: ["metrics", "traces", "dashboards", "alerts"],
  splunk: ["logs", "alerts", "dashboards"],
};

const TOOL_ALIASES = {
  opensearch: "elasticsearch",
};

const TOOL_ICONS = {
  prometheus: "🔥",
  grafana: "📊",
  loki: "📋",
  jaeger: "🔍",
  tempo: "⚡",
  elasticsearch: "🔎",
  opensearch: "🔎",
  dynatrace: "🛡️",
  datadog: "🐕",
  appdynamics: "📱",
  splunk: "🌊",
  alertmanager: "🔔",
};

const OBSCRAWL_SUPPORTED_TOOLS = new Set(Object.keys(DEFAULT_USAGES));

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

function normalizeToolName(value) {
  const raw = String(value || "").trim().toLowerCase();
  return TOOL_ALIASES[raw] || raw;
}

function deriveSplunkUrls(inputUrl) {
  try {
    const parsed = new URL(inputUrl);
    const hostname = parsed.hostname;

    return {
      splunkBaseUrl: `http://${hostname}:8000`,
      splunkMgmtUrl: `https://${hostname}:8089`,
      splunkHecUrl: `http://${hostname}:8088`,
      splunkVerifySsl: false,
    };
  } catch {
    return {
      splunkBaseUrl: null,
      splunkMgmtUrl: null,
      splunkHecUrl: null,
      splunkVerifySsl: false,
    };
  }
}

function normalizeValidatedTools(validatedTools = []) {
  return validatedTools
    .map((tool) => {
      const originalToolName = String(
        tool.tool_name || tool.toolName || tool.name || tool.tool || ""
      ).trim().toLowerCase();

      const toolName = normalizeToolName(originalToolName);
      const baseUrl = tool.base_url || tool.baseUrl || tool.url || "";
      const authToken = tool.auth_token || tool.authToken || tool.api_key || null;
      const splunkDerived = toolName === "splunk" ? deriveSplunkUrls(baseUrl) : {};

      if (!toolName || !baseUrl) {
        return null;
      }

      return {
        originalToolName,
        toolName,
        displayName: originalToolName || toolName,
        baseUrl,
        authToken,
        validation: tool.validation_result || tool.validation || { reachable: true },

        splunkBaseUrl:
          tool.splunk_base_url ||
          tool.splunkBaseUrl ||
          splunkDerived.splunkBaseUrl ||
          null,
        splunkMgmtUrl:
          tool.splunk_mgmt_url ||
          tool.splunkMgmtUrl ||
          splunkDerived.splunkMgmtUrl ||
          null,
        splunkHecUrl:
          tool.splunk_hec_url ||
          tool.splunkHecUrl ||
          splunkDerived.splunkHecUrl ||
          null,
        splunkHecToken:
          tool.splunk_hec_token ||
          tool.splunkHecToken ||
          authToken ||
          null,
        splunkVerifySsl:
          tool.splunk_verify_ssl ??
          tool.splunkVerifySsl ??
          splunkDerived.splunkVerifySsl ??
          false,
        supported: OBSCRAWL_SUPPORTED_TOOLS.has(toolName),
      };
    })
    .filter(Boolean);
}

function compactObject(input) {
  return Object.fromEntries(
    Object.entries(input).filter(
      ([, value]) => value !== undefined && value !== null && value !== ""
    )
  );
}

function toExportToolPayload(tool) {
  const payload = {
    name: tool.toolName,
    enabled: true,
    usages: DEFAULT_USAGES[tool.toolName] ?? ["metrics"],
    url: tool.toolName === "splunk" ? tool.splunkMgmtUrl || tool.baseUrl : tool.baseUrl,
  };

  if (tool.authToken) {
    payload.api_key = tool.authToken;
  }

  if (tool.toolName === "splunk") {
    payload.splunk_base_url = tool.splunkBaseUrl || tool.baseUrl;
    payload.splunk_mgmt_url = tool.splunkMgmtUrl;
    payload.splunk_hec_url = tool.splunkHecUrl;
    payload.splunk_hec_token = tool.splunkHecToken || tool.authToken;
    payload.splunk_verify_ssl = tool.splunkVerifySsl ?? false;
  }

  return compactObject(payload);
}

function formatApiError(err) {
  const detail = err?.response?.data?.detail;

  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        const loc = Array.isArray(item.loc) ? item.loc.join(".") : item.loc;
        return `${loc || "request"}: ${item.msg || JSON.stringify(item)}`;
      })
      .join("; ");
  }

  if (typeof detail === "string") {
    return detail;
  }

  if (detail && typeof detail === "object") {
    return JSON.stringify(detail);
  }

  const data = err?.response?.data;
  if (typeof data === "string") {
    return data;
  }

  if (data && typeof data === "object") {
    return data.message || data.error || JSON.stringify(data);
  }

  return err?.message || "ObsCrawl export failed.";
}

export default function CrawlModal({ onClose, validatedTools = [] }) {
  const [crawling, setCrawling] = useState(false);
  const [status, setStatus] = useState(null);

  const tools = useMemo(
    () => normalizeValidatedTools(validatedTools),
    [validatedTools]
  );

  const crawlTools = useMemo(
    () => tools.filter((tool) => tool.supported),
    [tools]
  );

  const skippedTools = useMemo(
    () => tools.filter((tool) => !tool.supported),
    [tools]
  );

  const busy = crawling;

  async function handleCrawl() {
    if (crawlTools.length === 0) {
      setStatus({
        type: "error",
        title: "No crawl-supported tools",
        msg: "ObsCrawl does not have collectors for the currently selected tools.",
      });
      return;
    }

    setCrawling(true);
    setStatus(null);

    try {
      const payload = {
        client: { name: "ObsCrawl Hub", environment: "hub" },
        tools: crawlTools.map(toExportToolPayload),
        ai: { enabled: false },
      };

      const res = await exportExcel(payload);

      setStatus({
        type: "success",
        title: "Report ready",
        msg: res.data.message,
      });

      triggerDownload(res.data.download_url);
    } catch (err) {
      console.error("ObsCrawl export failed", err);
      setStatus({
        type: "error",
        title: "Crawl failed",
        msg: formatApiError(err),
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
        <ExecutionLoader
          running={crawling}
          accelerator="obscrawl"
          mode="deterministic"
          toolCount={crawlTools.length}
          title="Generating ObsCrawl Report"
        />

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
                    {crawlTools.length} tool{crawlTools.length !== 1 ? "s" : ""} can be used by ObsCrawl.
                    {skippedTools.length > 0
                      ? ` Skipping ${skippedTools.map((tool) => tool.displayName).join(", ")} because ObsCrawl has no collector for them.`
                      : " These connections were validated from the Hub and will be reused by ObsCrawl."}
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
                    key={`${tool.displayName}-${tool.baseUrl}`}
                    className="mtool-cols mtool-cols-global mtool-row"
                  >
                    <span className="mtool-num">{index + 1}</span>

                    <span className="mtool-name">
                      <span>{TOOL_ICONS[tool.displayName] ?? TOOL_ICONS[tool.toolName] ?? "🔧"}</span>
                      {tool.displayName}
                      {tool.displayName !== tool.toolName ? ` → ${tool.toolName}` : ""}
                    </span>

                    <span className="mtool-url" title={tool.baseUrl}>
                      {tool.baseUrl}
                    </span>

                    <span className="mtool-auth">
                      {tool.authToken ? "•••••" : <span className="mtool-none">—</span>}
                    </span>

                    <span className="mtool-status">
                      {tool.supported ? (
                        <span className="validation-badge ok">✓ Used</span>
                      ) : (
                        <span className="validation-badge warn">Skipped</span>
                      )}
                    </span>
                  </div>
                ))}

                <div className="mtool-summary-bar">
                  <span>
                    {crawlTools.length} crawl-supported tool{crawlTools.length !== 1 ? "s" : ""} ready
                  </span>
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
            disabled={busy || crawlTools.length === 0}
          >
            {crawling ? (
              <>
                <span className="spinner" /> Generating…
              </>
            ) : (
              `⬇ Generate Report (${crawlTools.length} tool${crawlTools.length !== 1 ? "s" : ""})`
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
