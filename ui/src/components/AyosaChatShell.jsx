/**
 * AyosaChatShell — full conversational AI investigation workspace.
 * Rendered inside AYOSAModal when AI mode is active.
 *
 * Layout:  sidebar  |  chat thread
 *                   |  sticky input bar
 */
import { useEffect, useRef, useState } from "react";
import { generateAyosaRunbook, runAyosaInvestigation } from "../api";
import AyosaChartCard from "./AyosaChartCard";
import AyosaEvidenceCard from "./AyosaEvidenceCard";
import AyosaIncidentSnapshot from "./AyosaIncidentSnapshot";
import AyosaTimeline from "./AyosaTimeline";

// ── Constants ────────────────────────────────────────────────────────

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

const INTENT_LABELS = {
  current_time:        "⏰ Time",
  general_chat:        "💬 General",
  health_check:        "💚 Health Check",
  service_investigation: "🔬 Investigation",
  last_error:          "🚨 Last Error",
  metrics_trend:       "📈 Metrics",
  logs_search:         "📋 Logs",
  alerts_check:        "🔔 Alerts",
  traces_check:        "🔍 Traces",
  runbook_request:     "📖 Runbook",
};

const SUGGESTED_PROMPTS = [
  "Investigate payment service health",
  "Show error trend for checkout in the last 1h",
  "Are there any active alerts?",
  "What was the last error for the auth service?",
  "Check latency for order-service",
];

// ── Helpers ──────────────────────────────────────────────────────────

function buildSteps(tools) {
  const steps = [
    { label: "Understanding question", icon: "🤔", status: "pending" },
    { label: "Selecting tools",        icon: "🔧", status: "pending" },
  ];
  tools.forEach((t) =>
    steps.push({
      label: `Querying ${t.toolName}`,
      icon:  TOOL_ICONS[t.toolName] || "🔍",
      status: "pending",
    })
  );
  steps.push(
    { label: "Correlating evidence", icon: "🔗", status: "pending" },
    { label: "Generating answer",    icon: "✍️",  status: "pending" },
  );
  return steps;
}

function downloadBlob(content, filename, type) {
  if (!content) return;
  const blob = new Blob([content], { type });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}

// ── Sub-components ───────────────────────────────────────────────────

function ToolSteps({ steps }) {
  return (
    <div className="ayosa-tool-steps">
      {steps.map((step, i) => (
        <div key={i} className={`ayosa-tool-step ayosa-step-${step.status}`}>
          <span className="ayosa-step-icon">
            {step.status === "done"    ? "✓"
             : step.status === "error" ? "✗"
             : step.icon}
          </span>
          <span className="ayosa-step-label">{step.label}</span>
          {step.status === "running" && <span className="spinner ayosa-step-spinner" />}
        </div>
      ))}
    </div>
  );
}

