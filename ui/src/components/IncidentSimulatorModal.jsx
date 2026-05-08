import React, { useState } from "react";
import { runIncidentSimulation } from "../api.js";

const TOOL_OPTIONS = [
  "prometheus", "grafana", "jaeger", "loki", "alertmanager", "tempo",
  "elasticsearch", "datadog", "dynatrace", "appdynamics", "splunk",
];

const TOOL_ICONS = {
  prometheus: "📊", grafana: "📈", jaeger: "🔗", loki: "📝",
  alertmanager: "🔔", tempo: "⏱️", elasticsearch: "🔍",
  datadog: "🐕", dynatrace: "⚡", appdynamics: "🍎", splunk: "🔎",
};

const INCIDENT_TYPES = [
  { value: "high_latency", label: "High Latency" },
  { value: "error_spike", label: "Error Spike" },
  { value: "traffic_drop", label: "Traffic Drop" },
  { value: "traffic_surge", label: "Traffic Surge" },
  { value: "service_down", label: "Service Down" },
  { value: "dependency_failure", label: "Dependency Failure" },
];

const STATUS_LABELS = {
  "Ready": "#15803d",
  "Mostly Ready": "#ea580c",
  "At Risk": "#ef4444",
  "High MTTR Risk": "#991b1b",
  "Not Ready": "#7c2d12",
};

