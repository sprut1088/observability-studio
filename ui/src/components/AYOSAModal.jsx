import { useMemo, useState } from "react";
import { runAyosaInvestigation, generateAyosaRunbook } from "../api";
import AyosaChatMessage from "./AyosaChatMessage";
import AyosaIncidentSnapshot from "./AyosaIncidentSnapshot";
import AyosaChatShell from "./AyosaChatShell";

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
  const [message, setMessage] = useState("");
  const [service, setService] = useState("");
  const [timeRange, setTimeRange] = useState("30m");

  // AI configuration
  const [useAi, setUseAi] = useState(false);
  const [aiModeActive, setAiModeActive] = useState(false); // true = chat workspace
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

  // When AI toggle is turned off, exit chat workspace too
  function handleAiToggle(enabled) {
    setUseAi(enabled);
    if (!enabled) setAiModeActive(false);
  }

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
        className={`modal modal-wide${aiModeActive ? " ayosa-ai-modal-wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label="AYOSA"
      >
        <div className="modal-header modal-header-violet">
          <div className="modal-header-left">
            <span className="modal-icon">🧠</span>
            <div>
              <div className="modal-title">
                AYOSA
                {aiModeActive && (
                  <span className="ayosa-ai-mode-badge">✨ AI Chat</span>
                )}
              </div>
              <div className="modal-subtitle">
                {aiModeActive
                  ? "Conversational AI investigation workspace"
                  : "Ask Your Observability Stack Anything using globally validated tools."}
              </div>
            </div>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {aiModeActive && (
              <button
                className="btn btn-secondary btn-sm"
                onClick={() => setAiModeActive(false)}
                title="Return to form view"
              >
                ⚙ Settings
              </button>
            )}
            <button className="modal-close" onClick={onClose} aria-label="Close">
              ✕
            </button>
          </div>
        </div>

        {/* ── AI Chat Workspace ── */}
        {aiModeActive ? (
          <AyosaChatShell
            tools={tools}
            aiConfig={{
              provider:         aiProvider,
              apiKey:           aiApiKey,
              azureEndpoint,
              azureDeployment,
              openrouterModel,
              model:            null,
            }}
            onClose={onClose}
          />
        ) : (
          <>
        <div className="modal-body">
          <div className="mtool-add-bar" style={{ marginBottom: 12 }}>
            <div className="form-group mtool-add-url">
              <label className="form-label">Question</label>
              <input
                className="form-input"
                type="text"
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                placeholder="Ask me anything about your services…"
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
                placeholder="service name (optional)"
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
            onClick={() => !busy && handleAiToggle(!useAi)}
            style={{ marginTop: 16 }}
          >
            <div className="toggle-label">
              <span className="toggle-emoji">🤖</span>
              <div>
                <div className="toggle-title">Enable AI-Powered Analysis</div>
                <div className="toggle-desc">
                  Switch to a conversational AI workspace with intent classification, evidence-backed answers, and charts.
                </div>
              </div>
            </div>
            <label className="switch" onClick={(e) => e.stopPropagation()}>
              <input
                type="checkbox"
                checked={useAi}
                onChange={(e) => handleAiToggle(e.target.checked)}
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

              {/* Enter chat workspace button */}
              <div className="ayosa-enter-ai-bar animate-in">
                <div className="ayosa-enter-ai-hint">
                  Once your API key is set, enter the AI chat workspace for a conversational investigation experience.
                </div>
                <button
                  className="btn btn-violet"
                  onClick={() => setAiModeActive(true)}
                  disabled={!aiApiKey.trim() || tools.length === 0}
                >
                  Enter AI Chat Mode →
                </button>
              </div>
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
            <div className="ayosa-chat animate-in">
              <AyosaChatMessage
                userMessage={message}
                service={service}
                timeRange={timeRange}
                result={result}
              />
              <AyosaIncidentSnapshot
                snapshot={result.incident_snapshot}
                onGenerateRunbook={handleGenerateRunbook}
                runbookBusy={runbookBusy}
                runbook={runbook}
                onCopyRunbook={handleCopyRunbook}
                onDownloadMarkdown={handleDownloadMarkdown}
                onDownloadText={handleDownloadText}
              />
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
          </>
        )}
      </div>
    </div>
  );
}

