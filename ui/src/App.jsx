import { useState } from "react";
import { validateTool, exportExcel, runAssessment } from "./api";
import "./styles.css";

const toolOptions = [
  "prometheus",
  "grafana",
  "loki",
  "jaeger",
  "alertmanager",
  "tempo",
  "elasticsearch",
  "dynatrace",
  "datadog",
  "appdynamics",
  "splunk",
];

const usageOptions = ["metrics", "traces", "logs", "dashboards", "alerts"];
const llmOptions = ["gpt-5-mini", "gpt-4.1", "claude"];

export default function App() {
  const [clientName, setClientName] = useState("MVP Client");
  const [environment, setEnvironment] = useState("dev");
  const [selectedTool, setSelectedTool] = useState("prometheus");
  const [toolUrl, setToolUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [usages, setUsages] = useState(["metrics"]);
  const [tools, setTools] = useState([]);
  const [validationResults, setValidationResults] = useState({});
  const [aiEnabled, setAiEnabled] = useState(false);
  const [selectedLlm, setSelectedLlm] = useState("gpt-5-mini");
  const [aiApiKey, setAiApiKey] = useState("");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);

  const toggleUsage = (usage) => {
    setUsages((prev) =>
      prev.includes(usage)
        ? prev.filter((u) => u !== usage)
        : [...prev, usage]
    );
  };

  const addTool = () => {
    if (!toolUrl.trim()) {
      setMessage("Tool URL is required.");
      return;
    }

    if (usages.length === 0) {
      setMessage("Select at least one usage type.");
      return;
    }

    const newTool = {
      name: selectedTool,
      enabled: true,
      usages,
      url: toolUrl,
      api_key: apiKey || null,
    };

    setTools((prev) => {
      const filtered = prev.filter((tool) => tool.name !== selectedTool);
      return [...filtered, newTool];
    });

    setMessage(`${selectedTool} added/updated successfully.`);
    setToolUrl("");
    setApiKey("");
    setUsages(["metrics"]);
  };

  const validateSingleTool = async (tool) => {
    try {
      const res = await validateTool(tool);
      setValidationResults((prev) => ({
        ...prev,
        [tool.name]: res.data,
      }));
    } catch (err) {
      setValidationResults((prev) => ({
        ...prev,
        [tool.name]: {
          reachable: false,
          message: err?.response?.data?.detail || "Validation failed",
        },
      }));
    }
  };

  const buildPayload = () => ({
    client: {
      name: clientName,
      environment,
    },
    tools,
    ai: {
      enabled: aiEnabled,
      provider: aiEnabled ? selectedLlm : null,
      model: aiEnabled ? selectedLlm : null,
      api_key: aiEnabled ? aiApiKey : null,
    },
  });

  const handleExport = async () => {
    if (tools.length === 0) {
      setMessage("Add at least one tool before running crawler.");
      return;
    }

    try {
      setLoading(true);
      const res = await exportExcel(buildPayload());
      setMessage(`${res.data.message} | ${res.data.download_url || ""}`);
    } catch (err) {
      setMessage(err?.response?.data?.detail || "Export failed.");
    } finally {
      setLoading(false);
    }
  };

  const handleAssess = async () => {
    if (tools.length === 0) {
      setMessage("Add at least one tool before assessment.");
      return;
    }

    if (aiEnabled && !aiApiKey.trim()) {
      setMessage("AI API key is required when AI evaluation is enabled.");
      return;
    }

    try {
      setLoading(true);
      const res = await runAssessment(buildPayload());
      setMessage(`${res.data.message} | ${res.data.download_url || ""}`);
    } catch (err) {
      setMessage(err?.response?.data?.detail || "Assessment failed.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="page">
      <h1>ObservaScore UI MVP</h1>

      <section className="card">
        <h2>Client Details</h2>
        <div className="grid">
          <input
            type="text"
            value={clientName}
            onChange={(e) => setClientName(e.target.value)}
            placeholder="Client Name"
          />
          <input
            type="text"
            value={environment}
            onChange={(e) => setEnvironment(e.target.value)}
            placeholder="Environment"
          />
        </div>
      </section>

      <section className="card">
        <h2>Excel Utility / Crawler</h2>

        <div className="grid">
          <select value={selectedTool} onChange={(e) => setSelectedTool(e.target.value)}>
            {toolOptions.map((tool) => (
              <option key={tool} value={tool}>
                {tool}
              </option>
            ))}
          </select>

          <input
            type="text"
            value={toolUrl}
            onChange={(e) => setToolUrl(e.target.value)}
            placeholder="Tool URL"
          />

          <input
            type="text"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="API Key (optional)"
          />
        </div>

        <div className="usage-row">
          {usageOptions.map((usage) => (
            <label key={usage}>
              <input
                type="checkbox"
                checked={usages.includes(usage)}
                onChange={() => toggleUsage(usage)}
              />
              {usage}
            </label>
          ))}
        </div>

        <button onClick={addTool}>Add / Update Tool</button>

        <h3>Configured Tools</h3>

        {tools.length === 0 ? (
          <p>No tools added yet.</p>
        ) : (
          tools.map((tool) => (
            <div key={tool.name} className="tool-row">
              <div>
                <strong>{tool.name}</strong> — {tool.url}
              </div>
              <div className="tool-actions">
                <button onClick={() => validateSingleTool(tool)}>Validate</button>
                <span>
                  {validationResults[tool.name]
                    ? validationResults[tool.name].reachable
                      ? "✅ Connected"
                      : `❌ ${validationResults[tool.name].message}`
                    : ""}
                </span>
              </div>
            </div>
          ))
        )}

        <div className="actions">
          <button onClick={handleExport} disabled={loading}>
            {loading ? "Running..." : "Run Crawler & Download Excel"}
          </button>
        </div>
      </section>

      <section className="card">
        <h2>Assessment</h2>

        <label className="checkbox-line">
          <input
            type="checkbox"
            checked={aiEnabled}
            onChange={(e) => setAiEnabled(e.target.checked)}
          />
          Enable AI Evaluation
        </label>

        {aiEnabled && (
          <div className="grid">
            <select value={selectedLlm} onChange={(e) => setSelectedLlm(e.target.value)}>
              {llmOptions.map((llm) => (
                <option key={llm} value={llm}>
                  {llm}
                </option>
              ))}
            </select>

            <input
              type="text"
              value={aiApiKey}
              onChange={(e) => setAiApiKey(e.target.value)}
              placeholder="LLM API Key"
            />
          </div>
        )}

        <div className="actions">
          <button onClick={handleAssess} disabled={loading}>
            {loading ? "Running..." : "Run Assessment"}
          </button>
        </div>
      </section>

      {message && (
        <section className="card">
          <strong>Status:</strong>
          <p>{message}</p>
        </section>
      )}
    </div>
  );
}
