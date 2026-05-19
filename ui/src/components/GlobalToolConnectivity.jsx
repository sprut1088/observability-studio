import { useMemo, useState } from "react";
import { v1Validate } from "../api";

const TOOL_OPTIONS = [
  { value: "prometheus", label: "🔥 Prometheus" },
  { value: "grafana", label: "📊 Grafana" },
  { value: "loki", label: "📋 Loki" },
  { value: "jaeger", label: "🔍 Jaeger" },
  { value: "alertmanager", label: "🔔 Alertmanager" },
  { value: "tempo", label: "⚡ Tempo" },
  { value: "elasticsearch", label: "🔎 Elasticsearch" },
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

export default function GlobalToolConnectivity({ onChange }) {
  const [toolName, setToolName] = useState("prometheus");
  const [baseUrl, setBaseUrl] = useState("");
  const [authToken, setAuthToken] = useState("");
  const [tools, setTools] = useState([]);
  const [validating, setValidating] = useState(false);
  const [message, setMessage] = useState("");

  const validatedTools = useMemo(
    () => tools.filter((tool) => tool.validated),
    [tools]
  );

  const persistInMemory = (nextTools) => {
    setTools(nextTools);
    onChange?.(nextTools);
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
      base_url: baseUrl.trim(),
      auth_token: authToken.trim() || null,
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
        validated_at: new Date().toISOString(),
      };

      const nextTools = [
        ...tools.filter(
          (tool) =>
            !(
              tool.tool_name === nextTool.tool_name &&
              tool.base_url === nextTool.base_url
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
          error?.message ||
          "Validation failed."
      );
    } finally {
      setValidating(false);
    }
  };

  const removeTool = (id) => {
    const nextTools = tools.filter((tool) => tool.id !== id);
    persistInMemory(nextTools);
  };

  const clearAll = () => {
    persistInMemory([]);
    setMessage("All session tools cleared.");
  };

  return (
    <section className="global-tool-connectivity">
      <div className="connectivity-header">
        <div>
          <h2>Tool Connectivity</h2>
          <p>
            Add and validate tools for this session. Reloading the page clears
            them.
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
                <strong>{tool.tool_name}</strong>
                <span>{tool.base_url}</span>
              </div>
              <span className="status-ok">Validated</span>
              <button onClick={() => removeTool(tool.id)}>Remove</button>
            </div>
          ))
        )}
      </div>
    </section>
  );
}