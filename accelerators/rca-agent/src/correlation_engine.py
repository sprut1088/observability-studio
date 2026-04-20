"""
correlation_engine.py
──────────────────────
Analyses the CollectedSignals to surface anomalies and rank root-cause
candidates.  No external dependencies — pure Python statistical logic
against the normalised signal model produced by SignalCollector.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).parent.parent / "config"


# ─────────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AnomalyFinding:
    """A single detected anomaly in the signals."""
    source_tool: str
    category: str          # firing_alert | slow_trace | error_logs | unhealthy_target | resource
    severity: str          # critical | high | medium | low
    title: str
    detail: str
    service: str = ""
    metric_value: float = 0.0
    threshold: float = 0.0
    confidence: float = 1.0   # 0-1 score
    evidence: list[str] = field(default_factory=list)


@dataclass
class CorrelationResult:
    """Output of the correlation engine."""
    anomalies: list[AnomalyFinding]
    root_cause_candidates: list[AnomalyFinding]   # ranked by confidence
    affected_services: list[str]
    summary_text: str
    total_firing_alerts: int = 0
    total_error_logs: int = 0
    total_slow_traces: int = 0
    total_unhealthy_targets: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# CorrelationEngine
# ─────────────────────────────────────────────────────────────────────────────

class CorrelationEngine:
    """
    Correlates collected observability signals to identify root causes.

    Algorithm:
      1. Map each signal type to anomaly findings.
      2. Cross-correlate: if a service shows both high latency traces AND
         firing alerts AND error logs → boost confidence.
      3. Rank candidates by weighted confidence score.
      4. Return CorrelationResult with ranked root-cause candidates.
    """

    _SEVERITY_WEIGHT = {"critical": 1.0, "high": 0.8, "warning": 0.5, "medium": 0.5, "low": 0.3, "unknown": 0.2}
    _SLOW_TRACE_THRESHOLD_MS = 1000   # spans > 1 s considered slow
    _ERROR_LOG_BOOST = 0.15           # confidence boost per corroborating signal type

    def __init__(self):
        self.thresholds   = self._load_thresholds()
        self.corr_rules   = self._load_correlation_rules()

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def correlate(self, signals: Any) -> CorrelationResult:
        """
        Main entry: takes a CollectedSignals and returns CorrelationResult.
        """
        from signal_collector import CollectedSignals  # local to avoid circular import
        assert isinstance(signals, CollectedSignals)

        all_anomalies: list[AnomalyFinding] = []

        # ── 1. Firing alerts ──────────────────────────────────────────────────
        for alert in signals.firing_alerts:
            severity = alert.severity.lower()
            svc = (
                alert.labels.get("job")
                or alert.labels.get("service")
                or alert.labels.get("namespace")
                or ""
            )
            desc = alert.annotations.get("description") or alert.annotations.get("summary") or ""
            all_anomalies.append(AnomalyFinding(
                source_tool=alert.source_tool,
                category="firing_alert",
                severity=severity if severity in self._SEVERITY_WEIGHT else "medium",
                title=f"Firing alert: {alert.name}",
                detail=desc or f"Alert '{alert.name}' has been firing since {alert.starts_at}",
                service=svc,
                confidence=self._SEVERITY_WEIGHT.get(severity, 0.5),
                evidence=[f"Alert '{alert.name}' is firing", f"Labels: {alert.labels}"],
            ))

        # ── 2. Unhealthy scrape targets ───────────────────────────────────────
        for tgt in signals.scrape_targets:
            if tgt.get("health", "up") not in ("up", "ok", "unknown"):
                job = tgt.get("job", "unknown")
                err = tgt.get("error") or "unknown error"
                all_anomalies.append(AnomalyFinding(
                    source_tool="prometheus",
                    category="unhealthy_target",
                    severity="high",
                    title=f"Scrape target down: {job}",
                    detail=f"Prometheus cannot scrape {job} @ {tgt.get('instance', '')} — {err}",
                    service=job,
                    confidence=0.75,
                    evidence=[f"Target health: {tgt.get('health', 'down')}", f"Error: {err}"],
                ))

        # ── 3. Slow / errored traces (Jaeger) ─────────────────────────────────
        error_spans   = [t for t in signals.traces if t.status == "error"]
        slow_spans    = [t for t in signals.traces if t.duration_us / 1000 > self._SLOW_TRACE_THRESHOLD_MS]

        if error_spans:
            svc_errors: dict[str, int] = {}
            for span in error_spans:
                svc_errors[span.service_name] = svc_errors.get(span.service_name, 0) + 1
            worst_svc = max(svc_errors, key=svc_errors.__getitem__)
            all_anomalies.append(AnomalyFinding(
                source_tool="jaeger",
                category="slow_trace",
                severity="high",
                title=f"Error spans detected in distributed traces",
                detail=f"{len(error_spans)} error spans across {len(svc_errors)} services. "
                       f"Highest: {worst_svc} ({svc_errors[worst_svc]} errors)",
                service=worst_svc,
                metric_value=float(len(error_spans)),
                confidence=0.8,
                evidence=[f"Service '{s}': {c} error spans" for s, c in svc_errors.items()],
            ))

        if slow_spans:
            worst = max(slow_spans, key=lambda x: x.duration_us)
            avg_ms = sum(s.duration_us for s in slow_spans) / len(slow_spans) / 1000
            all_anomalies.append(AnomalyFinding(
                source_tool="jaeger",
                category="slow_trace",
                severity="medium",
                title=f"High-latency traces detected",
                detail=f"{len(slow_spans)} spans exceeded {self._SLOW_TRACE_THRESHOLD_MS} ms. "
                       f"Worst: {worst.service_name}/{worst.operation_name} "
                       f"({round(worst.duration_us / 1000, 1)} ms). Average: {round(avg_ms, 1)} ms",
                service=worst.service_name,
                metric_value=round(worst.duration_us / 1000, 1),
                threshold=float(self._SLOW_TRACE_THRESHOLD_MS),
                confidence=0.7,
                evidence=[
                    f"{s.service_name}/{s.operation_name}: {round(s.duration_us/1000,1)} ms"
                    for s in sorted(slow_spans, key=lambda x: x.duration_us, reverse=True)[:5]
                ],
            ))

        # ── 4. Error logs (OpenSearch / Elasticsearch) ────────────────────────
        if signals.error_logs:
            log_count = len(signals.error_logs)
            fatal_count = sum(1 for l in signals.error_logs if "FATAL" in l.level.upper())
            sample_msgs = list({l.message[:120] for l in signals.error_logs[:5]})
            all_anomalies.append(AnomalyFinding(
                source_tool="opensearch",
                category="error_logs",
                severity="critical" if fatal_count > 0 else "high",
                title=f"{log_count} error log entries in time window",
                detail=f"Found {log_count} error-level log entries "
                       f"({fatal_count} FATAL). Sample messages follow.",
                service="",
                metric_value=float(log_count),
                confidence=0.85 if fatal_count > 0 else 0.65,
                evidence=sample_msgs,
            ))

        # ── 5. Firing alert rules (not yet firing, but state="pending") ───────
        pending = [r for r in signals.alert_rules if r.get("state") == "pending"]
        if pending:
            names = [r["name"] for r in pending[:5]]
            all_anomalies.append(AnomalyFinding(
                source_tool="prometheus",
                category="firing_alert",
                severity="medium",
                title=f"{len(pending)} alert(s) in PENDING state",
                detail=f"These alerts are about to fire: {', '.join(names)}",
                service="",
                confidence=0.6,
                evidence=[f"Pending: {r['name']} ({r.get('expression','')})" for r in pending[:5]],
            ))

        # ── 6. Cross-correlate: boost confidence where multiple signals agree ─
        self._cross_correlate(all_anomalies, signals)

        # ── 7. Rank and split into root cause candidates ──────────────────────
        ranked = sorted(all_anomalies, key=lambda a: a.confidence, reverse=True)
        rc_candidates = ranked[:5]  # top 5 ranked candidates

        affected_services = list({
            a.service for a in all_anomalies if a.service
        })

        summary = self._build_summary(all_anomalies, signals)

        return CorrelationResult(
            anomalies=all_anomalies,
            root_cause_candidates=rc_candidates,
            affected_services=affected_services,
            summary_text=summary,
            total_firing_alerts=len(signals.firing_alerts),
            total_error_logs=len(signals.error_logs),
            total_slow_traces=len(slow_spans),
            total_unhealthy_targets=len([t for t in signals.scrape_targets
                                         if t.get("health") not in ("up", "ok", "unknown")]),
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _cross_correlate(self, anomalies: list[AnomalyFinding], signals: Any) -> None:
        """
        Boost confidence of anomalies that are corroborated by signals from
        multiple tools (e.g. Prometheus alert + Jaeger errors for same service).
        """
        service_signal_types: dict[str, set[str]] = {}
        for a in anomalies:
            if a.service:
                service_signal_types.setdefault(a.service, set()).add(a.category)

        for a in anomalies:
            if a.service and a.service in service_signal_types:
                types = service_signal_types[a.service]
                # Each additional corroborating signal type adds a boost
                extra_types = types - {a.category}
                boost = len(extra_types) * self._ERROR_LOG_BOOST
                a.confidence = min(1.0, a.confidence + boost)
                if extra_types:
                    a.evidence.append(
                        f"Corroborated by {len(extra_types)} additional signal type(s): "
                        + ", ".join(extra_types)
                    )

    def _build_summary(self, anomalies: list[AnomalyFinding], signals: Any) -> str:
        """Build a plain-text summary for the LLM context."""
        if not anomalies:
            return "No anomalies detected across the collected signals."

        lines = [f"Detected {len(anomalies)} anomalies across {len(signals.tools if hasattr(signals,'tools') else [])} tool(s):"]
        by_severity: dict[str, list[str]] = {}
        for a in anomalies:
            by_severity.setdefault(a.severity, []).append(a.title)
        for sev in ("critical", "high", "medium", "low"):
            items = by_severity.get(sev, [])
            if items:
                lines.append(f"  {sev.upper()}: " + "; ".join(items[:3]))

        return "\n".join(lines)

    def _load_thresholds(self) -> dict:
        path = _CONFIG_DIR / "thresholds.yaml"
        if path.exists():
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            return data.get("thresholds", {})
        return {}

    def _load_correlation_rules(self) -> dict:
        path = _CONFIG_DIR / "correlation_rules.yaml"
        if path.exists():
            with open(path) as f:
                return yaml.safe_load(f) or {}
        return {}
