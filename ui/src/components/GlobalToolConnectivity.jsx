import { useEffect, useMemo, useState } from "react";
import { v1Validate } from "../api";
import { DEMO_TOOLS, DEMO_TOOLS_ENABLED } from "../demoTools";

const TOOL_OPTIONS = [
  { value: "prometheus", label: "🔥 Prometheus" },
  { value: "grafana", label: "📊 Grafana" },
  { value: "loki", label: "📋 Loki" },
  { value: "jaeger", label: "🔍 Jaeger" },
  { value: "alertmanager", label: "🔔 Alertmanager" },
  { value: "tempo", label: "⚡ Tempo" },
  { value: "elasticsearch", label: "🔎 Elasticsearch" },
  { value: "opensearch", label: "🔎 OpenSearch" },
  { value: "dynatrace", label: "🛡️ Dynatrace" },
  { value: "datadog", label: "🐶 Datadog" },
  { value: "appdynamics", label: "🎛️ AppDynamics" },
  { value: "splunk", label: "🌊 Splunk" },
];

function deriveSplunkUrls(inputUrl) {
  try {
    const parsed = new URL(inputUrl);
    const hostname = parsed.hostname;

    return {
      splunk_base_url: `http://${hostname}:8000`,
      splunk_mgmt_url: `https://${hostname}:8089`,
      splunk_hec_url: `http://${hostname}:8088`,
      splunk_verify_ssl: false,
    };
  } catch {
    return {};
  }
}

function normalizeToolIdentity(tool) {
  return `${tool.tool_name || tool.toolName || tool.name || ""}::${
    tool.base_url || tool.baseUrl || tool.url || ""
  }`;
}

function mergeDemoTools(nextTools = []) {
  if (!DEMO_TOOLS_ENABLED) {
    return nextTools;
  }

  const manualTools = nextTools.filter(
    (tool) =>
      !DEMO_TOOLS.some(
        (demoTool) => normalizeToolIdentity(demoTool) === normalizeToolIdentity(tool)
      )
  );

  return [...DEMO_TOOLS, ...manualTools];
}

function loadInitialTools() {
  if (DEMO_TOOLS_ENABLED) {
    return DEMO_TOOLS;
  }

  try {
    const saved = sessionStorage.getItem("observabilityStudioTools");
    return saved ? JSON.parse(saved) : [];
  } catch {
    return [];
  }
}

