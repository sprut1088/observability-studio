"""
rca_agent.py
─────────────
Main RCA Agent orchestrator.

Flow:
  1. SignalCollector  → pull metrics, alerts, traces, logs from all tools
  2. CorrelationEngine → rank anomalies, produce root-cause candidates
  3. CascadeDetector   → map blast radius through service dependency graph
  4. LLMFormatter      → Claude generates narrative + renders HTML report

The agent is invoked directly by rca_service.py (no subprocess); it writes
the HTML report to disk and returns the path.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from signal_collector import CollectedSignals, SignalCollector
from correlation_engine import CorrelationEngine, CorrelationResult
from cascade_detector import CascadeDetector
from llm_formatter import LLMFormatter

logger = logging.getLogger(__name__)

RUNTIME_DIR = Path(__file__).resolve().parents[3] / "runtime"


class RCAAgent:
    """
    Orchestrates a full root-cause analysis run.

    Args:
        tools:      list[{tool_name, base_url, auth_token}]  — tool endpoints
        ai_config:  dict passed directly to LLMFormatter:
                      provider          – "anthropic" (default) | "azure"
                      api_key           – Anthropic or Azure API key
                      model             – Anthropic model name
                      azure_endpoint    – Azure endpoint URL
                      azure_deployment  – Azure deployment name
                      azure_api_version – Azure API version (default: 2024-02-01)

    Backwards-compatible kwargs (Anthropic-only, deprecated):
        api_key:    Anthropic API key
        model:      Claude model ID
    """

    def __init__(
        self,
        tools: list[dict[str, Any]],
        ai_config: dict[str, Any] | None = None,
        # legacy kwargs kept for backwards compat
        api_key: str | None = None,
        model: str = "claude-sonnet-4-6",
    ):
        self.tools      = tools
        self.collector  = SignalCollector(tools)
        self.correlator = CorrelationEngine()

        # Build effective AI config: explicit dict takes precedence over legacy kwargs
        if ai_config is not None:
            effective_config = ai_config
        else:
            effective_config = {
                "provider": "anthropic",
                "api_key":  api_key or "",
                "model":    model,
            }

        try:
            self.formatter = LLMFormatter(effective_config)
        except Exception as exc:
            # Non-fatal: formatter will fall back to deterministic RCA
            logger.warning("LLMFormatter init failed (%s) — AI narrative disabled", exc)
            self.formatter = None
            self._formatter_error = str(exc)
        else:
            self._formatter_error = None

    # ─────────────────────────────────────────────────────────────────────────
    # Public entry point
    # ─────────────────────────────────────────────────────────────────────────

    def run(
        self,
        incident: dict[str, Any],
        output_dir: Path | None = None,
    ) -> dict[str, Any]:
        """
        Execute a full RCA and write an HTML report to disk.

        incident: {
            service:              str   — primary service under investigation
            alert_name:           str   — alert or issue title
            description:          str   — free-text incident description
            time_window_minutes:  int   — look-back window (default 15)
        }

        Returns:
            {
                run_id:       str
                html_path:    Path
                rca_data:     dict   — structured RCA JSON
                signals:      CollectedSignals
                correlation:  CorrelationResult
                cascade:      dict
                success:      bool
                error:        str | None
            }
        """
        run_id = uuid.uuid4().hex
        if output_dir is None:
            output_dir = RUNTIME_DIR / run_id / "rca"
        output_dir.mkdir(parents=True, exist_ok=True)

        time_window = int(incident.get("time_window_minutes", 15))
        error_msg: str | None = None

        logger.info("[RCA %s] Starting analysis — service=%s window=%dm",
                    run_id, incident.get("service", "all"), time_window)

        # ── Step 1: Collect signals ───────────────────────────────────────────
        logger.info("[RCA %s] Collecting signals from %d tool(s)…", run_id, len(self.tools))
        try:
            signals: CollectedSignals = self.collector.collect_all(time_window)
        except Exception as exc:
            logger.error("[RCA %s] Signal collection failed: %s", run_id, exc)
            signals = CollectedSignals(collection_errors=[str(exc)])

        # ── Step 2: Correlate ─────────────────────────────────────────────────
        logger.info("[RCA %s] Correlating signals…", run_id)
        try:
            correlation: CorrelationResult = self.correlator.correlate(signals)
        except Exception as exc:
            logger.error("[RCA %s] Correlation failed: %s", run_id, exc)
            from correlation_engine import CorrelationResult as CR
            correlation = CR(
                anomalies=[], root_cause_candidates=[], affected_services=[],
                summary_text=f"Correlation error: {exc}",
            )

        # ── Step 3: Cascade detection ─────────────────────────────────────────
        logger.info("[RCA %s] Detecting cascade…", run_id)
        try:
            root_services = list({
                a.service for a in correlation.root_cause_candidates if a.service
            })
            if not root_services and incident.get("service"):
                root_services = [incident["service"]]

            detector = CascadeDetector()
            cascade = detector.detect_cascade(root_services, signals.services)
        except Exception as exc:
            logger.error("[RCA %s] Cascade detection failed: %s", run_id, exc)
            cascade = {
                "root_services": [], "direct_dependents": [],
                "indirect_dependents": [], "cascade_chain": [],
                "blast_radius": 0, "all_affected": [],
            }

        # ── Step 4: Build signals summary for LLM context ────────────────────
        signals_summary = _build_signals_summary(signals, correlation)

        # ── Step 5: LLM-powered RCA generation ───────────────────────────────
        provider_label = self.formatter.provider if self.formatter else "none"
        logger.info("[RCA %s] Generating RCA report (provider=%s)…", run_id, provider_label)

        if self.formatter is None:
            rca_data = _make_fallback_rca(
                incident, correlation, cascade,
                self._formatter_error or "LLMFormatter not initialised",
            )
            error_msg = self._formatter_error
        else:
            try:
                rca_data = self.formatter.generate_rca(
                    incident, signals_summary, correlation, cascade
                )
            except Exception as exc:
                logger.error("[RCA %s] LLM generation failed: %s", run_id, exc)
                rca_data = self.formatter._fallback_rca(incident, correlation, cascade, str(exc))
                error_msg = str(exc)

        # ── Step 6: Render HTML ───────────────────────────────────────────────
        logger.info("[RCA %s] Rendering HTML report…", run_id)
        try:
            if self.formatter is not None:
                html = self.formatter.render_html(
                    rca_data, incident, signals_summary, correlation,
                    cascade, signals.collection_errors,
                )
            else:
                html = _render_minimal_html(rca_data, incident)
            ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            html_path = output_dir / f"rca-report-{ts}.html"
            html_path.write_text(html, encoding="utf-8")
            logger.info("[RCA %s] Report written: %s", run_id, html_path)
        except Exception as exc:
            logger.error("[RCA %s] HTML write failed: %s", run_id, exc)
            html_path = output_dir / "rca-report-error.html"
            html_path.write_text(f"<pre>Report generation error: {exc}</pre>", encoding="utf-8")
            error_msg = error_msg or str(exc)

        return {
            "run_id":      run_id,
            "html_path":   html_path,
            "rca_data":    rca_data,
            "signals":     signals,
            "correlation": correlation,
            "cascade":     cascade,
            "success":     error_msg is None,
            "error":       error_msg,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Module-level helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_fallback_rca(
    incident: dict[str, Any],
    correlation: CorrelationResult,
    cascade: dict[str, Any],
    error: str,
) -> dict[str, Any]:
    """
    Build a deterministic RCA dict without calling any LLM.
    Used when LLMFormatter cannot be initialised (missing key, bad provider, etc.).
    """
    from datetime import datetime, timezone as _tz
    candidates = correlation.root_cause_candidates or []
    top = candidates[0] if candidates else None
    return {
        "executive_summary": (
            f"Automated analysis detected {correlation.total_firing_alerts} firing alert(s), "
            f"{correlation.total_error_logs} error log entries, and "
            f"{correlation.total_slow_traces} slow trace(s). "
            f"AI narrative generation was unavailable: {error}"
        ),
        "root_cause": {
            "title":       top.title if top else "Unknown — no anomalies detected",
            "description": top.detail if top else "No significant anomalies found in the time window.",
            "confidence":  round(top.confidence, 2) if top else 0.0,
            "category":    "unknown",
        },
        "contributing_factors": [
            {"factor": a.title, "description": a.detail, "severity": a.severity}
            for a in candidates[1:4]
        ],
        "impact_assessment": {
            "severity":              "P2" if correlation.total_firing_alerts > 0 else "P3",
            "affected_services":     cascade.get("all_affected", []),
            "blast_radius":          f"{cascade.get('blast_radius', 0)} services",
            "estimated_user_impact": "Unknown — manual review required",
            "business_impact":       "Unknown — manual review required",
        },
        "timeline":    [{"time": "T+0m", "event": "Incident signals collected by RCA Agent"}],
        "remediation": {
            "immediate_actions":      ["Review firing alerts and acknowledge in Alertmanager"],
            "short_term_fixes":       ["Investigate root cause service for recent deployments"],
            "long_term_improvements": ["Add runbooks to all firing alerts"],
        },
        "prevention":         ["Improve alert coverage for identified gap areas"],
        "observability_gaps": ["AI analysis unavailable — manual gap review required"],
        "model_used":         "none (fallback)",
        "provider":           "none",
        "generated_at":       datetime.now(_tz.utc).isoformat(),
        "ai_powered":         False,
        "error":              error,
    }


def _render_minimal_html(rca_data: dict[str, Any], incident: dict[str, Any]) -> str:
    """Bare-minimum HTML report when LLMFormatter is unavailable."""
    import json as _json
    summary  = rca_data.get("executive_summary", "No summary available.")
    rc_title = rca_data.get("root_cause", {}).get("title", "Unknown")
    svc      = incident.get("service", "unknown")
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>RCA Report — {svc}</title></head>"
        "<body style='font-family:sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem'>"
        f"<h1>RCA Report — {svc}</h1>"
        f"<p style='color:#d97706'>⚠ AI narrative unavailable — deterministic report only</p>"
        f"<h2>Executive Summary</h2><p>{summary}</p>"
        f"<h2>Root Cause</h2><p><strong>{rc_title}</strong></p>"
        f"<pre style='background:#f4f6fc;padding:1rem;border-radius:8px;overflow-x:auto'>"
        f"{_json.dumps(rca_data, indent=2)}</pre>"
        "</body></html>"
    )


def _build_signals_summary(signals: CollectedSignals, correlation: CorrelationResult) -> dict[str, Any]:
    """Build a compact, LLM-friendly summary of collected signals."""
    top_slow = signals.slow_traces[:5]
    sample_errors = [
        {"level": e.level, "message": e.message[:120], "index": e.index}
        for e in signals.error_logs[:5]
    ]
    sample_alerts = [
        {"name": a.name, "severity": a.severity, "service": a.labels.get("job", "")}
        for a in signals.firing_alerts[:10]
    ]
    unhealthy = [t for t in signals.scrape_targets if t.get("health") not in ("up", "ok", "unknown")]

    return {
        "firing_alert_count":     len(signals.firing_alerts),
        "firing_alerts_sample":   sample_alerts,
        "alert_rule_count":       len(signals.alert_rules),
        "recording_rule_count":   len(signals.recording_rules),
        "scrape_target_count":    len(signals.scrape_targets),
        "unhealthy_target_count": len(unhealthy),
        "unhealthy_targets":      unhealthy[:5],
        "trace_span_count":       len(signals.traces),
        "error_span_count":       sum(1 for t in signals.traces if t.status == "error"),
        "slow_traces_top5":       top_slow,
        "error_log_count":        len(signals.error_logs),
        "error_logs_sample":      sample_errors,
        "dashboard_count":        len(signals.dashboards),
        "service_count":          len(signals.services),
        "services":               signals.services[:20],
        "tool_latencies_ms":      signals.tool_latencies,
        "collection_errors":      signals.collection_errors,
        "collected_at":           signals.collected_at,
        "correlation_summary":    correlation.summary_text,
        "anomaly_count":          len(correlation.anomalies),
    }
