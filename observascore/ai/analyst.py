"""AI-powered observability gap analyst.

Uses Claude (via the Anthropic SDK) to analyze an observability estate snapshot
and produce insights that go beyond deterministic rule checks:

  - Technical gaps: missing tools, anti-patterns, configuration debt
  - Functional gaps: user journey coverage, business KPI blindspots, on-call readiness
  - Trend alignment: how the stack compares to 2024-2025 industry standards
  - Prioritized recommendations ranked by business impact

The analyst is ADDITIVE — it does not replace the rules engine. It synthesizes
the deterministic findings and adds qualitative context an LLM is uniquely
suited to provide (narrative, trend awareness, cross-dimensional reasoning).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from observascore.engine import MaturityResult
from observascore.model import (
    AIAnalysis,
    AIInsight,
    ObservabilityEstate,
    SignalType,
    TrendAlignment,
)
from observascore.rules import Finding

logger = logging.getLogger(__name__)


class AIAnalystError(Exception):
    """Raised when the AI analysis fails unrecoverably."""


# ---------------------------------------------------------------------------
# System prompt — establishes the analyst persona
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are an elite Site Reliability Engineer and observability architect with 15+ years of experience. You have deep expertise in:

TOOLS & PLATFORMS:
- Open-source: Prometheus, Grafana, Loki, Tempo, Jaeger, AlertManager, Thanos, Cortex, VictoriaMetrics, OpenTelemetry Collector, Pyroscope, Grafana Alloy
- Commercial: Datadog, New Relic, Dynatrace, Elastic APM, Splunk, Honeycomb, Lightstep
- Security: Falco, Tetragon, SIEM integrations, runtime security

CURRENT TRENDS (2024-2025):
- OpenTelemetry as the universal instrumentation standard (CNCF Graduated)
- eBPF-based observability: zero-instrumentation telemetry via Cilium, Pixie, Odigos
- Continuous profiling: always-on CPU/memory profiling with Grafana Pyroscope, Parca
- Synthetic & active monitoring: Grafana k6, synthetic probes via Blackbox Exporter
- Cost observability: OpenCost, Kubecost, per-tenant/per-service cloud spend tracking
- Security observability: runtime security signals, audit log streams, CSPM integration
- Platform engineering: self-service observability golden paths, IDP integration
- Chaos engineering: steady-state hypothesis validation (LitmusChaos, Chaos Monkey)
- AIOps: ML-based anomaly detection, automated alert grouping, noise reduction
- DORA metrics: deployment frequency, lead time, MTTR, change failure rate instrumentation
- Business KPI alignment: custom SLIs tied to revenue, conversion, user experience
- OpenFeature / feature flag observability: correlating deploys/flag changes with signals
- Exemplars: linking metrics to traces to logs in a single click (Grafana exemplars)
- Continuous verification: SLO-based deployment gates, progressive delivery guardrails

SRE PRACTICES:
- Google SRE / SLO methodology, error budgets, burn-rate alerting
- Toil reduction, runbook automation, on-call health
- Incident management: paging hygiene, alert fatigue, MTTR optimization

Your analysis is precise, opinionated, and actionable. You identify gaps other tools miss. You speak to both engineering teams (technical depth) and leadership (business impact).

RESPONSE FORMAT: Respond ONLY with a valid JSON object. No markdown, no explanation outside the JSON. The JSON must exactly match the schema provided in the user message."""


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

