/**
 * AyosaChatMessage — renders one full investigation exchange.
 * User message bubble → AYOSA structured response.
 */
import AyosaChartCard from "./AyosaChartCard";
import AyosaEvidenceCard from "./AyosaEvidenceCard";
import AyosaTimeline from "./AyosaTimeline";

export default function AyosaChatMessage({ userMessage, service, timeRange, result }) {
  const hasAi = !!result.ai_analysis && !result.ai_analysis.error;
  const hasLlm = !!result.llm_analysis && !result.llm_analysis.error;
  const executiveSummary =
    (hasLlm && result.llm_analysis.executive_summary) || result.answer;
  const confidence = Math.round((result.confidence || 0) * 100);
  const charts = (result.charts || []).filter((c) => (c.data || []).length > 0);
  const timeline = result.timeline || [];
  const evidence = result.evidence || [];

  return (
    <div className="ayosa-message">
      {/* ── User bubble ── */}
      <div className="ayosa-message-user">
        <div className="ayosa-message-user-bubble">
          <div className="ayosa-message-user-label">You asked</div>
          <div className="ayosa-message-user-text">{userMessage}</div>
          {(service || timeRange) && (
            <div className="ayosa-message-user-meta">
              {service && <span className="ayosa-meta-pill">📦 {service}</span>}
              {timeRange && <span className="ayosa-meta-pill">⏱ {timeRange}</span>}
            </div>
          )}
        </div>
      </div>

      {/* ── Assistant response ── */}
      <div className="ayosa-message-assistant">
        {/* Response header */}
        <div className="ayosa-response-header">
          <span className="ayosa-response-icon">🧠</span>
          <div>
            <div className="ayosa-response-title">AYOSA Response</div>
            <div className="ayosa-response-meta">
              Confidence: <strong>{confidence}%</strong>
              {hasAi && <span className="ayosa-ai-badge">✨ AI Enhanced</span>}
            </div>
          </div>
        </div>

        {/* Executive Summary */}
        <div className="ayosa-result-card ayosa-result-card-primary">
          <div className="ayosa-result-label">Executive Summary</div>
          <p>{executiveSummary}</p>
        </div>

        {/* Root Cause + Impact */}
        {(result.probable_root_cause || result.impact) && (
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

        {/* Signal Coverage */}
        {Object.keys(result.signal_coverage || {}).length > 0 && (
          <div className="ayosa-result-card">
            <div className="ayosa-result-label">Signal Coverage</div>
            <div className="ayosa-signal-grid">
              {Object.entries(result.signal_coverage).map(([signal, providers]) => (
                <div
                  key={signal}
                  className={`ayosa-signal-pill ${providers.length ? "available" : "missing"}`}
                >
                  <strong>{signal}</strong>
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

        {/* Charts */}
        {charts.length > 0 && (
          <div>
            <div className="ayosa-section-title">📈 Charts</div>
            <div className="ayosa-charts-grid">
              {charts.map((chart, i) => (
                <AyosaChartCard key={`${chart.source}-${chart.title}-${i}`} chart={chart} />
              ))}
            </div>
          </div>
        )}

        {/* Timeline */}
        {timeline.length > 0 && (
          <div>
            <div className="ayosa-section-title">🕐 Incident Timeline</div>
            <AyosaTimeline items={timeline} />
          </div>
        )}

        {/* Recommended Actions */}
        {((result.suggested_actions || []).length > 0 ||
          (hasLlm && (result.llm_analysis.recommended_next_steps || []).length > 0)) && (
          <div className="ayosa-result-card">
            <div className="ayosa-result-label">Recommended Actions</div>
            {(result.suggested_actions || []).length > 0 && (
              <ul className="ayosa-action-list">
                {result.suggested_actions.map((a, i) => (
                  <li key={i}>{a}</li>
                ))}
              </ul>
            )}
            {hasLlm && (result.llm_analysis.recommended_next_steps || []).length > 0 && (
              <>
                <div className="ayosa-result-label" style={{ marginTop: 12 }}>
                  ✨ AI Next Steps
                </div>
                <ul className="ayosa-action-list">
                  {result.llm_analysis.recommended_next_steps.map((s, i) => (
                    <li key={i}>{s}</li>
                  ))}
                </ul>
              </>
            )}
          </div>
        )}

        {/* Evidence */}
        {evidence.length > 0 && (
          <div className="ayosa-result-card">
            <div className="ayosa-result-label">
              Evidence ({evidence.length} item{evidence.length !== 1 ? "s" : ""})
            </div>
            {evidence.map((item, i) => (
              <AyosaEvidenceCard key={`${item.source}-${i}`} item={item} />
            ))}
          </div>
        )}

        {/* Focused LLM Analysis */}
        {hasLlm && (
          <div className="ayosa-ai-analysis animate-in">
            <div className="ayosa-ai-header">
              <span>🔍</span>
              <div>
                <div className="ayosa-ai-title">Focused AI Reasoning</div>
                <div className="ayosa-ai-meta">
                  {result.llm_analysis.provider}
                  {result.llm_analysis.model && ` · ${result.llm_analysis.model}`}
                  <span> · evidence-bounded</span>
                </div>
              </div>
            </div>
            {result.llm_analysis.reasoning && (
              <div className="ayosa-result-card">
                <div className="ayosa-result-label">Reasoning Chain</div>
                <p style={{ whiteSpace: "pre-wrap" }}>{result.llm_analysis.reasoning}</p>
              </div>
            )}
            {(result.llm_analysis.missing_information || []).length > 0 && (
              <div className="ayosa-result-card">
                <div className="ayosa-result-label">Information Gaps</div>
                <ul className="ayosa-action-list">
                  {result.llm_analysis.missing_information.map((m, i) => (
                    <li key={i}>{m}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {/* Deep AI Analysis — collapsible */}
        {hasAi && (
          <details className="ayosa-deep-ai-details">
            <summary className="ayosa-deep-ai-summary">
              <span>✨</span>
              <span>Deep AI Analysis</span>
              <span className="ayosa-ai-meta" style={{ marginLeft: "auto" }}>
                {result.ai_analysis.provider}
                {result.ai_analysis.model && ` · ${result.ai_analysis.model}`}
                {result.ai_analysis.root_cause_confidence && (
                  <span
                    className={`ayosa-confidence-badge ayosa-confidence-${result.ai_analysis.root_cause_confidence}`}
                    style={{ marginLeft: 6 }}
                  >
                    {result.ai_analysis.root_cause_confidence} confidence
                  </span>
                )}
              </span>
            </summary>

            <div className="ayosa-ai-analysis" style={{ marginTop: 0, borderTop: 0 }}>
              {result.ai_analysis.executive_summary && (
                <div className="ayosa-result-card ayosa-result-card-primary">
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
                    <div>
                      <strong>Severity: </strong>
                      <span
                        className={`ayosa-severity-badge ayosa-severity-${result.ai_analysis.risk_assessment.severity}`}
                      >
                        {result.ai_analysis.risk_assessment.severity}
                      </span>
                    </div>
                    <div><strong>Blast Radius: </strong>{result.ai_analysis.risk_assessment.blast_radius}</div>
                    <div><strong>User Impact: </strong>{result.ai_analysis.risk_assessment.user_impact}</div>
                    {result.ai_analysis.risk_assessment.escalation_required && (
                      <div className="ayosa-escalation-flag">⚠️ Escalation Required</div>
                    )}
                  </div>
                </div>
              )}
              {(result.ai_analysis.remediation_steps || []).length > 0 && (
                <div className="ayosa-result-card">
                  <div className="ayosa-result-label">Remediation Steps</div>
                  <ol className="ayosa-remediation-list">
                    {result.ai_analysis.remediation_steps.map((step, i) => (
                      <li key={i} className="ayosa-remediation-item">
                        <div className="ayosa-remediation-action">{step.action}</div>
                        {step.tool && (
                          <div className="ayosa-remediation-meta">
                            <strong>Tool: </strong>{step.tool}
                          </div>
                        )}
                        {step.expected_outcome && (
                          <div className="ayosa-remediation-meta">
                            <strong>Expected: </strong>{step.expected_outcome}
                          </div>
                        )}
                        {step.effort && (
                          <span className="ayosa-effort-badge">{step.effort}</span>
                        )}
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
                  <div className="ayosa-result-label">Signal Gaps</div>
                  <ul className="ayosa-action-list">
                    {result.ai_analysis.signal_gaps.map((g, i) => (
                      <li key={i}>{g}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </details>
        )}

        {/* AI error fallback */}
        {result.ai_analysis && result.ai_analysis.error && (
          <div className="modal-alert modal-alert-error animate-in">
            <span className="modal-alert-icon">✗</span>
            <div>
              <div className="modal-alert-title">AI Analysis Failed</div>
              <div className="modal-alert-msg">{result.ai_analysis.error}</div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