function AssistantMessage({ msg, onGenerateRunbook }) {
  const { status, steps = [], result, error } = msg;

  // ── Loading state ──
  if (status === "pending") {
    return (
      <div className="ayosa-message-assistant-wrap">
        <div className="ayosa-response-header">
          <span className="ayosa-response-icon">🧠</span>
          <div>
            <div className="ayosa-response-title">AYOSA is investigating…</div>
            <div className="ayosa-response-meta">Running tool queries</div>
          </div>
        </div>
        <ToolSteps steps={steps} />
      </div>
    );
  }

  // ── Error state ──
  if (status === "error") {
    return (
      <div className="ayosa-message-assistant-wrap">
        <details className="ayosa-steps-collapsed">
          <summary className="ayosa-steps-summary">
            {steps.filter((s) => s.status === "done").length} steps before error
          </summary>
          <ToolSteps steps={steps} />
        </details>
        <div className="modal-alert modal-alert-error animate-in">
          <span className="modal-alert-icon">✗</span>
          <div>
            <div className="modal-alert-title">Investigation failed</div>
            <div className="modal-alert-msg">{error}</div>
          </div>
        </div>
      </div>
    );
  }

  if (!result) return null;

  // ── Complete state ──
  const hasAi       = !!result.ai_analysis && !result.ai_analysis?.error;
  const hasLlm      = !!result.llm_analysis && !result.llm_analysis?.error;
  const summary     = (hasLlm && result.llm_analysis.executive_summary) || result.answer;
  const confidence  = Math.round((result.confidence || 0) * 100);
  const intentLabel = INTENT_LABELS[result.intent] || "🔬 Investigation";

  // Only show non-empty charts
  const allCharts     = result.charts || [];
  const charts        = allCharts.filter((c) => (c.data || []).length > 0);
  const hasEmptyCharts = allCharts.length > 0 && charts.length < allCharts.length;

  const timeline = result.timeline || [];
  const evidence = result.evidence || [];
  const isSimple = ["current_time", "general_chat"].includes(result.intent);

  return (
    <div className="ayosa-message-assistant-wrap">
      {/* Collapsed step summary */}
      <details className="ayosa-steps-collapsed">
        <summary className="ayosa-steps-summary">
          ✓ {steps.length} steps · {intentLabel}
        </summary>
        <ToolSteps steps={steps} />
      </details>

      {/* Response header */}
      <div className="ayosa-response-header">
        <span className="ayosa-response-icon">🧠</span>
        <div>
          <div className="ayosa-response-title">AYOSA Response</div>
          <div className="ayosa-response-meta">
            {intentLabel}
            {!isSimple && (
              <> · Confidence: <strong>{confidence}%</strong></>
            )}
            {hasAi && <span className="ayosa-ai-badge">✨ AI Enhanced</span>}
          </div>
        </div>
      </div>

      {/* Summary */}
      <div className="ayosa-result-card ayosa-result-card-primary">
        <div className="ayosa-result-label">Summary</div>
        <p>{summary}</p>
      </div>

      {/* Root Cause + Impact — investigation intents only */}
      {!isSimple && result.probable_root_cause && (
        <div className="ayosa-summary-grid">
          <div className="ayosa-result-card">
            <div className="ayosa-result-label">Root Cause</div>
            <p>{result.probable_root_cause}</p>
          </div>
          <div className="ayosa-result-card">
            <div className="ayosa-result-label">Impact</div>
            <p>{result.impact}</p>
          </div>
        </div>
      )}

      {/* Signal coverage */}
      {!isSimple && Object.keys(result.signal_coverage || {}).length > 0 && (
        <div className="ayosa-result-card">
          <div className="ayosa-result-label">Signal Coverage</div>
          <div className="ayosa-signal-grid">
            {Object.entries(result.signal_coverage).map(([sig, providers]) => (
              <div
                key={sig}
                className={`ayosa-signal-pill ${providers.length ? "available" : "missing"}`}
              >
                <strong>{sig}</strong>
                <span>{providers.length ? providers.join(", ") : "missing"}</span>
              </div>
            ))}
          </div>
          {(result.missing_signals || []).length > 0 && (
            <p className="ayosa-missing-note">
              Missing coverage: {result.missing_signals.join(", ")}.
            </p>
          )}
        </div>
      )}

      {/* Detected Patterns */}
      {(result.detected_patterns || []).length > 0 && (
        <div className="ayosa-result-card">
          <div className="ayosa-result-label">Detected Patterns</div>
          <div className="ayosa-pattern-list">
            {result.detected_patterns.map((p) => (
              <span key={p}>{p}</span>
            ))}
          </div>
        </div>
      )}

      {/* Charts — only rendered when data exists */}
      {charts.length > 0 && (
        <div>
          <div className="ayosa-section-title">📈 Metrics Charts</div>
          <div className="ayosa-charts-grid">
            {charts.map((chart, i) => (
              <AyosaChartCard key={`${chart.source}-${chart.title}-${i}`} chart={chart} />
            ))}
          </div>
        </div>
      )}
      {allCharts.length > 0 && charts.length === 0 && (
        <p className="ayosa-chart-note">
          Metrics charts unavailable — no valid time-series data was returned by the connected tools.
        </p>
      )}
      {hasEmptyCharts && charts.length > 0 && (
        <p className="ayosa-chart-note">
          Some metrics charts were omitted because no time-series data was available for them.
        </p>
      )}

      {/* Timeline */}
      {timeline.length > 0 && (
        <div>
          <div className="ayosa-section-title">🕐 Incident Timeline</div>
          <AyosaTimeline items={timeline} />
        </div>
      )}

      {/* Evidence — collapsible */}
      {evidence.length > 0 && (
        <details className="ayosa-evidence-section">
          <summary className="ayosa-section-title ayosa-evidence-summary">
            🔍 Evidence ({evidence.length} items — click to expand)
          </summary>
          <div className="ayosa-evidence-list">
            {evidence.map((item, i) => (
              <AyosaEvidenceCard key={`${item.source}-${i}`} item={item} />
            ))}
          </div>
        </details>
      )}

      {/* Suggested / AI actions */}
      {((result.suggested_actions || []).length > 0 ||
        (hasLlm && (result.llm_analysis.recommended_next_steps || []).length > 0)) && (
        <div className="ayosa-result-card">
          <div className="ayosa-result-label">Recommended Actions</div>
          <ul className="ayosa-action-list">
            {(result.suggested_actions || []).map((a, i) => <li key={`det-${i}`}>{a}</li>)}
            {hasLlm && (result.llm_analysis.recommended_next_steps || []).map((a, i) => (
              <li key={`llm-${i}`}>✨ {a}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Deep AI analysis — collapsible */}
      {hasAi && (
        <details className="ayosa-deep-ai-details">
          <summary className="ayosa-deep-ai-summary">
            ✨ Deep AI Analysis ({result.ai_analysis.provider})
          </summary>
          <div className="ayosa-ai-analysis">
            {result.ai_analysis.executive_summary && (
              <div className="ayosa-result-card">
                <div className="ayosa-result-label">AI Executive Summary</div>
                <p>{result.ai_analysis.executive_summary}</p>
              </div>
            )}
            {result.ai_analysis.narrative && (
              <div className="ayosa-result-card">
                <div className="ayosa-result-label">Narrative</div>
                <p style={{ whiteSpace: "pre-wrap" }}>{result.ai_analysis.narrative}</p>
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
          </div>
        </details>
      )}

      {/* Incident Snapshot */}
      {!isSimple && (
        <AyosaIncidentSnapshot
          snapshot={result.incident_snapshot}
          onGenerateRunbook={onGenerateRunbook}
          runbookBusy={msg.runbookBusy || false}
          runbook={msg.runbook || null}
          onCopyRunbook={() =>
            navigator.clipboard.writeText(msg.runbook || "").catch(() => {})
          }
          onDownloadMarkdown={() =>
            downloadBlob(
              msg.runbook,
              `ayosa-runbook-${result.service || "incident"}.md`,
              "text/markdown"
            )
          }
          onDownloadText={() =>
            downloadBlob(
              msg.runbook,
              `ayosa-runbook-${result.service || "incident"}.txt`,
              "text/plain"
            )
          }
        />
      )}
    </div>
  );
}

// ── Main component ───────────────────────────────────────────────────

export default function AyosaChatShell({ tools, aiConfig }) {
  const [messages, setMessages] = useState(() => [
    {
      id: "welcome",
      role: "system",
      content: `Hi! I'm AYOSA. I have access to ${tools.length} validated observability tool${tools.length !== 1 ? "s" : ""}. Ask me anything about your services.`,
    },
  ]);
  const [chatInput, setChatInput]     = useState("");
  const [service, setService]         = useState("");
  const [timeRange, setTimeRange]     = useState("30m");
  const [sessions, setSessions]       = useState([]);
  const [sessionTitle, setSessionTitle] = useState(null);

  const threadRef = useRef(null);
  const inputRef  = useRef(null);

  // Auto-scroll thread to bottom on new messages
  useEffect(() => {
    if (threadRef.current) {
      threadRef.current.scrollTop = threadRef.current.scrollHeight;
    }
  }, [messages]);

  const isRunning = messages.some(
    (m) => m.role === "assistant" && m.status === "pending"
  );

  // ── New chat ──
  function handleNewChat() {
    if (messages.length > 1) {
      const firstUser = messages.find((m) => m.role === "user");
      setSessions((prev) =>
        [
          {
            id: `s-${Date.now()}`,
            title: firstUser?.content?.slice(0, 42) || "Investigation",
            ts:    new Date(),
            msgs:  [...messages],
          },
          ...prev,
        ].slice(0, 12)
      );
    }
    setSessionTitle(null);
    setMessages([
      {
        id: "welcome",
        role: "system",
        content: `New chat. ${tools.length} tool${tools.length !== 1 ? "s" : ""} connected.`,
      },
    ]);
    inputRef.current?.focus();
  }

  function handleLoadSession(session) {
    setSessionTitle(session.title);
    setMessages(session.msgs);
  }

  // ── Runbook for a specific message ──
  async function handleGenerateRunbook(msgId, result) {
    setMessages((prev) =>
      prev.map((m) => (m.id === msgId ? { ...m, runbookBusy: true } : m))
    );
    try {
      const res = await generateAyosaRunbook({
        message:    result?.answer || "Investigate service",
        service:    result?.service || service || null,
        time_range: result?.time_range || timeRange,
        tools: tools.map((t) => ({
          tool:       t.toolName,
          base_url:   t.baseUrl,
          auth_token: t.authToken ?? null,
        })),
      });
      setMessages((prev) =>
        prev.map((m) =>
          m.id === msgId
            ? { ...m, runbookBusy: false, runbook: res.data.generated_runbook }
            : m
        )
      );
    } catch {
      setMessages((prev) =>
        prev.map((m) => (m.id === msgId ? { ...m, runbookBusy: false } : m))
      );
    }
  }

  // ── Send a message ──
  async function handleSend() {
    const input = chatInput.trim();
    if (!input || isRunning) return;

    const userMsgId      = `u-${Date.now()}`;
    const assistantMsgId = `a-${Date.now()}`;
    const steps          = buildSteps(tools);

    setChatInput("");
    setMessages((prev) => [
      ...prev,
      {
        id:        userMsgId,
        role:      "user",
        content:   input,
        timestamp: new Date(),
      },
      {
        id:           assistantMsgId,
        role:         "assistant",
        status:       "pending",
        steps:        [...steps],
        result:       null,
        error:        null,
        runbook:      null,
        runbookBusy:  false,
        timestamp:    new Date(),
      },
    ]);

    // Animate steps while API is in flight
    let stepIdx = 0;
    const tick = setInterval(() => {
      stepIdx++;
      setMessages((prev) =>
        prev.map((m) => {
          if (m.id !== assistantMsgId) return m;
          const updated = m.steps.map((s, i) => ({
            ...s,
            status:
              i < stepIdx - 1   ? "done"
              : i === stepIdx - 1 ? "running"
              : s.status,
          }));
          return { ...m, steps: updated };
        })
      );
      if (stepIdx >= steps.length) clearInterval(tick);
    }, 500);

    const payload = {
      message:    input,
      service:    service.trim() || null,
      time_range: timeRange.trim() || "30m",
      tools: tools.map((t) => ({
        tool:       t.toolName,
        base_url:   t.baseUrl,
        auth_token: t.authToken ?? null,
      })),
      ai: {
        enabled:          true,
        provider:         aiConfig.provider || "anthropic",
        api_key:          aiConfig.apiKey   || null,
        azure_endpoint:   aiConfig.azureEndpoint   || null,
        azure_deployment: aiConfig.azureDeployment || null,
        openrouter_model: aiConfig.openrouterModel || null,
      },
    };

    try {
      const res = await runAyosaInvestigation(payload);
      clearInterval(tick);
      setMessages((prev) =>
        prev.map((m) => {
          if (m.id !== assistantMsgId) return m;
          return {
            ...m,
            status: "complete",
            steps:  m.steps.map((s) => ({ ...s, status: "done" })),
            result: res.data,
          };
        })
      );
    } catch (err) {
      clearInterval(tick);
      setMessages((prev) =>
        prev.map((m) => {
          if (m.id !== assistantMsgId) return m;
          return {
            ...m,
            status: "error",
            steps:  m.steps.map((s, i) => ({
              ...s,
              status:
                i < stepIdx     ? "done"
                : i === stepIdx ? "error"
                : s.status,
            })),
            error: err?.response?.data?.detail || err.message || "Unknown error",
          };
        })
      );
    }
  }

  function handleKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  const providerLabel =
    aiConfig.provider === "azure"       ? "Azure OpenAI"
    : aiConfig.provider === "openrouter" ? (aiConfig.openrouterModel?.split("/")[1] || "OpenRouter")
    : "Claude";

  return (
    <div className="ayosa-chat-shell">
      {/* ── Sidebar ── */}
      <aside className="ayosa-chat-sidebar">
        <button
          className="ayosa-sidebar-new-chat"
          onClick={handleNewChat}
          disabled={isRunning}
        >
          + New Chat
        </button>

        {/* Connected tools */}
        <div className="ayosa-sidebar-section">Tools ({tools.length})</div>
        {tools.map((t) => (
          <div key={t.toolName} className="ayosa-sidebar-tool">
            <span>{TOOL_ICONS[t.toolName] || "🔧"}</span>
            <span>{t.toolName}</span>
          </div>
        ))}

        {/* Recent sessions */}
        {sessions.length > 0 && (
          <>
            <div className="ayosa-sidebar-section">Recent</div>
            {sessions.map((s) => (
              <button
                key={s.id}
                className={`ayosa-sidebar-session${sessionTitle === s.title ? " active" : ""}`}
                onClick={() => handleLoadSession(s)}
                title={new Date(s.ts).toLocaleString()}
              >
                {s.title}
              </button>
            ))}
          </>
        )}

        {/* Artifact shortcuts */}
        <div className="ayosa-sidebar-section">Artifacts</div>
        <div className="ayosa-sidebar-artifact">📋 Evidence</div>
        <div className="ayosa-sidebar-artifact">📈 Charts</div>
        <div className="ayosa-sidebar-artifact">🕐 Timeline</div>
        <div className="ayosa-sidebar-artifact">📖 Runbook</div>
      </aside>

      {/* ── Main area ── */}
      <div className="ayosa-chat-main">
        {/* Thread */}
        <div className="ayosa-chat-thread" ref={threadRef}>
          {messages.map((msg) => {
            if (msg.role === "system") {
              return (
                <div key={msg.id} className="ayosa-system-message">
                  🧠 {msg.content}
                </div>
              );
            }

            if (msg.role === "user") {
              return (
                <div key={msg.id} className="ayosa-message-user">
                  <div className="ayosa-message-user-bubble">
                    <div className="ayosa-message-user-label">You</div>
                    <div className="ayosa-message-user-text">{msg.content}</div>
                    {(service || timeRange) && (
                      <div className="ayosa-message-user-meta">
                        {service   && <span className="ayosa-meta-pill">📦 {service}</span>}
                        {timeRange && <span className="ayosa-meta-pill">⏱ {timeRange}</span>}
                      </div>
                    )}
                  </div>
                </div>
              );
            }

            if (msg.role === "assistant") {
              return (
                <div key={msg.id} className="ayosa-message">
                  <AssistantMessage
                    msg={msg}
                    onGenerateRunbook={() =>
                      handleGenerateRunbook(msg.id, msg.result)
                    }
                  />
                </div>
              );
            }

            return null;
          })}

          {/* Suggested prompts — only when thread is empty (just welcome) */}
          {messages.length === 1 && (
            <div className="ayosa-suggestions">
              <div className="ayosa-section-title">Try asking…</div>
              <div className="ayosa-suggestion-pills">
                {SUGGESTED_PROMPTS.map((p) => (
                  <button
                    key={p}
                    className="ayosa-suggestion-pill"
                    onClick={() => {
                      setChatInput(p);
                      inputRef.current?.focus();
                    }}
                  >
                    {p}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* ── Sticky Input Bar ── */}
        <div className="ayosa-chat-input-bar">
          <div className="ayosa-chat-input-row">
            <textarea
              ref={inputRef}
              className="ayosa-chat-textarea"
              placeholder="Ask AYOSA anything… e.g. Why is payment failing?"
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={isRunning}
              rows={2}
            />
            <button
              className="btn btn-violet ayosa-send-btn"
              onClick={handleSend}
              disabled={isRunning || !chatInput.trim()}
              title="Send (Enter)"
            >
              {isRunning ? <span className="spinner" /> : "▶ Send"}
            </button>
          </div>
          <div className="ayosa-chat-input-meta">
            <div className="ayosa-chat-input-controls">
              <span className="ayosa-input-label">Service</span>
              <input
                className="form-input ayosa-input-mini"
                type="text"
                value={service}
                onChange={(e) => setService(e.target.value)}
                placeholder="e.g. checkout"
                disabled={isRunning}
              />
              <span className="ayosa-input-label">Range</span>
              <input
                className="form-input ayosa-input-mini"
                type="text"
                value={timeRange}
                onChange={(e) => setTimeRange(e.target.value)}
                placeholder="30m"
                disabled={isRunning}
              />
            </div>
            <div className="ayosa-provider-badge">
              ✨ {providerLabel}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