def _build_context(
    estate: ObservabilityEstate,
    findings: list[Finding],
    result: MaturityResult,
) -> dict[str, Any]:
    """Distil the estate into a compact context dict for the LLM prompt."""

    # Signal counts
    signals_by_type: dict[str, int] = {}
    for s in estate.signals:
        signals_by_type[s.signal_type.value] = signals_by_type.get(s.signal_type.value, 0) + 1

    semantic_types: dict[str, int] = {}
    for s in estate.signals:
        if s.semantic_type:
            semantic_types[s.semantic_type] = semantic_types.get(s.semantic_type, 0) + 1

    # Alert portfolio summary
    severity_dist: dict[str, int] = {}
    for a in estate.alert_rules:
        sev = a.severity or "none"
        severity_dist[sev] = severity_dist.get(sev, 0) + 1

    classification_dist: dict[str, int] = {}
    for a in estate.alert_rules:
        c = a.classification.value
        classification_dist[c] = classification_dist.get(c, 0) + 1

    alerts_with_runbook = sum(1 for a in estate.alert_rules if a.runbook_url)
    alerts_with_description = sum(
        1 for a in estate.alert_rules
        if a.annotations.get("description") or a.annotations.get("summary")
    )

    # Dashboard summary
    dashboards_with_vars = sum(1 for d in estate.dashboards if d.has_templating)
    dashboards_with_tags = sum(1 for d in estate.dashboards if d.tags)
    folders = list({d.folder for d in estate.dashboards if d.folder and d.folder.lower() != "general"})

    # Service names for context
    service_names = list({s.name for s in estate.services})[:20]

    # Scrape target health
    targets_down = [t for t in estate.scrape_targets if t.health != "up"]
    unique_jobs = list({t.job for t in estate.scrape_targets})

    # Detect tool presence heuristics from signals and configured_tools
    has_otel_collector = "otel_collector" in estate.configured_tools
    has_tempo = "tempo" in estate.configured_tools or any(
        s.source_tool == "tempo" for s in estate.signals
    )
    has_alertmanager = "alertmanager" in estate.configured_tools
    has_elasticsearch = "elasticsearch" in estate.configured_tools
    has_profiling = any(
        kw in s.identifier.lower()
        for s in estate.signals
        for kw in ("pprof", "pyroscope", "profile", "profiling")
    )
    has_blackbox = any("blackbox" in t.job.lower() for t in estate.scrape_targets)
    has_service_mesh = any(
        kw in s.identifier.lower()
        for s in estate.signals
        for kw in ("envoy", "istio", "linkerd", "cilium", "consul")
    )
    has_security_signals = any(
        kw in s.identifier.lower()
        for s in estate.signals
        for kw in ("falco", "audit", "security", "vulnerability", "cve")
    )
    has_business_metrics = any(
        s.semantic_type == "business" for s in estate.signals
    ) or any(
        kw in s.identifier.lower()
        for s in estate.signals
        for kw in ("revenue", "conversion", "checkout", "order", "payment_success", "cart")
    )
    has_dora_metrics = any(
        kw in s.identifier.lower()
        for s in estate.signals
        for kw in ("deployment_frequency", "lead_time", "change_failure", "mttr")
    )
    has_cost_metrics = any(
        kw in s.identifier.lower()
        for s in estate.signals
        for kw in ("cost", "spend", "kubecost", "opencost")
    )
    has_otel_conventions = any(
        kw in (s.labels.get("service_name", "") + s.identifier)
        for s in estate.signals
        for kw in ("service.name", "deployment.environment", "service.version")
    ) or any("otel" in t.job.lower() for t in estate.scrape_targets)

    # Alert routing maturity
    receiver_types = []
    for ar in estate.alert_receivers:
        receiver_types.extend(ar.receiver_types)
    has_pagerduty_opsgenie = any(t in ("pagerduty", "opsgenie") for t in receiver_types)

    # Top findings (cap to keep prompt manageable)
    top_findings = sorted(
        findings,
        key=lambda f: {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}.get(f.severity, 5)
    )[:20]

    return {
        "client": {
            "name": estate.client_name,
            "environment": estate.environment,
            "assessment_timestamp": estate.timestamp,
        },
        "configured_tools": estate.configured_tools,
        "tool_inventory": {
            "prometheus": "prometheus" in estate.configured_tools,
            "grafana": "grafana" in estate.configured_tools,
            "loki": "loki" in estate.configured_tools,
            "jaeger": "jaeger" in estate.configured_tools,
            "alertmanager": has_alertmanager,
            "tempo": has_tempo,
            "elasticsearch": has_elasticsearch,
            "otel_collector": has_otel_collector,
        },
        "signal_coverage": {
            "metrics_count": signals_by_type.get("metric", 0),
            "log_labels": signals_by_type.get("log", 0),
            "traced_services": signals_by_type.get("trace", 0),
            "golden_signals_present": {
                "latency": semantic_types.get("latency", 0) > 0,
                "errors": semantic_types.get("error", 0) > 0,
                "traffic": semantic_types.get("traffic", 0) > 0,
                "saturation": semantic_types.get("saturation", 0) > 0,
            },
            "services_traced": service_names,
        },
        "scrape_targets": {
            "total": len(estate.scrape_targets),
            "down": len(targets_down),
            "jobs": unique_jobs[:30],
        },
        "alert_portfolio": {
            "total": len(estate.alert_rules),
            "recording_rules": len(estate.recording_rules),
            "severity_distribution": severity_dist,
            "classification_distribution": classification_dist,
            "runbook_coverage_pct": round(100 * alerts_with_runbook / max(len(estate.alert_rules), 1)),
            "description_coverage_pct": round(100 * alerts_with_description / max(len(estate.alert_rules), 1)),
            "has_pagerduty_or_opsgenie_routing": has_pagerduty_opsgenie,
            "alert_receivers": [
                {"name": ar.name, "types": ar.receiver_types} for ar in estate.alert_receivers
            ],
        },
        "dashboards": {
            "total": len(estate.dashboards),
            "with_template_variables": dashboards_with_vars,
            "with_ownership_tags": dashboards_with_tags,
            "custom_folders": folders[:10],
        },
        "datasources": [
            {"name": ds.name, "type": ds.ds_type} for ds in estate.datasources
        ],
        "maturity_scores": {
            "overall": round(result.overall_score, 1),
            "overall_level": result.overall_level,
            "overall_level_name": result.overall_level_name,
            "by_dimension": {
                d.dimension: {"score": round(d.score, 1), "level": d.level, "level_name": d.level_name}
                for d in result.dimension_scores
            },
        },
        "modern_stack_signals": {
            "otel_native_tracing": has_tempo,
            "otel_collector_pipeline": has_otel_collector,
            "otel_semantic_conventions": has_otel_conventions,
            "continuous_profiling": has_profiling,
            "synthetic_monitoring": has_blackbox,
            "service_mesh_telemetry": has_service_mesh,
            "security_observability": has_security_signals,
            "business_kpi_metrics": has_business_metrics,
            "dora_metrics": has_dora_metrics,
            "cost_observability": has_cost_metrics,
        },
        "top_deterministic_findings": [
            {
                "rule_id": f.rule_id,
                "dimension": f.dimension,
                "severity": f.severity,
                "title": f.title,
                "description": f.description,
            }
            for f in top_findings
        ],
        "extraction_errors": estate.summary.extraction_errors[:10],
    }


