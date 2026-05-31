import { useMemo, useState } from "react";
import { runAyosaInvestigation, generateAyosaRunbook } from "../api";

const AI_PROVIDERS = [
  { value: "anthropic", label: "✨ Anthropic (Claude)" },
  { value: "azure", label: "🧠 Azure OpenAI" },
  { value: "openrouter", label: "🔀 OpenRouter" },
];

const OPENROUTER_MODELS = [
  { value: "anthropic/claude-sonnet-4-6", label: "Claude Sonnet 4.6" },
  { value: "anthropic/claude-3.5-sonnet", label: "Claude 3.5 Sonnet" },
  { value: "openai/gpt-4o", label: "GPT-4o" },
  { value: "openai/gpt-4-turbo", label: "GPT-4 Turbo" },
  { value: "google/gemini-pro-1.5", label: "Gemini 2.5 Flash Lite" },
  { value: "meta-llama/llama-3.1-70b-instruct", label: "Llama 3.1 70B" },
];

const DEFAULT_USAGES = {
  prometheus: ["metrics"],
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

function normalizeValidatedTools(validatedTools = []) {
  return validatedTools
    .map((tool) => ({
      toolName: tool.tool_name || tool.toolName || tool.name || tool.tool,
      baseUrl: tool.base_url || tool.baseUrl || tool.url || tool.endpoint,
      authToken: tool.auth_token || tool.authToken || tool.api_key || tool.token || null,
      validation: tool.validation_result || tool.validation || { reachable: true },
    }))
    .filter((tool) => tool.toolName && tool.baseUrl && DEFAULT_USAGES[tool.toolName]);
}

export default function AYOSAModal({ onClose, validatedTools = [] }) {
  const [message, setMessage] = useState("Investigate checkout latency and errors");
  const [service, setService] = useState("checkout");
  const [timeRange, setTimeRange] = useState("30m");

  // AI configuration
  const [useAi, setUseAi] = useState(false);
  const [aiProvider, setAiProvider] = useState("anthropic");
  const [aiApiKey, setAiApiKey] = useState("");
  const [azureEndpoint, setAzureEndpoint] = useState("");
  const [azureDeployment, setAzureDeployment] = useState("");
  const [openrouterModel, setOpenrouterModel] = useState("anthropic/claude-3.5-sonnet");

  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState(null);
  const [result, setResult] = useState(null);

  const [runbook, setRunbook] = useState(null);
  const [runbookBusy, setRunbookBusy] = useState(false);

  const tools = useMemo(
    () => normalizeValidatedTools(validatedTools),
    [validatedTools]
  );

  const busy = running;

  async function handleRun() {
    if (tools.length === 0) {
      setStatus({
        type: "error",
        title: "Validation error",
        msg: "No globally validated tools found. Close this modal and validate at least one tool from Tool Connectivity.",
      });
      return;
    }

    if (useAi && !aiApiKey.trim()) {
      setStatus({
        type: "error",
        title: "Missing API key",
        msg: "An AI API key is required when AI analysis is enabled.",
      });
      return;
    }

    if (useAi && aiProvider === "azure" && !azureEndpoint.trim()) {
      setStatus({
        type: "error",
        title: "Missing Azure endpoint",
        msg: "Azure OpenAI endpoint URL is required.",
      });
      return;
    }

    if (useAi && aiProvider === "azure" && !azureDeployment.trim()) {
      setStatus({
        type: "error",
        title: "Missing deployment name",
        msg: "Azure OpenAI deployment name is required.",
      });
      return;
    }

    setRunning(true);
    setStatus(null);
    setResult(null);

    try {
      const payload = {
        message: message.trim() || "Investigate service health",
        service: service.trim() || null,
        time_range: timeRange.trim() || "30m",
        tools: tools.map((tool) => ({
          tool: tool.toolName,
          base_url: tool.baseUrl,
          auth_token: tool.authToken ?? null,
        })),
        ai: {
          enabled: useAi,
          provider: useAi ? aiProvider : null,
          api_key: useAi ? aiApiKey : null,
          azure_endpoint: useAi && aiProvider === "azure" ? azureEndpoint : null,
          azure_deployment: useAi && aiProvider === "azure" ? azureDeployment : null,
          openrouter_model: useAi && aiProvider === "openrouter" ? openrouterModel : null,
        },
      };

      const res = await runAyosaInvestigation(payload);
      setResult(res.data);
      setRunbook(null);

      setStatus({
        type: "success",
        title: "AYOSA investigation complete",
        msg: `Investigation completed with ${Math.round((res.data.confidence || 0) * 100)}% confidence.${useAi && res.data.ai_analysis && !res.data.ai_analysis.error ? " AI analysis included." : ""}`,
      });
    } catch (err) {
      setStatus({
        type: "error",
        title: "AYOSA investigation failed",
        msg: err?.response?.data?.detail || err.message,
      });
    } finally {
      setRunning(false);
    }
  }

  async function handleGenerateRunbook() {
    try {
      setRunbookBusy(true);

      const payload = {
        message,
        service,
        time_range: timeRange,
        tools: tools.map((tool) => ({
          tool: tool.toolName,
          base_url: tool.baseUrl,
          auth_token: tool.authToken ?? null,
        })),
      };

      const response = await generateAyosaRunbook(payload);
      setRunbook(response.data.generated_runbook);
    } catch (err) {
      setStatus({
        type: "error",
        title: "Runbook generation failed",
        msg: err?.response?.data?.detail || err.message,
      });
    } finally {
      setRunbookBusy(false);
    }
  }

  async function handleCopyRunbook() {
    try {
      await navigator.clipboard.writeText(runbook || "");
      setStatus({
        type: "success",
        title: "Runbook copied",
        msg: "AYOSA runbook copied to clipboard.",
      });
    } catch (err) {
      setStatus({
        type: "error",
        title: "Copy failed",
        msg: err.message,
      });
    }
  }

  function downloadRunbook(filename, contentType) {
    if (!runbook) return;

    const blob = new Blob([runbook], { type: contentType });
    const url = URL.createObjectURL(blob);

    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();

    URL.revokeObjectURL(url);
  }

  function handleDownloadMarkdown() {
    downloadRunbook(
      `ayosa-runbook-${service || "incident"}.md`,
      "text/markdown"
    );
  }

  function handleDownloadText() {
    downloadRunbook(
      `ayosa-runbook-${service || "incident"}.txt`,
      "text/plain"
    );
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
        aria-label="AYOSA"
      >
        <div className="modal-header modal-header-violet">
          <div className="modal-header-left">
            <span className="modal-icon">🧠</span>
            <div>
              <div className="modal-title">AYOSA</div>
              <div className="modal-subtitle">
                Ask Your Observability Stack Anything using globally validated tools.
              </div>
            </div>
          </div>

          <button className="modal-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>

        <div className="modal-body">
          <div className="mtool-add-bar" style={{ marginBottom: 12 }}>
            <div className="form-group mtool-add-url">
              <label className="form-label">Question</label>
              <input
                className="form-input"
                type="text"
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                placeholder="Investigate checkout latency and errors"
                disabled={busy}
              />
            </div>

            <div className="form-group mtool-add-tool">
              <label className="form-label">Service</label>
              <input
                className="form-input"
                type="text"
                value={service}
                onChange={(e) => setService(e.target.value)}
                placeholder="checkout"
                disabled={busy}
              />
            </div>

            <div className="form-group mtool-add-tool">
              <label className="form-label">Time Range</label>
              <input
                className="form-input"
                type="text"
                value={timeRange}
                onChange={(e) => setTimeRange(e.target.value)}
                placeholder="30m"
                disabled={busy}
              />
            </div>
          </div>

          {tools.length > 0 ? (
            <>
              <div className="modal-alert modal-alert-success animate-in">
                <span className="modal-alert-icon">✓</span>
                <div>
                  <div className="modal-alert-title">
                    {tools.length} validated tool{tools.length !== 1 ? "s" : ""} loaded
                  </div>
                  <div className="modal-alert-msg">
                    AYOSA will query these live observability connections for RCA evidence.
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
              <span className="empty-icon">🧠</span>
              <span className="empty-text">
                No globally validated tools found. Close this modal and validate at least one observability tool from Tool Connectivity.
              </span>
            </div>
          )}

          {/* AI Toggle */}
          <div
            className={`toggle-row${useAi ? " toggle-row-active" : ""}`}
            onClick={() => !busy && setUseAi((v) => !v)}
            style={{ marginTop: 16 }}
          >
            <div className="toggle-label">
              <span className="toggle-emoji">🤖</span>
              <div>
                <div className="toggle-title">Enable AI-Powered Analysis</div>
                <div className="toggle-desc">
                  Enrich investigation results with deep LLM root cause analysis and remediation guidance.
                </div>
              </div>
            </div>
            <label className="switch" onClick={(e) => e.stopPropagation()}>
              <input
                type="checkbox"
                checked={useAi}
                onChange={(e) => setUseAi(e.target.checked)}
                disabled={busy}
              />
              <span className="switch-track" />
            </label>
          </div>

          {useAi && (
            <div className="modal-ai-fields animate-in">
              <div className="form-grid form-grid-2">
                <div className="form-group">
                  <label className="form-label">AI Provider</label>
                  <select
                    className="form-select"
                    value={aiProvider}
                    onChange={(e) => setAiProvider(e.target.value)}
                    disabled={busy}
                  >
                    {AI_PROVIDERS.map((p) => (
                      <option key={p.value} value={p.value}>{p.label}</option>
                    ))}
                  </select>
                </div>

                <div className="form-group">
                  <label className="form-label">
                    {aiProvider === "azure" ? "Azure API Key" : aiProvider === "openrouter" ? "OpenRouter API Key" : "Anthropic API Key"}
                  </label>
                  <input
                    className="form-input"
                    type="password"
                    value={aiApiKey}
                    onChange={(e) => setAiApiKey(e.target.value)}
                    placeholder={
                      aiProvider === "azure"
                        ? "Azure OpenAI key"
                        : aiProvider === "openrouter"
                        ? "sk-or-••••••••••••"
                        : "sk-ant-••••••••••••"
                    }
                    disabled={busy}
                  />
                </div>
              </div>

              {aiProvider === "azure" && (
                <div className="form-grid form-grid-2 animate-in" style={{ marginTop: 12 }}>
                  <div className="form-group">
                    <label className="form-label">Azure Endpoint URL</label>
                    <input
                      className="form-input"
                      type="url"
                      value={azureEndpoint}
                      onChange={(e) => setAzureEndpoint(e.target.value)}
                      placeholder="https://your-resource.openai.azure.com/"
                      disabled={busy}
                    />
                  </div>
                  <div className="form-group">
                    <label className="form-label">Deployment Name</label>
                    <input
                      className="form-input"
                      type="text"
                      value={azureDeployment}
                      onChange={(e) => setAzureDeployment(e.target.value)}
                      placeholder="e.g. gpt-4o"
                      disabled={busy}
                    />
                  </div>
                </div>
              )}

              {aiProvider === "openrouter" && (
                <div className="form-grid form-grid-1 animate-in" style={{ marginTop: 12 }}>
                  <div className="form-group">
                    <label className="form-label">Model</label>
                    <select
                      className="form-select"
                      value={openrouterModel}
                      onChange={(e) => setOpenrouterModel(e.target.value)}
                      disabled={busy}
                    >
                      {OPENROUTER_MODELS.map((m) => (
                        <option key={m.value} value={m.value}>{m.label}</option>
                      ))}
                    </select>
                  </div>
                </div>
              )}
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

          {result && (
            <div className="ayosa-result-stack animate-in">
              <div className="report-preview-card">
                <div className="report-preview-header">
                  <div>
                    <div className="report-preview-title">
                      AYOSA Investigation Summary
                    </div>
                    <div className="report-preview-subtitle">
                      Confidence: {Math.round((result.confidence || 0) * 100)}%
                      {result.ai_analysis && !result.ai_analysis.error && (
                        <span className="ayosa-ai-badge">✨ AI Enhanced</span>
                      )}
                    </div>
                  </div>
                </div>

                <div className="ayosa-result-card">
                  <div className="ayosa-result-label">Signal Coverage</div>

                  <div className="ayosa-signal-grid">
                    {Object.entries(result.signal_coverage || {}).map(([signal, providers]) => (
                      <div
                        key={signal}
                        className={`ayosa-signal-pill ${
                          providers.length ? "available" : "missing"
                        }`}
                      >
                        <strong>{signal}</strong>
                        <span>
                          {providers.length ? providers.join(", ") : "missing"}
                        </span>
                      </div>
                    ))}
                  </div>

                  {(result.missing_signals || []).length > 0 && (
                    <p className="ayosa-missing-note">
                      AYOSA could not query {result.missing_signals.join(", ")} because no matching validated tools were provided.
                    </p>
                  )}
                </div>

                <div className="ayosa-summary-grid">
                  <div className="ayosa-result-card ayosa-result-card-primary">
                    <div className="ayosa-result-label">Probable Root Cause</div>
                    <p>{result.probable_root_cause}</p>
                  </div>

                  <div className="ayosa-result-card">
                    <div className="ayosa-result-label">Impact</div>
                    <p>{result.impact}</p>
                  </div>
                </div>

                <div className="ayosa-result-card">
                  <div className="ayosa-result-label">Executive Summary</div>
                  <p>{result.answer}</p>
                </div>

                {/* AI Analysis Section */}
                {result.ai_analysis && !result.ai_analysis.error && (
                  <div className="ayosa-ai-analysis animate-in">
                    <div className="ayosa-ai-header">
                      <span>✨</span>
                      <div>
                        <div className="ayosa-ai-title">AI-Powered Analysis</div>
                        <div className="ayosa-ai-meta">
                          {result.ai_analysis.provider} · {result.ai_analysis.model} ·{" "}
                          <span className={`ayosa-confidence-badge ayosa-confidence-${result.ai_analysis.root_cause_confidence}`}>
                            {result.ai_analysis.root_cause_confidence} confidence
                          </span>
                        </div>
                      </div>
                    </div>

                    {result.ai_analysis.executive_summary && (
                      <div className="ayosa-result-card ayosa-result-card-primary">
                        <div className="ayosa-result-label">AI Executive Summary</div>
                        <p>{result.ai_analysis.executive_summary}</p>
                      </div>
                    )}

                    {result.ai_analysis.narrative && (
                      <div className="ayosa-result-card">
                        <div className="ayosa-result-label">AI Root Cause Narrative</div>
                        <p style={{ whiteSpace: "pre-wrap" }}>{result.ai_analysis.narrative}</p>
                      </div>
                    )}

                    {result.ai_analysis.root_cause_reasoning && (
                      <div className="ayosa-result-card">
                        <div className="ayosa-result-label">Root Cause Reasoning</div>
                        <p style={{ whiteSpace: "pre-wrap" }}>{result.ai_analysis.root_cause_reasoning}</p>
                      </div>
                    )}

                    {result.ai_analysis.risk_assessment && (
                      <div className="ayosa-result-card">
                        <div className="ayosa-result-label">Risk Assessment</div>
                        <div className="ayosa-risk-grid">
                          <div><strong>Severity:</strong> <span className={`ayosa-severity-badge ayosa-severity-${result.ai_analysis.risk_assessment.severity}`}>{result.ai_analysis.risk_assessment.severity}</span></div>
                          <div><strong>Blast Radius:</strong> {result.ai_analysis.risk_assessment.blast_radius}</div>
                          <div><strong>User Impact:</strong> {result.ai_analysis.risk_assessment.user_impact}</div>
                          {result.ai_analysis.risk_assessment.escalation_required && (
                            <div className="ayosa-escalation-flag">⚠️ Escalation Required</div>
                          )}
                        </div>
                      </div>
                    )}

                    {(result.ai_analysis.remediation_steps || []).length > 0 && (
                      <div className="ayosa-result-card">
                        <div className="ayosa-result-label">AI Remediation Steps</div>
                        <ol className="ayosa-remediation-list">
                          {result.ai_analysis.remediation_steps.map((step, i) => (
                            <li key={i} className="ayosa-remediation-item">
                              <div className="ayosa-remediation-action">{step.action}</div>
                              {step.tool && <div className="ayosa-remediation-meta"><strong>Tool:</strong> {step.tool}</div>}
                              {step.expected_outcome && <div className="ayosa-remediation-meta"><strong>Expected:</strong> {step.expected_outcome}</div>}
                              {step.effort && <span className="ayosa-effort-badge">{step.effort}</span>}
                            </li>
                          ))}
                        </ol>
                      </div>
                    )}

                    {(result.ai_analysis.follow_up_queries || []).length > 0 && (
                      <div className="ayosa-result-card">
                        <div className="ayosa-result-label">Follow-up Queries</div>
                        <ul className="ayosa-action-list">
                          {result.ai_analysis.follow_up_queries.map((q, i) => (
                            <li key={i}>{q}</li>
                          ))}
                        </ul>
                      </div>
                    )}

                    {(result.ai_analysis.signal_gaps || []).length > 0 && (
                      <div className="ayosa-result-card">
                        <div className="ayosa-result-label">Signal Gaps Identified by AI</div>
                        <ul className="ayosa-action-list">
                          {result.ai_analysis.signal_gaps.map((gap, i) => (
                            <li key={i}>{gap}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                )}

                {result.ai_analysis && result.ai_analysis.error && (
                  <div className="modal-alert modal-alert-error animate-in">
                    <span className="modal-alert-icon">✗</span>
                    <div>
                      <div className="modal-alert-title">AI Analysis Failed</div>
                      <div className="modal-alert-msg">{result.ai_analysis.error}</div>
                    </div>
                  </div>
                )}

                <div className="ayosa-result-card">
                  <div className="ayosa-result-label">Detected Patterns</div>
                  <div className="ayosa-pattern-list">
                    {(result.detected_patterns || []).map((pattern) => (
                      <span key={pattern}>{pattern}</span>
                    ))}
                  </div>
                </div>

                <div className="ayosa-result-card">
                  <div className="ayosa-result-label">Timeline</div>
                  <div className="ayosa-timeline">
                    {(result.timeline || []).map((item, index) => (
                      <div className="ayosa-timeline-item" key={`${item.timestamp}-${index}`}>
                        <div className="ayosa-timeline-top">
                          <strong>{item.source}</strong>
                          <span>{item.severity || "event"}</span>
                        </div>
                        <small>{item.timestamp}</small>
                        <p>{item.event}</p>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="ayosa-result-card">
                  <div className="ayosa-result-label">Suggested Actions</div>
                  <ul className="ayosa-action-list">
                    {(result.suggested_actions || []).map((action) => (
                      <li key={action}>{action}</li>
                    ))}
                  </ul>
                </div>

                <div className="ayosa-result-card">
                  <div className="ayosa-result-label">Runbook Generation</div>

                  <button
                    className="btn btn-violet"
                    onClick={handleGenerateRunbook}
                    disabled={runbookBusy}
                  >
                    {runbookBusy ? "Generating..." : "Generate Incident Runbook"}
                  </button>

                  {runbook && (
                    <>
                      <div className="ayosa-runbook-actions">
                        <button
                          className="btn btn-secondary btn-sm"
                          onClick={handleCopyRunbook}
                        >
                          Copy
                        </button>

                        <button
                          className="btn btn-secondary btn-sm"
                          onClick={handleDownloadMarkdown}
                        >
                          Download .md
                        </button>

                        <button
                          className="btn btn-secondary btn-sm"
                          onClick={handleDownloadText}
                        >
                          Download .txt
                        </button>
                      </div>

                      <pre className="ayosa-runbook">
                        {runbook}
                      </pre>
                    </>
                  )}
                </div>

                <div className="ayosa-result-card">
                  <div className="ayosa-result-label">Evidence</div>
                  {(result.evidence || []).map((item, index) => (
                    <details className="ayosa-evidence" key={`${item.source}-${index}`}>
                      <summary>
                        {item.source} · {item.signal} · {item.status}
                      </summary>
                      <p>{item.finding}</p>
                      {item.query && <pre>{item.query}</pre>}
                    </details>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>

        <div className="modal-footer">
          <button className="btn btn-secondary" onClick={onClose} disabled={busy}>
            Cancel
          </button>

          <button
            className="btn btn-violet btn-lg"
            onClick={handleRun}
            disabled={busy || tools.length === 0}
          >
            {running ? (
              <>
                <span className="spinner" /> Running AYOSA…
              </>
            ) : (
              `▶ Run AYOSA Investigation (${tools.length} tool${tools.length !== 1 ? "s" : ""})`
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

