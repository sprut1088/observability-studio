/**
 * AyosaChatShell — full conversational AI investigation workspace.
 * Rendered inside AYOSAModal when AI mode is active.
 *
 * Layout:  sidebar  |  chat thread
 *                   |  sticky input bar
 */
import { useEffect, useRef, useState } from "react";
import { compareAyosaRuns, generateAyosaRunbook, streamAyosaInvestigation } from "../api";
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
  current_time:                   "⏰ Time",
  general_chat:                   "💬 General",
  service_health:                 "💚 Service Health",
  environment_health:             "🌍 Environment Health",
  latest_error:                   "🚨 Latest Error",
  error_trend:                    "📉 Error Trend",
  latency_trend:                  "📈 Latency Trend",
  active_alerts:                  "🔔 Active Alerts",
  trace_lookup:                   "🔍 Traces",
  dashboard_lookup:               "📊 Dashboards",
  incident_investigation:         "🔬 Incident Investigation",
  general_observability_question: "🔭 Observability",
  // legacy keys (backwards compat)
  health_check:          "💚 Health Check",
  service_investigation: "🔬 Investigation",
  last_error:            "🚨 Last Error",
  metrics_trend:         "📈 Metrics",
  logs_search:           "📋 Logs",
  alerts_check:          "🔔 Alerts",
  traces_check:          "🔍 Traces",
  runbook_request:       "📖 Runbook",
};