# ---------------------------------------------------------------------------
# Response schema for the LLM
# ---------------------------------------------------------------------------

_RESPONSE_SCHEMA = {
    "narrative": "string: 2-3 paragraph executive summary of the observability maturity. Mention the overall level, standout strengths, and most critical gaps. Write for a VP of Engineering audience — technical but business-grounded.",
    "technical_gaps": [
        {
            "title": "string: concise gap title",
            "description": "string: what is missing or wrong and why it matters technically",
            "severity": "string: critical|high|medium|low|info",
            "recommendation": "string: specific actionable fix with tool names and approach",
            "evidence": ["string: specific evidence from the estate data"]
        }
    ],
    "functional_gaps": [
        {
            "title": "string: concise gap title",
            "description": "string: what operational or business capability is absent",
            "severity": "string: critical|high|medium|low|info",
            "recommendation": "string: specific actionable fix",
            "evidence": ["string: evidence"]
        }
    ],
    "trend_alignments": [
        {
            "trend": "string: trend name",
            "status": "string: adopted|partial|absent",
            "impact": "string: high|medium|low",
            "description": "string: specific assessment of their alignment with this trend"
        }
    ],
    "prioritized_recommendations": [
        "string: recommendation 1 (most impactful first, 10-15 items)"
    ],
    "trend_score": "number: 0-100 score of how modern/aligned their stack is with 2024-2025 trends",
    "strengths": [
        "string: specific thing this estate does well (3-7 items)"
    ]
}


# ---------------------------------------------------------------------------
# Main analyst class
# ---------------------------------------------------------------------------

