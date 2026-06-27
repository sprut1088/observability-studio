const LEGACY_TOOL_ALIASES = {
  opensearch: "elasticsearch",
  elastic: "elasticsearch",
};

export const DEFAULT_TOOL_USAGES = {
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

export const TOOL_ICONS = {
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

function cleanObject(obj) {
  return Object.fromEntries(
    Object.entries(obj).filter(
      ([, value]) => value !== undefined && value !== null && value !== ""
    )
  );
}

function normalizeName(tool) {
  const rawName = String(
    tool?.tool_name || tool?.toolName || tool?.name || tool?.tool || ""
  )
    .trim()
    .toLowerCase();

  return LEGACY_TOOL_ALIASES[rawName] || rawName;
}

function normalizeBaseUrl(tool) {
  return String(tool?.base_url || tool?.baseUrl || tool?.url || "").trim();
}

function deriveSplunkUrls(baseUrl) {
  try {
    const parsed = new URL(baseUrl);
    const hostname = parsed.hostname;

    if (!hostname) {
      return {
        base: baseUrl.replace(/\/$/, ""),
        mgmt: baseUrl.replace(/\/$/, ""),
        hec: baseUrl.replace(/\/$/, ""),
      };
    }

    return {
      base: `http://${hostname}:8000`,
      mgmt: `https://${hostname}:8089`,
      hec: `http://${hostname}:8088`,
    };
  } catch {
    return {
      base: baseUrl || "",
      mgmt: baseUrl || "",
      hec: baseUrl || "",
    };
  }
}

export function normalizeLegacyTools(validatedTools = []) {
  return (validatedTools || [])
    .map((tool) => {
      const toolName = normalizeName(tool);
      const baseUrl = normalizeBaseUrl(tool);
      const authToken =
        tool?.auth_token || tool?.authToken || tool?.api_key || tool?.token || null;
      const splunkUrls = toolName === "splunk" ? deriveSplunkUrls(baseUrl) : {};

      return {
        originalToolName: tool?.tool_name || tool?.toolName || tool?.name || toolName,
        toolName,
        baseUrl,
        authToken,
        validation: tool?.validation_result || tool?.validation || { reachable: true },
        splunkBaseUrl:
          tool?.splunk_base_url ||
          tool?.splunkBaseUrl ||
          splunkUrls.base ||
          null,
        splunkMgmtUrl:
          tool?.splunk_mgmt_url ||
          tool?.splunkMgmtUrl ||
          splunkUrls.mgmt ||
          null,
        splunkHecUrl:
          tool?.splunk_hec_url ||
          tool?.splunkHecUrl ||
          splunkUrls.hec ||
          null,
        splunkHecToken:
          tool?.splunk_hec_token ||
          tool?.splunkHecToken ||
          authToken ||
          null,
        splunkVerifySsl: tool?.splunk_verify_ssl ?? tool?.splunkVerifySsl ?? false,
      };
    })
    .filter((tool) => tool.toolName && tool.baseUrl && DEFAULT_TOOL_USAGES[tool.toolName]);
}

export function toLegacyToolPayload(tool) {
  return cleanObject({
    name: tool.toolName,
    enabled: true,
    usages: DEFAULT_TOOL_USAGES[tool.toolName] ?? ["metrics"],
    url: tool.baseUrl,
    api_key: tool.authToken,
    splunk_base_url: tool.toolName === "splunk" ? tool.splunkBaseUrl : undefined,
    splunk_mgmt_url: tool.toolName === "splunk" ? tool.splunkMgmtUrl : undefined,
    splunk_hec_url: tool.toolName === "splunk" ? tool.splunkHecUrl : undefined,
    splunk_hec_token:
      tool.toolName === "splunk" ? tool.splunkHecToken || tool.authToken : undefined,
    splunk_verify_ssl: tool.toolName === "splunk" ? tool.splunkVerifySsl ?? false : undefined,
  });
}

export function buildLegacyToolsPayload(tools = []) {
  return (tools || []).map(toLegacyToolPayload);
}

export function formatApiError(err, fallback = "Request failed.") {
  const detail = err?.response?.data?.detail;

  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        const loc = Array.isArray(item.loc) ? item.loc.join(".") : item.loc;
        return loc ? `${loc}: ${item.msg}` : item.msg;
      })
      .filter(Boolean)
      .join("; ");
  }

  if (typeof detail === "string") return detail;

  if (detail && typeof detail === "object") {
    try {
      return JSON.stringify(detail);
    } catch {
      return fallback;
    }
  }

  return err?.response?.data?.message || err?.message || fallback;
}