export default function GlobalToolConnectivity({ onChange }) {
  const [toolName, setToolName] = useState("prometheus");
  const [baseUrl, setBaseUrl] = useState("");
  const [authToken, setAuthToken] = useState("");
  const [tools, setTools] = useState(loadInitialTools);
  const [validating, setValidating] = useState(false);
  const [message, setMessage] = useState(
    DEMO_TOOLS_ENABLED ? "Demo observability tools are preloaded and ready." : ""
  );

  const validatedTools = useMemo(
    () => tools.filter((tool) => tool.validated),
    [tools]
  );

  useEffect(() => {
    onChange?.(tools);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const persistInMemory = (nextTools) => {
    const finalTools = mergeDemoTools(nextTools);

    setTools(finalTools);
    onChange?.(finalTools);

    try {
      sessionStorage.setItem(
        "observabilityStudioTools",
        JSON.stringify(finalTools)
      );
    } catch {
      // ignore session storage failure
    }
  };

  const addAndValidateTool = async () => {
    if (!baseUrl.trim()) {
      setMessage("Base URL is required.");
      return;
    }

    setValidating(true);
    setMessage("");

    const splunkFields =
      toolName === "splunk" ? deriveSplunkUrls(baseUrl.trim()) : {};

    const candidate = {
      tool_name: toolName,
      toolName,
      name: toolName,
      base_url: baseUrl.trim(),
      baseUrl: baseUrl.trim(),
      url: baseUrl.trim(),
      auth_token: authToken.trim() || null,
      authToken: authToken.trim() || null,
      ...splunkFields,
      splunk_hec_token:
        toolName === "splunk" ? authToken.trim() || null : undefined,
    };

    try {
      const res = await v1Validate(candidate);

      const nextTool = {
        ...candidate,
        id: `${toolName}-${Date.now()}`,
        validated: true,
        validation_result: res.data,
        validation: res.data,
        validated_at: new Date().toISOString(),
      };

      const nextTools = [
        ...tools.filter(
          (tool) =>
            !(
              (tool.tool_name || tool.toolName) === nextTool.tool_name &&
              (tool.base_url || tool.baseUrl || tool.url) === nextTool.base_url
            )
        ),
        nextTool,
      ];

      persistInMemory(nextTools);
      setBaseUrl("");
      setAuthToken("");
      setMessage("Tool validated and saved for this browser session.");
    } catch (error) {
      setMessage(
        error?.response?.data?.detail ||
          error?.response?.data?.error ||
          error?.message ||
          "Validation failed."
      );
    } finally {
      setValidating(false);
    }
  };

  const removeTool = (id) => {
    if (DEMO_TOOLS_ENABLED && String(id).startsWith("demo-")) {
      setMessage("Demo default tools cannot be removed. Use Clear All to reset manual tools.");
      return;
    }

    const nextTools = tools.filter((tool) => tool.id !== id);
    persistInMemory(nextTools);
  };

  const clearAll = () => {
    const nextTools = DEMO_TOOLS_ENABLED ? DEMO_TOOLS : [];
    setTools(nextTools);
    onChange?.(nextTools);

    try {
      sessionStorage.setItem(
        "observabilityStudioTools",
        JSON.stringify(nextTools)
      );
    } catch {
      // ignore session storage failure
    }

    setMessage(
      DEMO_TOOLS_ENABLED
        ? "Manual tools cleared. Demo observability tools remain preloaded."
        : "All session tools cleared."
    );
  };

  return (
    <section className="global-tool-connectivity">
      <div className="connectivity-header">
        <div>
          <h2>Tool Connectivity</h2>
          <p>
            {DEMO_TOOLS_ENABLED
              ? "Demo observability tools are preloaded for this environment. You can add extra tools if needed."
              : "Add and validate tools for this session. Reloading the page clears them."}
          </p>
        </div>

        {validatedTools.length > 0 && (
          <button className="secondary-btn" onClick={clearAll}>
            Clear All
          </button>
        )}
      </div>

      <div className="connectivity-form">
        <label>
          Tool
          <select value={toolName} onChange={(e) => setToolName(e.target.value)}>
            {TOOL_OPTIONS.map((tool) => (
              <option key={tool.value} value={tool.value}>
                {tool.label}
              </option>
            ))}
          </select>
        </label>

        <label>
          Base URL
          <input
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="http://10.235.21.132:9090"
          />
        </label>

        <label>
          Auth Token
          <input
            value={authToken}
            onChange={(e) => setAuthToken(e.target.value)}
            placeholder="Optional"
            type="password"
          />
        </label>

        <button onClick={addAndValidateTool} disabled={validating}>
          {validating ? "Validating..." : "Validate + Save"}
        </button>
      </div>

      {message && <div className="connectivity-message">{message}</div>}

      <div className="validated-tools">
        {tools.length === 0 ? (
          <div className="empty-tools">
            No tools validated yet. Add at least one tool to unlock modules.
          </div>
        ) : (
          tools.map((tool) => (
            <div className="validated-tool" key={tool.id}>
              <div>
                <strong>
                  {tool.tool_name}
                  {tool.demo_default ? " · Demo Default" : ""}
                </strong>
                <span>{tool.base_url}</span>
              </div>

              <span className="status-ok">Validated</span>

              <button onClick={() => removeTool(tool.id)}>
                {tool.demo_default ? "Locked" : "Remove"}
              </button>
            </div>
          ))
        )}
      </div>
    </section>
  );
}