class ObservabilityAIAnalyst:
    """Calls Claude to produce qualitative gap analysis beyond deterministic rules."""

    def __init__(self, config: dict[str, Any]):
        try:
            import anthropic
        except ImportError as e:
            raise AIAnalystError(
                "anthropic package not installed. Run: pip install anthropic"
            ) from e

        api_key = config.get("api_key") or config.get("anthropic_api_key")
        if not api_key:
            raise AIAnalystError(
                "No Anthropic API key configured. Set ai.api_key in config."
            )

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = config.get("model", "claude-sonnet-4-6")
        self.max_tokens = config.get("max_tokens", 4096)
        self.temperature = config.get("temperature", 1.0)

    def analyze(
        self,
        estate: ObservabilityEstate,
        findings: list[Finding],
        result: MaturityResult,
    ) -> AIAnalysis:
        """Run AI analysis and return structured AIAnalysis."""
        logger.info("Running AI analysis with model %s ...", self.model)

        context = _build_context(estate, findings, result)
        user_message = self._build_user_message(context)

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
            raw_text = response.content[0].text
            logger.info("AI analysis complete (%d tokens used)", response.usage.output_tokens)
        except Exception as e:
            logger.error("AI analysis API call failed: %s", e)
            return self._error_analysis(str(e))

        try:
            parsed = self._parse_response(raw_text)
        except Exception as e:
            logger.error("Failed to parse AI response: %s", e)
            logger.debug("Raw AI response: %s", raw_text[:2000])
            return self._error_analysis(f"Response parse error: {e}")

        return self._build_analysis(parsed)

    def _build_user_message(self, context: dict[str, Any]) -> str:
        return f"""Analyze the following observability estate and produce a comprehensive gap analysis.

## ESTATE SNAPSHOT
```json
{json.dumps(context, indent=2)}
```

## REQUIRED RESPONSE SCHEMA
Respond ONLY with a JSON object matching this exact schema (no markdown fences, raw JSON only):
```json
{json.dumps(_RESPONSE_SCHEMA, indent=2)}
```

## ANALYSIS REQUIREMENTS

**Technical Gaps** — identify 5-10 specific technical gaps not captured by the deterministic findings. Focus on:
- Missing tools in the observability pipeline (instrumentation gaps, collection gaps)
- Anti-patterns in the current configuration
- Correlation and navigation blind spots
- Alerting and on-call tooling gaps
- Telemetry data quality issues

**Functional Gaps** — identify 5-8 operational capabilities this estate lacks:
- Incident response workflow gaps (no runbooks, no topology maps, no playbooks)
- On-call health concerns (alert fatigue risk, escalation paths)
- User journey observability (can they track a user request end-to-end?)
- Business KPI blindness (can the business answer "is our service making money?")
- Release safety (can they detect regressions during deploys?)

**Trend Alignments** — assess alignment with ALL of these 2024-2025 trends:
1. OpenTelemetry Adoption (instrumentation + collector + semantic conventions)
2. Continuous Profiling (Pyroscope, Parca, always-on profiling)
3. eBPF Observability (Cilium, Pixie, Odigos — zero-instrumentation approach)
4. Synthetic & Active Monitoring (end-user journey validation, uptime probes)
5. Chaos Engineering Readiness (steady-state hypotheses, fault injection tooling)
6. Cost Observability (cloud spend per service, OpenCost/Kubecost)
7. Security Observability (runtime security, audit trails, threat detection)
8. Business KPI Alignment (custom SLIs tied to revenue/user experience)
9. AI/ML Anomaly Detection (automated baselining, alert noise reduction)
10. Platform Engineering (self-service observability, golden path templates)
11. DORA Metrics Instrumentation (deployment frequency, MTTR, change failure rate)
12. Alert Fatigue Reduction (inhibition rules, notification policies, routing maturity)

**Recommendations** — prioritize by: (1) business risk reduction, (2) on-call quality improvement, (3) cost of implementation. Be specific (name tools, approaches, patterns).

**Trend Score** — score 0-100 reflecting modern stack alignment. 0=entirely legacy/reactive, 100=industry-leading. Justify in trend_alignments.

**Strengths** — acknowledge what they're doing well (critical for executive reports).

BE SPECIFIC. Do not give generic advice. Reference actual data from the estate (metric names, alert names, service names, tool configurations) wherever possible."""

    def _parse_response(self, raw: str) -> dict[str, Any]:
        """Extract JSON from the LLM response."""
        # Strip markdown fences if present
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last fence lines
            text = "\n".join(lines[1:] if lines[0].startswith("```") else lines)
            if text.endswith("```"):
                text = text[: text.rfind("```")]
        text = text.strip()
        return json.loads(text)

    def _build_analysis(self, data: dict[str, Any]) -> AIAnalysis:
        """Convert parsed LLM JSON into AIAnalysis dataclass."""
        technical_gaps = [
            AIInsight(
                category="technical_gap",
                title=g.get("title", ""),
                description=g.get("description", ""),
                severity=g.get("severity", "medium"),
                recommendation=g.get("recommendation", ""),
                evidence=g.get("evidence", []),
            )
            for g in data.get("technical_gaps", [])
        ]

        functional_gaps = [
            AIInsight(
                category="functional_gap",
                title=g.get("title", ""),
                description=g.get("description", ""),
                severity=g.get("severity", "medium"),
                recommendation=g.get("recommendation", ""),
                evidence=g.get("evidence", []),
            )
            for g in data.get("functional_gaps", [])
        ]

        trend_alignments = [
            TrendAlignment(
                trend=t.get("trend", ""),
                status=t.get("status", "absent"),
                impact=t.get("impact", "medium"),
                description=t.get("description", ""),
            )
            for t in data.get("trend_alignments", [])
        ]

        return AIAnalysis(
            narrative=data.get("narrative", ""),
            technical_gaps=technical_gaps,
            functional_gaps=functional_gaps,
            trend_alignments=trend_alignments,
            prioritized_recommendations=data.get("prioritized_recommendations", []),
            trend_score=float(data.get("trend_score", 0)),
            strengths=data.get("strengths", []),
            model_used=self.model,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    def _error_analysis(self, error_msg: str) -> AIAnalysis:
        """Return a minimal AIAnalysis indicating the analysis failed."""
        return AIAnalysis(
            narrative="AI analysis could not be completed. See error field for details.",
            technical_gaps=[],
            functional_gaps=[],
            trend_alignments=[],
            prioritized_recommendations=[],
            trend_score=0.0,
            strengths=[],
            model_used=self.model,
            generated_at=datetime.now(timezone.utc).isoformat(),
            error=error_msg,
        )
