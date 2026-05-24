import { useState } from "react";

const FALLBACK_TOOLS = [
  { tool: "prometheus", base_url: "http://10.235.21.132:9090" },
  { tool: "alertmanager", base_url: "http://10.235.21.132:9093" },
  { tool: "elasticsearch", base_url: "http://10.235.21.132:9200" },
];

function mapValidatedTools(validatedTools = []) {
  const mapped = validatedTools
    .filter((tool) => tool?.status === "success" || tool?.ok === true || tool?.validated === true)
    .map((tool) => ({
      tool: String(tool.tool || tool.name || tool.type || "").toLowerCase(),
      base_url: tool.base_url || tool.baseUrl || tool.url || tool.endpoint,
      auth_token: tool.auth_token || tool.authToken || tool.token || undefined,
    }))
    .filter((tool) => tool.tool && tool.base_url);

  return mapped.length ? mapped : FALLBACK_TOOLS;
}

export default function AYOSAModal({ onClose, validatedTools = [] }) {
  const [message, setMessage] = useState("Investigate checkout latency and errors");
  const [service, setService] = useState("checkout");
  const [timeRange, setTimeRange] = useState("30m");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");

  const ayosaTools = mapValidatedTools(validatedTools);
  

  async function runInvestigation() {
    setLoading(true);
    setError("");
    setResult(null);

    try {
      const response = await fetch("/api/ayosa/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          message,
          service,
          time_range: timeRange,
          tools: ayosaTools,
        }),
      });

      if (!response.ok) {
        throw new Error(`AYOSA request failed with HTTP ${response.status}`);
      }

      const data = await response.json();
      setResult(data);
    } catch (err) {
      setError(err.message || "AYOSA investigation failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="modal-backdrop">
      <div className="modal ayosa-modal">
        <div className="modal-header">
          <div>
            <div className="modal-title">AYOSA</div>
            <div className="modal-subtitle">Ask Your Observability Stack Anything</div>
          </div>
          <button className="icon-button" onClick={onClose}>×</button>
        </div>

        <div className="modal-body">
          <div className="ayosa-grid">
            <section className="ayosa-panel">
              <h3>Investigation</h3>

              <label>Question</label>
              <textarea
                value={message}
                onChange={(event) => setMessage(event.target.value)}
                rows={3}
              />

              <label>Service</label>
              <input
                value={service}
                onChange={(event) => setService(event.target.value)}
                placeholder="checkout"
              />

              <label>Time range</label>
              <input
                value={timeRange}
                onChange={(event) => setTimeRange(event.target.value)}
                placeholder="30m"
              />

              <button className="primary-button" onClick={runInvestigation} disabled={loading}>
                {loading ? "Investigating..." : "Run AYOSA Investigation"}
              </button>

              <div className="ayosa-tools">
                <strong>Live tools:</strong>
                {ayosaTools.map((tool) => (
                    <span key={`${tool.tool}-${tool.base_url}`}>{tool.tool}</span>
                ))}
              </div>

              {error && <div className="error-box">{error}</div>}
            </section>

            <section className="ayosa-panel ayosa-results">
              {!result && (
                <div className="empty-state">
                  Run an investigation to see RCA, impact, timeline, evidence, and suggested actions.
                </div>
              )}

              {result && (
                <>
                  <div className="ayosa-summary-card">
                    <div className="score-pill">Confidence: {Math.round(result.confidence * 100)}%</div>
                    <h3>AYOSA Summary</h3>
                    <p>{result.answer}</p>
                  </div>

                  <div className="ayosa-card">
                    <h3>Probable Root Cause</h3>
                    <p>{result.probable_root_cause}</p>
                  </div>

                  <div className="ayosa-card">
                    <h3>Impact</h3>
                    <p>{result.impact}</p>
                  </div>

                  <div className="ayosa-card">
                    <h3>Detected Patterns</h3>
                    <div className="pattern-list">
                      {(result.detected_patterns || []).map((pattern) => (
                        <span key={pattern}>{pattern}</span>
                      ))}
                    </div>
                  </div>

                  <div className="ayosa-card">
                    <h3>Timeline</h3>
                    <div className="timeline-list">
                      {(result.timeline || []).map((item, index) => (
                        <div className="timeline-item" key={`${item.timestamp}-${index}`}>
                          <strong>{item.source}</strong>
                          <small>{item.timestamp}</small>
                          <p>{item.event}</p>
                        </div>
                      ))}
                    </div>
                  </div>

                  <div className="ayosa-card">
                    <h3>Suggested Actions</h3>
                    <ul>
                      {(result.suggested_actions || []).map((action) => (
                        <li key={action}>{action}</li>
                      ))}
                    </ul>
                  </div>

                  <div className="ayosa-card">
                    <h3>Evidence</h3>
                    {(result.evidence || []).map((item, index) => (
                      <details key={`${item.source}-${index}`}>
                        <summary>
                          {item.source} · {item.signal} · {item.status}
                        </summary>
                        <p>{item.finding}</p>
                        {item.query && <pre>{item.query}</pre>}
                      </details>
                    ))}
                  </div>
                </>
              )}
            </section>
          </div>
        </div>
      </div>
    </div>
  );
}