const SUGGESTED_PROMPTS = [
  "What is the health of my environment?",
  "Show error trend for the last 1h",
  "Are there any active alerts?",
  "What was the last error?",
  "Check latency trend for the past 30 minutes",
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
  // Step 14: group steps by `iteration` so users can see the LLM's
  // multi-pass investigation trajectory. Falls back to a flat render
  // when iteration metadata is absent (legacy single-pass results).
  const groups = new Map();
  for (const step of steps) {
    const it = Number.isFinite(step?.iteration) ? step.iteration : 0;
    if (!groups.has(it)) groups.set(it, []);
    groups.get(it).push(step);
  }
  const iterations = [...groups.keys()].sort((a, b) => a - b);
  const multi = iterations.length > 1;

  const renderStep = (step, i) => (
    <div
      key={`${step.iteration ?? 0}-${i}-${step.tool}`}
      className={`ayosa-tool-step ayosa-step-${step.status}${step.status === "skipped" ? " ayosa-step-skipped" : ""}`}
    >
      <span className="ayosa-step-icon">
        {step.status === "done"    ? "✓"
         : step.status === "error" ? "✗"
         : step.status === "skipped" ? "–"
         : step.icon}
      </span>
      <span className="ayosa-step-label">{step.label}</span>
      {step.status === "running" && <span className="spinner ayosa-step-spinner" />}
    </div>
  );

  if (!multi) {
    return (
      <div className="ayosa-tool-steps">
        {(groups.get(iterations[0]) || steps).map(renderStep)}
      </div>
    );
  }

  return (
    <div className="ayosa-tool-steps-grouped">
      {iterations.map((it) => (
        <div key={it} className="ayosa-tool-step-group">
          <div className="ayosa-tool-step-group-header">
            <span className="ayosa-tool-step-group-badge">
              {it === 0 ? "Pass 1 · Initial" : `Pass ${it + 1} · Re-plan`}
            </span>
            <span className="ayosa-tool-step-group-count">
              {groups.get(it).length} step{groups.get(it).length !== 1 ? "s" : ""}
            </span>
          </div>
          <div className="ayosa-tool-steps">
            {groups.get(it).map(renderStep)}
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Step 8: Prior runs card with per-row "Compare" affordance ─────────
function PriorRunsCard({ matches, scanned, currentRunId }) {
  const [activeCompare, setActiveCompare] = useState(null); // { runId }
  const [comparison, setComparison] = useState(null);       // backend payload
  const [compareError, setCompareError] = useState(null);
  const [comparing, setComparing] = useState(false);

  const handleCompare = async (runId) => {
    if (!currentRunId) {
      setCompareError("Current run hasn't been persisted yet — nothing to compare against.");
      setActiveCompare({ runId });
      setComparison(null);
      return;
    }
    if (runId === currentRunId) {
      setCompareError("That's the same run as the current one.");
      setActiveCompare({ runId });
      setComparison(null);
      return;
    }
    setActiveCompare({ runId });
    setComparing(true);
    setCompareError(null);
    setComparison(null);
    try {
      const { data } = await compareAyosaRuns(currentRunId, runId);
      setComparison(data);
    } catch (err) {
      const detail = err?.response?.data?.detail;
      const msg = typeof detail === "string"
        ? detail
        : detail?.message || err?.message || "Compare failed";
      setCompareError(msg);
    } finally {
      setComparing(false);
    }
  };

  return (
    <details className="ayosa-prior-runs-card" open={false}>
      <summary className="ayosa-prior-runs-summary">
        <span className="ayosa-prior-runs-icon">📚</span>
        <span className="ayosa-prior-runs-title">Prior investigations</span>
        <span className="ayosa-prior-runs-meta">
          {matches.length} related run{matches.length === 1 ? "" : "s"}
          {scanned ? ` · scanned ${scanned}` : ""}
        </span>
      </summary>
      <ul className="ayosa-prior-runs-list">
        {matches.map((r) => {
          const isActive = activeCompare?.runId === r.run_id;
          return (
            <li key={r.run_id} className="ayosa-prior-run">
              <div className="ayosa-prior-run-head">
                <span className="ayosa-prior-run-intent">
                  {INTENT_LABELS[r.intent] || r.intent || "investigation"}
                </span>
                {r.service && (
                  <span className="ayosa-prior-run-service">{r.service}</span>
                )}
                {r.time_range && (
                  <span className="ayosa-prior-run-time">{r.time_range}</span>
                )}
                <span
                  className="ayosa-prior-run-score"
                  title={`Matched on: ${(r.matched_on || []).join(", ") || "recency"}`}
                >
                  {r.score?.toFixed(2)}
                </span>
                <button
                  type="button"
                  className="ayosa-prior-run-compare-btn"
                  onClick={() => handleCompare(r.run_id)}
                  disabled={comparing && isActive}
                  title={
                    currentRunId
                      ? `Compare current run with ${r.run_id}`
                      : "Current run not yet persisted"
                  }
                >
                  {comparing && isActive ? "Comparing…" : "Compare"}
                </button>
              </div>
              {r.message && (
                <div className="ayosa-prior-run-message">{r.message}</div>
              )}
              <div className="ayosa-prior-run-foot">
                {(r.tools_used || []).slice(0, 6).map((t) => (
                  <span key={t} className="ayosa-prior-run-tool">
                    {TOOL_ICONS[t] || "🔧"} {t}
                  </span>
                ))}
                {typeof r.confidence === "number" && (
                  <span className="ayosa-prior-run-conf">
                    confidence {Math.round((r.confidence || 0) * 100)}%
                  </span>
                )}
                {r.created_at && (
                  <span className="ayosa-prior-run-when">{r.created_at}</span>
                )}
              </div>
              {isActive && (
                <RunComparisonPanel
                  comparing={comparing}
                  error={compareError}
                  comparison={comparison}
                  currentRunId={currentRunId}
                  otherRunId={r.run_id}
                  onClose={() => {
                    setActiveCompare(null);
                    setComparison(null);
                    setCompareError(null);
                  }}
                />
              )}
            </li>
          );
        })}
      </ul>
    </details>
  );
}

function RunComparisonPanel({ comparing, error, comparison, currentRunId, otherRunId, onClose }) {
  return (
    <div className="ayosa-compare-panel">
      <div className="ayosa-compare-head">
        <strong>Comparing</strong>
        <span className="ayosa-compare-ids">
          current ({currentRunId ? currentRunId.slice(0, 10) : "—"}…) ↔ {otherRunId.slice(0, 10)}…
        </span>
        <button
          type="button"
          className="ayosa-compare-close"
          onClick={onClose}
          aria-label="Close comparison"
        >
          ×
        </button>
      </div>
      {comparing && <div className="ayosa-compare-loading">Loading…</div>}
      {error && (
        <div className="ayosa-compare-error">{error}</div>
      )}
      {comparison && (() => {
        const diffs = comparison.differences || {};
        const trajDiff = diffs.trajectory || null;
        const snapDiff = diffs.snapshot || null;
        const keys = Object.keys(diffs).filter(
          (k) => k !== "snapshot" && k !== "trajectory"
        );
        if (keys.length === 0 && !snapDiff && !trajDiff) {
          return (
            <div className="ayosa-compare-empty">
              No differences across compared fields.
            </div>
          );
        }
        return (
          <>
            {(keys.length > 0 || snapDiff) && (
              <table className="ayosa-compare-table">
                <thead>
                  <tr>
                    <th>Field</th>
                    <th>Current</th>
                    <th>Prior</th>
                  </tr>
                </thead>
                <tbody>
                  {keys.map((k) => (
                    <tr key={k}>
                      <td className="ayosa-compare-field">{k}</td>
                      <td>{formatCompareValue(diffs[k]?.left)}</td>
                      <td>{formatCompareValue(diffs[k]?.right)}</td>
                    </tr>
                  ))}
                  {snapDiff && Object.keys(snapDiff).map((sk) => (
                    <tr key={`snap.${sk}`}>
                      <td className="ayosa-compare-field">snapshot.{sk}</td>
                      <td>{formatCompareValue(snapDiff[sk]?.left)}</td>
                      <td>{formatCompareValue(snapDiff[sk]?.right)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {trajDiff && <TrajectoryDiff diff={trajDiff} />}
          </>
        );
      })()}
    </div>
  );
}

// Step 17: render the per-iteration trajectory diff returned by the
// backend's `compare_runs` endpoint. `diff` can carry:
//   iterations          — {left, right}
//   passes              — {left: [{iteration, tools, statuses}], right: [...]}
//   new_tools_per_pass  — [{iteration, new_tools: [...]}]
//   replan_reason       — {left, right}
function TrajectoryDiff({ diff }) {
  const passesLeft  = diff?.passes?.left  || [];
  const passesRight = diff?.passes?.right || [];
  const newPerPass  = diff?.new_tools_per_pass || [];

  // Build a concise summary line: "Current: 1 pass · Prior: 2 passes — added alertmanager on pass 2"
  const iterDiff = diff?.iterations;
  const summaryBits = [];
  if (iterDiff) {
    const lp = iterDiff.left, rp = iterDiff.right;
    summaryBits.push(
      `Current: ${lp} pass${lp !== 1 ? "es" : ""} · Prior: ${rp} pass${rp !== 1 ? "es" : ""}`
    );
  }
  const addedFragments = newPerPass
    .filter((p) => (p.new_tools || []).length > 0)
    .map(
      (p) => `${p.new_tools.join(", ")} on pass ${p.iteration + 1}`
    );
  if (addedFragments.length > 0) {
    summaryBits.push(`added ${addedFragments.join("; ")}`);
  }

  const renderPasses = (passes, sideLabel) => (
    <div className="ayosa-compare-trajectory-side">
      <div className="ayosa-compare-trajectory-side-label">{sideLabel}</div>
      {passes.length === 0 ? (
        <div className="ayosa-compare-trajectory-empty">—</div>
      ) : (
        <div className="ayosa-tool-steps-grouped">
          {passes.map((p) => (
            <div key={p.iteration} className="ayosa-tool-step-group">
              <div className="ayosa-tool-step-group-header">
                <span className="ayosa-tool-step-group-badge">
                  {p.iteration === 0 ? "Pass 1 · Initial" : `Pass ${p.iteration + 1} · Re-plan`}
                </span>
                <span className="ayosa-tool-step-group-count">
                  {(p.tools || []).length} tool{(p.tools || []).length !== 1 ? "s" : ""}
                </span>
              </div>
              <div className="ayosa-tool-steps">
                {(p.tools || []).map((tool, i) => {
                  const status = (p.statuses || [])[i] || "done";
                  return (
                    <div
                      key={`${tool}-${i}`}
                      className={`ayosa-tool-step ayosa-step-${status}${status === "skipped" ? " ayosa-step-skipped" : ""}`}
                    >
                      <span className="ayosa-step-icon">
                        {status === "done"    ? "✓"
                         : status === "error" ? "✗"
                         : status === "skipped" ? "–"
                         : "•"}
                      </span>
                      <span className="ayosa-step-label">
                        {TOOL_ICONS[tool] || "🔧"} {tool}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );

  return (
    <div className="ayosa-compare-trajectory">
      <div className="ayosa-compare-trajectory-title">
        🔁 Investigation trajectory
      </div>
      {summaryBits.length > 0 && (
        <div className="ayosa-compare-trajectory-summary">
          {summaryBits.join(" — ")}
        </div>
      )}
      {diff?.replan_reason && (
        <div className="ayosa-compare-trajectory-reason">
          <span className="ayosa-compare-trajectory-reason-label">Re-plan reason:</span>{" "}
          current = <em>{diff.replan_reason.left || "—"}</em> · prior ={" "}
          <em>{diff.replan_reason.right || "—"}</em>
        </div>
      )}
      {/* Step 22: loop_summary delta — at-a-glance comparison of the two
          investigations' iteration loops (passes used / cap / replan flag). */}
      {diff?.loop_summary && (() => {
        const fmt = (ls) => {
          if (!ls) return "—";
          const ran = ls.iterations_run ?? "?";
          const cap = ls.max_iterations ?? "?";
          const flag = ls.replanned ? "re-planned" : "single-pass";
          return `${ran}/${cap} passes (${flag})`;
        };
        const left  = fmt(diff.loop_summary.left);
        const right = fmt(diff.loop_summary.right);
        return (
          <div className="ayosa-compare-trajectory-loop">
            <span className="ayosa-compare-trajectory-loop-label">Loop:</span>{" "}
            current = <strong>{left}</strong> · prior = <strong>{right}</strong>
          </div>
        );
      })()}
      {(passesLeft.length > 0 || passesRight.length > 0) && (
        <div className="ayosa-compare-trajectory-grid">
          {renderPasses(passesLeft,  "Current")}
          {renderPasses(passesRight, "Prior")}
        </div>
      )}
    </div>
  );
}

function formatCompareValue(v) {
  if (v == null) return <span className="ayosa-compare-null">—</span>;
  if (Array.isArray(v)) return v.length ? v.join(", ") : <span className="ayosa-compare-null">[]</span>;
  if (typeof v === "object") {
    try { return JSON.stringify(v); } catch { return String(v); }
  }
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(3);
  return String(v);
}

function AssistantMessage({ msg, onGenerateRunbook, showDebug = false }) {
  const {
    status,
    steps = [],
    result,
    error,
    streamingText = "",
    intentMeta,         // captured live from stream `intent` event
    replanReason,       // captured live from stream `replan` event
    replanIteration,
    workspaceContext,   // captured live from stream `workspace_context` event
    priorRuns,          // captured live from stream `prior_runs` event
    liveToolSteps = [], // Step 15: per-tool start/result events grouped by iteration
    maxIterationsLive,  // Step 19: cap captured from `session_start.max_iterations`
    loopSummary,        // Step 21: canonical loop telemetry from `loop_summary` event
  } = msg;

  // ── Loading state ──
  if (status === "pending") {
    // Derive a human-readable current action from the running step
    const runningStep = steps.find((s) => s.status === "running");
    const metaLabel   = runningStep ? runningStep.label : "Preparing…";
    // Step 15: prefer the live, iteration-aware trajectory once any tool has been
    // dispatched. The canned ``steps`` list is only meaningful before the agent
    // begins acting (it is a static "Understanding question / Selecting tools / …"
    // checklist that doesn't reflect re-plans).
    const useLiveTrajectory = liveToolSteps.length > 0;
    const livePassCount = useLiveTrajectory
      ? new Set(liveToolSteps.map((s) => s.iteration ?? 0)).size
      : 0;
    // Step 19: render "Pass N of up to M" when we know the cap and have at
    // least one pass under way; fall back to "🔄 N passes" for older streams
    // that don't emit ``max_iterations`` on ``session_start``.
    const knownCap = Number.isFinite(maxIterationsLive) && maxIterationsLive > 0
      ? maxIterationsLive
      : null;
    const showPassBadge = knownCap ? livePassCount >= 1 : livePassCount > 1;
    const passBadgeText = knownCap
      ? `🔄 Pass ${Math.max(livePassCount, 1)} of up to ${knownCap}`
      : `🔄 ${livePassCount} passes`;

    return (
      <div className="ayosa-message-assistant-wrap">
        <div className="ayosa-response-header">
          <span className="ayosa-response-icon">🧠</span>
          <div>
            <div className="ayosa-response-title">AYOSA is investigating…</div>
            <div className="ayosa-response-meta">
              {metaLabel}
              {intentMeta?.source && (
                <span className={`ayosa-intent-source ayosa-intent-source-${intentMeta.source}`}>
                  {intentMeta.source === "llm" ? "🤖 LLM"
                    : intentMeta.source === "fast_path" ? "⚡ Fast-path"
                    : intentMeta.source === "llm_fallback" ? "🤖→📖 LLM→KW"
                    : "📖 Keyword"}
                </span>
              )}
              {showPassBadge && (
                <span
                  className="ayosa-iter-badge"
                  title={replanReason || "Agent re-planned mid-investigation"}
                >
                  {passBadgeText}
                </span>
              )}
            </div>
          </div>
        </div>
        {replanReason && (
          <div className="ayosa-replan-banner">
            <span className="ayosa-replan-icon">🔄</span>
            <span>
              <strong>Re-planning (pass {(replanIteration || 1) + 1}):</strong>{" "}
              {replanReason}
            </span>
          </div>
        )}
        <ToolSteps steps={useLiveTrajectory ? liveToolSteps : steps} />
        {streamingText && (
          <div className="ayosa-stream-preview">
            <div className="ayosa-stream-preview-label">
              <span className="spinner ayosa-step-spinner" /> Generating AI analysis…
            </div>
            <pre className="ayosa-stream-preview-text">{streamingText}</pre>
          </div>
        )}
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
  const allCharts = result.charts || [];
  const charts    = allCharts.filter((c) => c && c.data && c.data.length > 0);

  const timeline = result.timeline || [];
  const evidence = result.evidence || [];
  const intent   = result.intent;
  const isSimple = ["current_time", "general_chat"].includes(intent);

  // Stage 4: intent-aware section gating.
  // List-style intents shouldn't surface a Root Cause/Timeline meant for incidents.
  const LIST_INTENTS    = ["healthy_services_list", "active_alerts", "latency_issues"];
  const INCIDENT_INTENTS = ["latest_error", "error_investigation", "service_health", "incident_investigation"];

  const showRootCause = !!result.probable_root_cause && !isSimple && !LIST_INTENTS.includes(intent);
  const showTimeline  = timeline.length > 0 && !isSimple && !LIST_INTENTS.includes(intent);
  const showPatterns  = (result.detected_patterns || []).length > 0
                        && !isSimple
                        && intent !== "latency_issues";

  // For list-style intents, evidence IS the answer — open it by default.
  const evidenceOpenByDefault = LIST_INTENTS.includes(intent)
                                || INCIDENT_INTENTS.includes(intent);

  return (
    <div className="ayosa-message-assistant-wrap">
      {/* Step 21: agent-debug strip (opt-in via sidebar toggle).
          Prefers the live ``loopSummary`` captured from the SSE event;
          falls back to the same fields embedded in ``result`` so the
          strip stays visible on re-rendered historical messages. */}
      {showDebug && (() => {
        const ls = loopSummary || result?.loop_summary;
        if (!ls) return null;
        const ran  = ls.iterations_run ?? result?.iterations ?? 1;
        const cap  = ls.max_iterations ?? result?.max_iterations ?? ran;
        const flag = ls.replanned ? "re-planned" : "single-pass";
        const reason = ls.replan_reason;
        return (
          <div className="ayosa-debug-strip" title="Agent loop telemetry (loop_summary)">
            <span className="ayosa-debug-strip-label">Loop:</span>
            <span className="ayosa-debug-strip-stat">{ran}/{cap} passes</span>
            <span className="ayosa-debug-strip-sep">·</span>
            <span className="ayosa-debug-strip-stat">{flag}</span>
            {reason && (
              <>
                <span className="ayosa-debug-strip-sep">·</span>
                <span className="ayosa-debug-strip-reason">reason: {reason}</span>
              </>
            )}
          </div>
        );
      })()}

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
            {result.intent_meta?.source && (
              <span
                className={`ayosa-intent-source ayosa-intent-source-${result.intent_meta.source}`}
                title={`Intent routed via ${result.intent_meta.source}`}
              >
                {result.intent_meta.source === "llm" ? "🤖 LLM"
                  : result.intent_meta.source === "fast_path" ? "⚡ Fast-path"
                  : result.intent_meta.source === "llm_fallback" ? "🤖→📖 LLM→KW"
                  : "📖 Keyword"}
              </span>
            )}
            {(result.iterations || 0) > 1 && (
              <span className="ayosa-iter-badge" title={result.replan_reason || "Agent re-planned mid-investigation"}>
                🔄 {result.iterations}
                {Number.isFinite(result.max_iterations) && result.max_iterations > 0
                  ? ` of ${result.max_iterations} passes`
                  : " passes"}
              </span>
            )}
            {!isSimple && (
              <> · Confidence: <strong>{confidence}%</strong></>
            )}
            {hasAi && <span className="ayosa-ai-badge">✨ AI Enhanced</span>}
          </div>
          {result.replan_reason && (
            <div className="ayosa-replan-reason">
              <span className="ayosa-replan-icon">🔄</span>
              <span>{result.replan_reason}</span>
            </div>
          )}
        </div>
      </div>

      {/* Summary */}
      <div className="ayosa-result-card ayosa-result-card-primary">
        <div className="ayosa-result-label">Summary</div>
        <p>{summary}</p>
      </div>

      {/* Query plan card — shows intent, selected tools, missing signals */}
      {!isSimple && result.plan && (
        <div className="ayosa-plan-card">
          <span className="ayosa-plan-intent">
            {INTENT_LABELS[result.plan.intent] || result.plan.intent}
          </span>
          {/* Step 8: selection mode badge — deterministic vs LLM-picked */}
          {result.plan.selection_meta && (
            <span
              className={`ayosa-selection-mode ayosa-selection-mode-${result.plan.selection_meta.mode}`}
              title={
                result.plan.selection_meta.mode === "llm"
                  ? `LLM tool selection (${result.plan.selection_meta.provider || "?"}${result.plan.selection_meta.model ? " · " + result.plan.selection_meta.model : ""})${result.plan.selection_meta.reasoning ? "\n\n" + result.plan.selection_meta.reasoning : ""}`
                  : result.plan.selection_meta.mode === "llm_iterative"
                  ? `LLM iterative re-plan (${result.plan.selection_meta.provider || "?"}${result.plan.selection_meta.model ? " · " + result.plan.selection_meta.model : ""})${result.plan.selection_meta.reasoning ? "\n\n" + result.plan.selection_meta.reasoning : ""}`
                  : "Deterministic registry-based tool selection"
              }
            >
              {result.plan.selection_meta.mode === "llm" ? "🤖 LLM-picked"
                : result.plan.selection_meta.mode === "llm_iterative" ? "🔁 LLM iterative"
                : "⚙️ Auto"}
            </span>
          )}
          {result.plan.selected_tools && result.plan.selected_tools.length > 0 && (
            <div className="ayosa-plan-tools">
              {result.plan.selected_tools.map((t) => (
                <span key={t} className="ayosa-plan-tool-pill">
                  {TOOL_ICONS[t] || "🔧"} {t}
                </span>
              ))}
            </div>
          )}
          {result.plan.missing_signals && result.plan.missing_signals.length > 0 && (
            <span className="ayosa-plan-missing">
              Missing: {result.plan.missing_signals.join(", ")}
            </span>
          )}
          {result.plan.selection_meta && result.plan.selection_meta.mode === "llm" && result.plan.selection_meta.reasoning && (
            <div className="ayosa-selection-reasoning">
              <span className="ayosa-selection-reasoning-label">Why these tools:</span>{" "}
              {result.plan.selection_meta.reasoning}
            </div>
          )}
        </div>
      )}

      {/* Step 4: Workspace knowledge card — known artifacts cited from index */}
      {!isSimple && (() => {
        const wsc = result.workspace_context || workspaceContext;
        if (!wsc || wsc.available === false) return null;
        const overview = wsc.overview || {};
        const counts = overview.counts || {};
        const services = overview.services || [];
        const matches = (wsc.matches && wsc.matches.results) || [];
        const svcCtx = wsc.service_context && wsc.service_context.context;
        const hasAnything = matches.length > 0 || services.length > 0 || svcCtx;
        if (!hasAnything) return null;
        return (
          <details className="ayosa-workspace-card" open={matches.length > 0}>
            <summary className="ayosa-workspace-summary">
              <span className="ayosa-workspace-icon">🗂️</span>
              <span className="ayosa-workspace-title">Workspace knowledge</span>
              <span className="ayosa-workspace-meta">
                {overview.total ? `${overview.total} indexed` : "indexed"}
                {matches.length > 0 && ` · ${matches.length} match${matches.length === 1 ? "" : "es"}`}
              </span>
            </summary>
            {matches.length > 0 && (
              <div className="ayosa-workspace-section">
                <div className="ayosa-workspace-section-label">Top matches</div>
                <ul className="ayosa-workspace-match-list">
                  {matches.slice(0, 6).map((m, i) => (
                    <li key={`${m.kind}-${m.name}-${i}`} className="ayosa-workspace-match">
                      <span className={`ayosa-workspace-kind ayosa-workspace-kind-${m.kind}`}>
                        {m.kind}
                      </span>
                      <span className="ayosa-workspace-name">{m.name}</span>
                      {m.service && (
                        <span className="ayosa-workspace-service">· {m.service}</span>
                      )}
                      <span className="ayosa-workspace-score" title="Token-overlap score">
                        {m.score?.toFixed(2)}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {svcCtx && (
              ["dashboards", "alerts", "metrics", "log_indexes", "traces", "owners"]
                .filter((k) => (svcCtx[k] || []).length > 0)
                .map((k) => (
                  <div key={k} className="ayosa-workspace-section">
                    <div className="ayosa-workspace-section-label">
                      {k.replace("_", " ")} for {svcCtx.service}
                    </div>
                    <div className="ayosa-workspace-pills">
                      {svcCtx[k].slice(0, 12).map((name) => (
                        <span key={name} className="ayosa-workspace-pill">{name}</span>
                      ))}
                    </div>
                  </div>
                ))
            )}
            {Object.keys(counts).length > 0 && (
              <div className="ayosa-workspace-counts">
                {Object.entries(counts).map(([k, v]) => (
                  <span key={k} className="ayosa-workspace-count">
                    {k}: <strong>{v}</strong>
                  </span>
                ))}
              </div>
            )}
          </details>
        );
      })()}

      {/* Step 6 + 8: Prior investigation runs — with compare affordance */}
      {!isSimple && (() => {
        const pr = result.prior_runs || priorRuns;
        if (!pr || pr.available === false) return null;
        const matches = pr.matches || [];
        if (matches.length === 0) return null;
        return (
          <PriorRunsCard
            matches={matches}
            scanned={pr.scanned}
            currentRunId={result.run_id}
          />
        );
      })()}

      {/* Root Cause + Impact — only when backend produced real RCA and intent is incident-like */}
      {showRootCause && (
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
      {showPatterns && (
        <div className="ayosa-result-card">
          <div className="ayosa-result-label">Detected Patterns</div>
          <div className="ayosa-pattern-list">
            {result.detected_patterns.map((p) => (
              <span key={p}>{p}</span>
            ))}
          </div>
        </div>
      )}

      {/* Charts — only rendered when data exists; never show empty-chart placeholders */}
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

      {/* Timeline — hidden for list-style intents to avoid unrelated noise */}
      {showTimeline && (
        <div>
          <div className="ayosa-section-title">🕐 Incident Timeline</div>
          <AyosaTimeline items={timeline} />
        </div>
      )}

      {/* Evidence — collapsible; default-open for list/incident intents where it IS the answer */}
      {evidence.length > 0 && (
        <details className="ayosa-evidence-section" open={evidenceOpenByDefault}>
          <summary className="ayosa-section-title ayosa-evidence-summary">
            🔍 Evidence ({evidence.length} items — click to {evidenceOpenByDefault ? "collapse" : "expand"})
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
  // Step 3b: Agent Mode opt-in. Persisted so power users don't toggle every reload.
  const [agentMode, setAgentMode] = useState(() => {
    try { return localStorage.getItem("ayosa.agentMode") === "1"; }
    catch { return false; }
  });
  useEffect(() => {
    try { localStorage.setItem("ayosa.agentMode", agentMode ? "1" : "0"); }
    catch { /* ignore quota / privacy errors */ }
  }, [agentMode]);

  // Step 19: per-request max_iterations override. Empty string → let the
  // backend pick its LLM-conditional default (1 without LLM, 4 with LLM).
  // Persisted so power users keep their preferred ceiling across reloads.
  const [maxIterationsOverride, setMaxIterationsOverride] = useState(() => {
    try { return localStorage.getItem("ayosa.maxIterations") || ""; }
    catch { return ""; }
  });
  useEffect(() => {
    try { localStorage.setItem("ayosa.maxIterations", maxIterationsOverride); }
    catch { /* ignore quota / privacy errors */ }
  }, [maxIterationsOverride]);

  // Step 21: opt-in agent-debug strip toggle. When on, the assistant
  // message renders a one-line summary of the canonical loop telemetry
  // emitted by the backend's ``loop_summary`` SSE event.
  const [showDebug, setShowDebug] = useState(() => {
    try { return localStorage.getItem("ayosa.showDebug") === "1"; }
    catch { return false; }
  });
  useEffect(() => {
    try { localStorage.setItem("ayosa.showDebug", showDebug ? "1" : "0"); }
    catch { /* ignore quota / privacy errors */ }
  }, [showDebug]);

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
        streamingText: "",
        liveToolSteps: [],
        runbook:      null,
        runbookBusy:  false,
        timestamp:    new Date(),
      },
    ]);

    const payload = {
      message:    input,
      service:    service.trim() || null,
      time_range: timeRange.trim() || "30m",
      agent_mode: agentMode,
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

    // Step 19: forward the optional per-request iteration cap. Only attach
    // when the user typed a positive integer; otherwise let the backend
    // resolve its own LLM-conditional default.
    {
      const trimmed = (maxIterationsOverride || "").trim();
      if (trimmed) {
        const n = Number.parseInt(trimmed, 10);
        if (Number.isFinite(n) && n > 0) {
          payload.max_iterations = n;
        }
      }
    }

    // Helper: update one field on the assistant message immutably
    const updateMsg = (fields) =>
      setMessages((prev) =>
        prev.map((m) => (m.id === assistantMsgId ? { ...m, ...fields } : m))
      );

    try {
      await streamAyosaInvestigation(payload, (event) => {
        if (event.type === "session_start") {
          // Step 19: backend resolves the per-request iteration cap (request
          // override → LLM-conditional default) and echoes it on session_start
          // so the live trajectory badge can render "Pass N of up to M".
          if (Number.isFinite(event.max_iterations) && event.max_iterations > 0) {
            updateMsg({ maxIterationsLive: event.max_iterations });
          }
        } else if (event.type === "plan") {
          // Store the plan on the pending message so we can show it in the UI
          updateMsg({ plan: event.data });
        } else if (event.type === "intent") {
          // Step 3: capture intent + routing source so we can show a badge while the
          // investigation is still running (final result will also carry intent_meta).
          updateMsg({ intentMeta: event.meta || null, intent: event.intent });
        } else if (event.type === "replan") {
          // Step 3: agent re-planned mid-investigation. Show a transient banner so
          // the user understands why extra tools are being queried.
          updateMsg({
            replanIteration: event.iteration,
            replanReason:    event.reason,
          });
        } else if (event.type === "step") {
          // Update the specific step by index with the real status from the backend
          setMessages((prev) =>
            prev.map((m) => {
              if (m.id !== assistantMsgId) return m;
              const updatedSteps = m.steps.map((s, i) =>
                i === event.index ? { ...s, status: event.status } : s
              );
              return { ...m, steps: updatedSteps };
            })
          );
        } else if (event.type === "tool_start") {
          // Step 15: live trajectory — append a running entry tagged with the
          // current iteration so the assistant bubble groups by pass while the
          // agent is still acting.
          setMessages((prev) =>
            prev.map((m) => {
              if (m.id !== assistantMsgId) return m;
              const next = [
                ...(m.liveToolSteps || []),
                {
                  index:     event.index,
                  tool:      event.tool,
                  label:     event.label || `Querying ${event.tool}`,
                  status:    "running",
                  iteration: event.iteration ?? 0,
                  icon:      "🔍",
                },
              ];
              return { ...m, liveToolSteps: next };
            })
          );
        } else if (event.type === "tool_result") {
          // Step 15: mark the matching live entry done/error. Match by
          // (iteration, index, tool) so re-plans on the same tool don't collide
          // with the first pass.
          setMessages((prev) =>
            prev.map((m) => {
              if (m.id !== assistantMsgId) return m;
              const iter = event.iteration ?? 0;
              const live = (m.liveToolSteps || []).map((s) =>
                s.iteration === iter && s.index === event.index && s.tool === event.tool
                  ? { ...s, status: event.status || "done", error: event.error || null }
                  : s
              );
              return { ...m, liveToolSteps: live };
            })
          );
        } else if (event.type === "llm_chunk") {
          // Append streaming LLM text so the user can see it being generated
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMsgId
                ? { ...m, streamingText: (m.streamingText || "") + event.text }
                : m
            )
          );
        } else if (event.type === "workspace_context") {
          // Step 4: surface what the workspace index returned for this turn.
          updateMsg({ workspaceContext: event.data });
        } else if (event.type === "prior_runs") {
          // Step 6: surface related prior investigation runs persisted to SQLite.
          updateMsg({ priorRuns: event.data });
        } else if (event.type === "loop_summary") {
          // Step 21: canonical iteration-loop telemetry. We capture it from the
          // dedicated SSE event so the debug strip can render immediately
          // without waiting for ``final_snapshot`` to land.
          updateMsg({
            loopSummary: {
              iterations_run: event.iterations_run,
              max_iterations: event.max_iterations,
              replanned:      event.replanned,
              replan_reason:  event.replan_reason,
            },
          });
        } else if (event.type === "result" || event.type === "final_snapshot") {
          // `result` = legacy stream, `final_snapshot` = agent-mode stream.
          updateMsg({
            status:       "complete",
            steps:        steps.map((s) => ({ ...s, status: "done" })),
            result:       event.data,
            streamingText: "",
          });
        } else if (event.type === "done") {
          // Agent-mode emits a terminal `done` after `final_snapshot`. Ensure
          // the message is marked complete even if `final_snapshot` was skipped
          // (e.g. tool-less intents).
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMsgId && m.status !== "complete"
                ? { ...m, status: "complete", streamingText: "" }
                : m
            )
          );
        } else if (event.type === "error") {
          updateMsg({
            status: "error",
            error:  event.message || "Unknown error",
          });
        }
      });
    } catch (err) {
      updateMsg({
        status: "error",
        steps:  steps.map((s) => ({ ...s, status: s.status === "running" ? "error" : s.status })),
        error:  err?.message || "Unknown error",
      });
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

        {/* Step 3b: Agent Mode toggle ----------------------------- */}
        <div className="ayosa-sidebar-section">Mode</div>
        <label
          className={`ayosa-agent-toggle${agentMode ? " on" : ""}${isRunning ? " disabled" : ""}`}
          title={
            agentMode
              ? "Agent mode: LLM intent routing + iterative re-plan"
              : "Legacy deterministic mode"
          }
        >
          <input
            type="checkbox"
            checked={agentMode}
            disabled={isRunning}
            onChange={(e) => setAgentMode(e.target.checked)}
          />
          <span className="ayosa-agent-toggle-track">
            <span className="ayosa-agent-toggle-thumb" />
          </span>
          <span className="ayosa-agent-toggle-label">
            🤖 Agent Mode
            {agentMode && <span className="ayosa-agent-toggle-on">ON</span>}
          </span>
        </label>

        {/* Step 19: optional per-request iteration cap override.
            Empty input → backend picks default (1 without LLM, 4 with LLM). */}
        <label
          className={`ayosa-max-iter-field${isRunning ? " disabled" : ""}`}
          title="Override the agent loop iteration cap. Leave blank for the LLM-conditional default (1 without LLM, 4 with LLM)."
        >
          <span className="ayosa-max-iter-label">🔁 Max passes</span>
          <input
            type="number"
            min={1}
            max={8}
            step={1}
            inputMode="numeric"
            placeholder="auto"
            value={maxIterationsOverride}
            disabled={isRunning}
            onChange={(e) => setMaxIterationsOverride(e.target.value)}
            className="ayosa-max-iter-input"
          />
        </label>

        {/* Step 21: Show agent debug strip toggle. Reveals the canonical
            loop_summary telemetry under each completed assistant message. */}
        <label
          className={`ayosa-debug-toggle${showDebug ? " on" : ""}`}
          title="Show the agent's iteration loop telemetry (loop_summary) under each completed response."
        >
          <input
            type="checkbox"
            checked={showDebug}
            onChange={(e) => setShowDebug(e.target.checked)}
          />
          <span className="ayosa-debug-toggle-label">
            🐞 Show agent debug
            {showDebug && <span className="ayosa-debug-toggle-on">ON</span>}
          </span>
        </label>

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
                    showDebug={showDebug}
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
              placeholder="Ask AYOSA anything… e.g. What is the health of my environment?"
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
