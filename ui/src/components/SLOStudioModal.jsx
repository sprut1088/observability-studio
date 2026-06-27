import { useMemo, useState } from "react";
import { API_HOST, v1SloStudio } from "../api";

const SUPPORTED = ["prometheus", "jaeger", "tempo", "grafana", "alertmanager", "loki", "splunk", "opensearch"];

function normalizeTools(validatedTools = []) {
  return validatedTools
    .map((tool) => {
      const name = (tool.tool_name || tool.toolName || tool.name || tool.tool || "").toLowerCase();
      const url = tool.base_url || tool.baseUrl || tool.url || "";
      return {
        name,
        tool: name,
        tool_name: name,
        url,
        base_url: url,
        auth_token: tool.auth_token || tool.authToken || tool.api_key || null,
      };
    })
    .filter((tool) => tool.name && tool.url && SUPPORTED.includes(tool.name));
}

function absoluteUrl(path) {
  if (!path) return null;
  return path.startsWith("http") ? path : `${API_HOST}${path}`;
}

function downloadArtifact(path, fallbackName) {
  const url = absoluteUrl(path);
  if (!url) return;

  const a = document.createElement("a");
  a.href = url;
  a.download = fallbackName || "";
  a.target = "_blank";
  a.rel = "noreferrer";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

export default function SLOStudioModal({ onClose, validatedTools = [] }) {
  const tools = useMemo(() => normalizeTools(validatedTools), [validatedTools]);

  const [application, setApplication] = useState("");
  const [service, setService] = useState("");
  const [primaryJourney, setPrimaryJourney] = useState("");
  const [criticality, setCriticality] = useState("balanced");
  const [objectiveStyle, setObjectiveStyle] = useState("balanced");
  const [lookbackDays, setLookbackDays] = useState(7);
  const [windowDays, setWindowDays] = useState(30);
  const [repoPath, setRepoPath] = useState("");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  async function run() {
    setRunning(true);
    setError(null);
    setResult(null);

    try {
      const payload = {
        application: application || null,
        service: service || null,
        primary_journey: primaryJourney || null,
        criticality,
        objective_style: objectiveStyle,
        lookback_days: Number(lookbackDays),
        window_days: Number(windowDays),
        include_yaml: true,
        include_ai: false,
        repo_path: repoPath || null,
        tools,
      };

      const res = await v1SloStudio(payload);
      if (!res.data?.success) {
        throw new Error(res.data?.error || "SLO Studio failed");
      }

      setResult(res.data);
    } catch (err) {
      setError(err?.response?.data?.error || err?.response?.data?.detail || err.message || String(err));
    } finally {
      setRunning(false);
    }
  }

  const reportUrl = absoluteUrl(result?.report_url);

  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal modal-wide" role="dialog" aria-modal="true" aria-label="SLO Studio">
        <div className="modal-header modal-header-teal">
          <div className="modal-header-left">
            <span className="modal-icon">📏</span>
            <div>
              <div className="modal-title">SLO Studio</div>
              <div className="modal-subtitle">
                Evidence-backed SLO discovery from metrics, traces, alerts, and repository context.
              </div>
            </div>
          </div>
          <button className="modal-close" onClick={onClose} aria-label="Close">✕</button>
        </div>

        <div className="modal-body">
          <div className="modal-alert modal-alert-success">
            <span className="modal-alert-icon">✓</span>
            <div>
              <div className="modal-alert-title">{tools.length} compatible tool(s) loaded</div>
              <div className="modal-alert-msg">
                SLO Studio will use Prometheus for trends, Jaeger/Tempo for operations, and optional repository context.
              </div>
            </div>
          </div>

          <div className="form-grid form-grid-2">
            <div className="form-group">
              <label className="form-label">Application</label>
              <input className="form-input" value={application} onChange={(e) => setApplication(e.target.value)} placeholder="e.g. astronomy-shop" />
            </div>

            <div className="form-group">
              <label className="form-label">Service</label>
              <input className="form-input" value={service} onChange={(e) => setService(e.target.value)} placeholder="Optional, e.g. cart" />
            </div>

            <div className="form-group">
              <label className="form-label">Primary Journey</label>
              <input className="form-input" value={primaryJourney} onChange={(e) => setPrimaryJourney(e.target.value)} placeholder="e.g. checkout" />
            </div>

            <div className="form-group">
              <label className="form-label">Criticality</label>
              <select className="form-select" value={criticality} onChange={(e) => setCriticality(e.target.value)}>
                <option value="balanced">Auto / Balanced</option>
                <option value="critical">Critical</option>
                <option value="high">High</option>
                <option value="medium">Medium</option>
                <option value="low">Low</option>
              </select>
            </div>

            <div className="form-group">
              <label className="form-label">Objective Style</label>
              <select className="form-select" value={objectiveStyle} onChange={(e) => setObjectiveStyle(e.target.value)}>
                <option value="conservative">Conservative</option>
                <option value="balanced">Balanced</option>
                <option value="aggressive">Aggressive</option>
              </select>
            </div>

            <div className="form-group">
              <label className="form-label">Lookback Days</label>
              <input className="form-input" type="number" min="1" max="90" value={lookbackDays} onChange={(e) => setLookbackDays(e.target.value)} />
            </div>

            <div className="form-group">
              <label className="form-label">SLO Window Days</label>
              <input className="form-input" type="number" min="7" max="90" value={windowDays} onChange={(e) => setWindowDays(e.target.value)} />
            </div>

            <div className="form-group">
              <label className="form-label">Repo Path</label>
              <input className="form-input" value={repoPath} onChange={(e) => setRepoPath(e.target.value)} placeholder="/app or /home/user/repo, optional" />
            </div>
          </div>

          {error && (
            <div className="modal-alert modal-alert-error">
              <span className="modal-alert-icon">✗</span>
              <div>
                <div className="modal-alert-title">SLO Studio failed</div>
                <div className="modal-alert-msg">{error}</div>
              </div>
            </div>
          )}

          {result && (
            <div className="modal-alert modal-alert-success">
              <span className="modal-alert-icon">✓</span>

              <div style={{ flex: 1 }}>
                <div className="modal-alert-title">SLO Studio report ready</div>

                <div className="modal-alert-msg">
                  Services: {result.summary?.service_count ?? 0} · Existing SLOs:{" "}
                  {result.summary?.existing_slo_count ?? 0} · Top SLOs:{" "}
                  {result.summary?.top_recommendation_count ??
                    result.summary?.recommended_slo_count ??
                    0}{" "}
                  · Production-ready:{" "}
                  {result.summary?.production_ready_slo_count ??
                    result.summary?.recommended_slo_count ??
                    0}{" "}
                  · Evidence: {result.summary?.evidence_count ?? 0}
                </div>

                <div
                  style={{
                    marginTop: "10px",
                    display: "flex",
                    gap: "8px",
                    flexWrap: "wrap",
                  }}
                >
                  {result.report_url && (
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      onClick={() =>
                        downloadArtifact(result.report_url, "slo-studio-report.html")
                      }
                    >
                      ⬇ Download Report
                    </button>
                  )}

                  {result.json_url && (
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      onClick={() =>
                        downloadArtifact(result.json_url, "slo-studio-report.json")
                      }
                    >
                      ⬇ Download JSON
                    </button>
                  )}

                  {result.yaml_url && (
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      onClick={() =>
                        downloadArtifact(result.yaml_url, "sloth-slos.yaml")
                      }
                    >
                      ⬇ Download YAML
                    </button>
                  )}
                </div>
              </div>
            </div>
          )}

          {reportUrl && (
            <div className="report-preview-card">
              <div className="report-preview-header">
                <div>
                  <div className="report-preview-title">SLO Studio Report Preview</div>
                  <div className="report-preview-subtitle">
                    Evidence-backed recommendations and generated Sloth YAML.
                  </div>
                </div>

                <div className="report-preview-actions">
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() =>
                      downloadArtifact(result.report_url, "slo-studio-report.html")
                    }
                  >
                    ⬇ Download Report
                  </button>
                </div>
              </div>

              <iframe
                className="report-preview-frame"
                title="SLO Studio report"
                src={reportUrl}
              />
            </div>
          )}

        </div>

        <div className="modal-footer">
          <button className="btn btn-secondary" onClick={onClose} disabled={running}>Cancel</button>
          <button className="btn btn-teal" onClick={run} disabled={running || tools.length === 0}>
            {running ? (<><span className="spinner" /> Building SLO intelligence…</>) : "📏 Run SLO Studio"}
          </button>
        </div>
      </div>
    </div>
  );
}