export default function IncidentSimulatorModal({ onClose }) {
  const [incident, setIncident] = useState({
    application_name: "",
    environment: "prod",
    service_name: "",
    incident_type: "high_latency",
    description: "",
    canonical_services: "",
  });

  const [tools, setTools] = useState([]);
  const [aiEnabled, setAiEnabled] = useState(false);
  const [aiProvider, setAiProvider] = useState("anthropic");
  const [aiKey, setAiKey] = useState("");

  const [validatingId, setValidatingId] = useState(null);
  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState(null);
  const [result, setResult] = useState(null);

  const busy = validatingId !== null || running;

  const addTool = () => {
    setTools([...tools, { name: "prometheus", enabled: true, url: "", api_key: "" }]);
  };

  const removeTool = (idx) => {
    setTools(tools.filter((_, i) => i !== idx));
  };

  const updateTool = (idx, field, value) => {
    const updated = [...tools];
    updated[idx][field] = value;
    setTools(updated);
  };

  const validateAll = async () => {
    if (!tools.length) {
      setStatus({ type: "error", title: "No tools", msg: "Add at least one tool." });
      return;
    }
    setValidatingId("all");
    for (let i = 0; i < tools.length; i++) {
      setValidatingId(i);
      try {
        await runIncidentSimulation({ incident: { ...incident, canonical_services: incident.canonical_services.split("\n").filter(s => s.trim()) }, tools: [tools[i]], client: {}, ai: null });
      } catch (e) {
        setStatus({ type: "warn", title: `Tool ${i + 1}`, msg: e.response?.data?.detail || e.message });
      }
    }
    setValidatingId(null);
    setStatus({ type: "success", title: "Validation", msg: "All tools validated." });
  };

  const runSimulation = async () => {
    if (!incident.application_name || !incident.service_name) {
      setStatus({ type: "error", title: "Missing", msg: "Enter app name and service name." });
      return;
    }
    if (!tools.length) {
      setStatus({ type: "error", title: "No tools", msg: "Add at least one tool." });
      return;
    }

    setRunning(true);
    try {
      const payload = {
        incident: {
          ...incident,
          canonical_services: incident.canonical_services.split("\n").filter(s => s.trim()),
        },
        tools: tools.filter(t => t.enabled),
        client: { name: incident.application_name, environment: incident.environment },
        ai: aiEnabled ? { enabled: true, provider: aiProvider, api_key: aiKey } : null,
      };

      const res = await runIncidentSimulation(payload);
      setResult(res.data);
      setStatus({ type: "success", title: "Success", msg: "Simulation completed." });
    } catch (e) {
      setStatus({ type: "error", title: "Failed", msg: e.response?.data?.detail || e.message });
    } finally {
      setRunning(false);
    }
  };

  const triggerDownload = (url) => {
    if (!url) return;
    const a = document.createElement("a");
    a.href = url; a.download = ""; a.style.display = "none";
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
  };

  return (
    <div className="modal-overlay" role="dialog">
      <div className="modal modal-wide">
        <div className="modal-header modal-header-amber">
          <span style={{ fontSize: "24px" }}>🔍</span>
          <div>
            <h1>Incident Readiness Simulator</h1>
            <p>Simulate production incidents for a selected application service and score readiness across detection, visibility, diagnosis, and response.</p>
          </div>
          <button onClick={onClose} className="modal-close">✕</button>
        </div>

        <div className="modal-body">
          {status && (
            <div className={`modal-alert modal-alert-${status.type} animate-in`}>
              <span className="modal-alert-icon">{status.type === "success" ? "✓" : (status.type === "warn" ? "⚠" : "✗")}</span>
              <div>
                <div className="modal-alert-title">{status.title}</div>
                <div className="modal-alert-msg">{status.msg}</div>
              </div>
            </div>
          )}

          {!result ? (
            <>
              <fieldset disabled={busy}>
                <h3>Incident Scope</h3>
                <div className="mtool-grid">
                  <div className="mtool-field">
                    <label>Application Name</label>
                    <input
                      value={incident.application_name}
                      onChange={(e) => setIncident({ ...incident, application_name: e.target.value })}
                      placeholder="e.g., PaymentService"
                    />
                  </div>
                  <div className="mtool-field">
                    <label>Environment</label>
                    <select value={incident.environment} onChange={(e) => setIncident({ ...incident, environment: e.target.value })}>
                      <option>prod</option>
                      <option>staging</option>
                      <option>dev</option>
                    </select>
                  </div>
                  <div className="mtool-field">
                    <label>Service Name</label>
                    <input
                      value={incident.service_name}
                      onChange={(e) => setIncident({ ...incident, service_name: e.target.value })}
                      placeholder="e.g., payment-api"
                    />
                  </div>
                  <div className="mtool-field">
                    <label>Incident Type</label>
                    <select value={incident.incident_type} onChange={(e) => setIncident({ ...incident, incident_type: e.target.value })}>
                      {INCIDENT_TYPES.map((t) => (
                        <option key={t.value} value={t.value}>{t.label}</option>
                      ))}
                    </select>
                  </div>
                </div>

                <div className="mtool-field">
                  <label>Incident Description</label>
                  <textarea
                    value={incident.description}
                    onChange={(e) => setIncident({ ...incident, description: e.target.value })}
                    placeholder="Optional: describe the incident scenario..."
                    rows={3}
                  />
                </div>

                <h3 style={{ marginTop: "20px" }}>Canonical Services</h3>
                <div className="mtool-field">
                  <label>Service List (one per line)</label>
                  <textarea
                    value={incident.canonical_services}
                    onChange={(e) => setIncident({ ...incident, canonical_services: e.target.value })}
                    placeholder="e.g., auth-service&#10;payment-service&#10;notification-service"
                    rows={4}
                  />
                </div>

                <h3 style={{ marginTop: "20px" }}>Tool Connections</h3>
                <div className="mtool-add-bar">
                  <button onClick={addTool} className="btn btn-outline">+ Add Tool</button>
                </div>

                {tools.map((tool, idx) => (
                  <div key={idx} className="mtool-item" style={{ marginBottom: "12px" }}>
                    <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                      <input
                        type="checkbox"
                        checked={tool.enabled}
                        onChange={(e) => updateTool(idx, "enabled", e.target.checked)}
                      />
                      <select value={tool.name} onChange={(e) => updateTool(idx, "name", e.target.value)}>
                        {TOOL_OPTIONS.map((t) => (
                          <option key={t} value={t}>{TOOL_ICONS[t]} {t}</option>
                        ))}
                      </select>
                      <input
                        placeholder="URL"
                        value={tool.url}
                        onChange={(e) => updateTool(idx, "url", e.target.value)}
                        style={{ flex: 1 }}
                      />
                      <input
                        placeholder="API Key (optional)"
                        type="password"
                        value={tool.api_key}
                        onChange={(e) => updateTool(idx, "api_key", e.target.value)}
                        style={{ flex: 1 }}
                      />
                      <button onClick={() => removeTool(idx)} className="btn btn-danger" style={{ width: "40px" }}>✕</button>
                    </div>
                  </div>
                ))}

                <h3 style={{ marginTop: "20px" }}>AI Enrichment (Optional)</h3>
                <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
                  <input
                    type="checkbox"
                    checked={aiEnabled}
                    onChange={(e) => setAiEnabled(e.target.checked)}
                  />
                  <span>Enable AI for executive summary</span>
                </div>

                {aiEnabled && (
                  <div style={{ marginTop: "12px", padding: "12px", background: "#fef3c7", borderRadius: "6px" }}>
                    <div className="mtool-field" style={{ marginBottom: "8px" }}>
                      <label>AI Provider</label>
                      <select value={aiProvider} onChange={(e) => setAiProvider(e.target.value)}>
                        <option value="anthropic">Anthropic Claude</option>
                        <option value="azure">Azure OpenAI</option>
                      </select>
                    </div>
                    <div className="mtool-field">
                      <label>API Key</label>
                      <input
                        type="password"
                        value={aiKey}
                        onChange={(e) => setAiKey(e.target.value)}
                        placeholder="sk-ant-... or Azure key"
                      />
                    </div>
                  </div>
                )}
              </fieldset>
            </>
          ) : (
            <div>
              <div style={{ marginBottom: "16px" }}>
                <h3>Overall Readiness Score: <span style={{ color: STATUS_LABELS[result.readiness_status] }}>{result.overall_readiness_score?.toFixed(0)} — {result.readiness_status}</span></h3>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "12px", marginBottom: "16px" }}>
                <div style={{ padding: "12px", background: "#eff6ff", borderRadius: "6px", textAlign: "center" }}>
                  <div style={{ fontSize: "12px", color: "#666" }}>Detection</div>
                  <div style={{ fontSize: "20px", fontWeight: "bold", color: "#667eea" }}>{result.detection_score?.toFixed(0)}</div>
                </div>
                <div style={{ padding: "12px", background: "#faf5ff", borderRadius: "6px", textAlign: "center" }}>
                  <div style={{ fontSize: "12px", color: "#666" }}>Visibility</div>
                  <div style={{ fontSize: "20px", fontWeight: "bold", color: "#764ba2" }}>{result.visibility_score?.toFixed(0)}</div>
                </div>
                <div style={{ padding: "12px", background: "#fffbeb", borderRadius: "6px", textAlign: "center" }}>
                  <div style={{ fontSize: "12px", color: "#666" }}>Diagnosis</div>
                  <div style={{ fontSize: "20px", fontWeight: "bold", color: "#f59e0b" }}>{result.diagnosis_score?.toFixed(0)}</div>
                </div>
                <div style={{ padding: "12px", background: "#f0fdf4", borderRadius: "6px", textAlign: "center" }}>
                  <div style={{ fontSize: "12px", color: "#666" }}>Response</div>
                  <div style={{ fontSize: "20px", fontWeight: "bold", color: "#10b981" }}>{result.response_score?.toFixed(0)}</div>
                </div>
              </div>
              <p><strong>Preview URL:</strong> <code>{result.preview_url}</code></p>
            </div>
          )}
        </div>

        <div className="modal-footer">
          <button onClick={onClose} className="btn btn-outline" disabled={busy}>Cancel</button>
          {!result && <button onClick={validateAll} className="btn btn-outline" disabled={busy}>Validate All</button>}
          {!result ? (
            <button onClick={runSimulation} className="btn btn-amber" disabled={busy}>
              {running ? "Running..." : "Run Simulation"}
            </button>
          ) : (
            <>
              <button onClick={() => result.download_url && triggerDownload(result.download_url)} className="btn btn-amber">
                Download HTML
              </button>
              <button onClick={() => result.json_url && triggerDownload(result.json_url)} className="btn btn-amber">
                Download JSON
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
