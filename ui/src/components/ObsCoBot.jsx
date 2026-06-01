import { useEffect, useRef, useState } from "react";
import { obscoChat, getFeatureFlags } from "../api";

/**
 * ObsCo — Observability Copilot.
 *
 * Floating chat widget rendered globally at the bottom-right of the viewport.
 * Lives entirely in this component and `api.obscoChat`; does not import from
 * or modify any other accelerator.
 *
 * Props:
 *   - validatedTools?: Array<{ tool: string, base_url?: string, auth_token?: string, validated?: boolean }>
 *       Passed through to the backend so ObsCo knows what's actually configured.
 */
export default function ObsCoBot({ validatedTools = [] }) {
  const [enabled, setEnabled] = useState(true); // feature flag
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [input, setInput] = useState("");
  const [aiKey, setAiKey] = useState("");
  const [aiEnabled, setAiEnabled] = useState(false);
  const [messages, setMessages] = useState([
    {
      role: "bot",
      text:
        "Hi, I'm **ObsCo** — your Observability Copilot. Ask me anything about " +
        "your configured tools (Prometheus, Grafana, Splunk, Datadog, Dynatrace, " +
        "Jaeger, Tempo, Loki, Elasticsearch, AppDynamics, Alertmanager, OpenSearch).",
    },
  ]);
  const threadRef = useRef(null);

  // Honour the obsco feature flag — hide entirely if disabled.
  useEffect(() => {
    getFeatureFlags()
      .then((res) => {
        const flag = res?.data?.obsco;
        if (flag === false) setEnabled(false);
      })
      .catch(() => {});
  }, []);

  // Auto-scroll the thread on new messages.
  useEffect(() => {
    if (threadRef.current) {
      threadRef.current.scrollTop = threadRef.current.scrollHeight;
    }
  }, [messages, open]);

  if (!enabled) return null;

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setBusy(true);
    setMessages((m) => [...m, { role: "user", text }]);
    setInput("");

    try {
      const tools = (validatedTools || [])
        .filter((t) => t && t.tool)
        .map((t) => ({
          tool: String(t.tool).toLowerCase(),
          base_url: t.base_url || null,
        }));

      const payload = {
        message: text,
        tools,
        ai: aiEnabled && aiKey
          ? { enabled: true, provider: "anthropic", api_key: aiKey }
          : null,
      };

      const res = await obscoChat(payload);
      const data = res.data || {};
      setMessages((m) => [
        ...m,
        {
          role: "bot",
          text: data.answer || "(no answer)",
          tool_facts: data.tool_facts || {},
          ai_used: !!data.ai_used,
        },
      ]);
    } catch (err) {
      const detail =
        err?.response?.data?.detail || err?.message || "Request failed.";
      setMessages((m) => [
        ...m,
        { role: "bot", text: `⚠️ ${detail}`, isError: true },
      ]);
    } finally {
      setBusy(false);
    }
  }

  function onKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  const configuredCount = (validatedTools || []).filter((t) => t?.validated).length;

  return (
    <>
      {/* Launcher button (always visible) */}
      {!open && (
        <button
          className="obsco-launcher"
          aria-label="Open Observability Copilot"
          onClick={() => setOpen(true)}
        >
          <span className="obsco-launcher-icon">💬</span>
          <span className="obsco-launcher-label">ObsCo</span>
          <span className="obsco-launcher-dot" />
        </button>
      )}

      {/* Chat panel */}
      {open && (
        <div className="obsco-panel" role="dialog" aria-label="ObsCo Observability Copilot">
          <div className="obsco-header">
            <div className="obsco-header-left">
              <span className="obsco-avatar">🤖</span>
              <div>
                <div className="obsco-title">ObsCo</div>
                <div className="obsco-subtitle">
                  Observability Copilot
                  {configuredCount > 0 && (
                    <span className="obsco-tool-pill">{configuredCount} tool{configuredCount === 1 ? "" : "s"}</span>
                  )}
                </div>
              </div>
            </div>
            <button
              className="obsco-close"
              aria-label="Close ObsCo"
              onClick={() => setOpen(false)}
            >
              ✕
            </button>
          </div>

          <div className="obsco-thread" ref={threadRef}>
            {messages.map((m, idx) => (
              <div
                key={idx}
                className={`obsco-msg obsco-msg-${m.role}${m.isError ? " obsco-msg-error" : ""}`}
              >
                {m.role === "bot" && <div className="obsco-msg-author">ObsCo{m.ai_used ? " · AI" : ""}</div>}
                <div className="obsco-msg-body">{m.text}</div>
                {m.role === "bot" && m.tool_facts && Object.keys(m.tool_facts).length > 0 && (
                  <div className="obsco-fact-chips">
                    {Object.entries(m.tool_facts).map(([key, f]) => (
                      <span key={key} className="obsco-fact-chip">
                        {f.display_name || key}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
            {busy && (
              <div className="obsco-msg obsco-msg-bot">
                <div className="obsco-msg-author">ObsCo</div>
                <div className="obsco-msg-body obsco-typing">
                  <span /><span /><span />
                </div>
              </div>
            )}
          </div>

          <div className="obsco-ai-row">
            <label className="obsco-ai-toggle">
              <input
                type="checkbox"
                checked={aiEnabled}
                onChange={(e) => setAiEnabled(e.target.checked)}
                disabled={busy}
              />
              <span>Enhance with Claude</span>
            </label>
            {aiEnabled && (
              <input
                type="password"
                placeholder="Anthropic API key (sk-ant-...)"
                value={aiKey}
                onChange={(e) => setAiKey(e.target.value)}
                disabled={busy}
                className="obsco-ai-key"
              />
            )}
          </div>

          <div className="obsco-input-row">
            <textarea
              className="obsco-input"
              rows={2}
              placeholder="Ask about Prometheus, Splunk, Grafana…"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              disabled={busy}
            />
            <button
              className="obsco-send"
              onClick={send}
              disabled={busy || !input.trim()}
              aria-label="Send"
            >
              ➤
            </button>
          </div>
        </div>
      )}
    </>
  );
}
