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
import os
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


_ADVISOR_SYSTEM_PROMPT = """You are an elite Site Reliability Engineer and observability architect advising leadership and service owners.

Your job is to explain deterministic ObservaScore findings in plain, executive-ready language.

Hard rules:
- Use only the provided estate snapshot and deterministic findings.
- Do not invent tools, alerts, metrics, owners, incidents, or scores.
- Do not output JSON.
- Do not output markdown tables.
- Do not mention parsing, malformed JSON, API keys, or implementation details.
- Keep the response concise and suitable for a VP of Engineering / SRE leadership report.

Return 2 to 4 short paragraphs covering:
1. Current reliability and observability posture.
2. The highest-risk gaps and why they matter operationally.
3. The recommended leadership action plan for the next 30 days.
"""


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
    """Calls Claude or Azure OpenAI to produce qualitative gap analysis beyond deterministic rules."""

    def __init__(self, config: dict[str, Any]):
        """
        config keys:
          - provider: "anthropic" (default) or "azure" / "azure_openai"
          - api_key / anthropic_api_key / azure_api_key
          - model (Anthropic model name) or deployment (Azure deployment name)
          - api_base (required for Azure)
          - api_version (optional for Azure, default "2023-05-15")
          - max_tokens, temperature
        """
        self.provider = (config.get("provider") or "anthropic").strip().lower()
        self.max_tokens = config.get("max_tokens", 4096)
        #self.temperature = config.get("temperature", 1.0)
        self.temperature = config.get("temperature", 0.2)
        # Bug fix: use `or` so that an explicit null/None value falls back to
        # the default rather than being passed as-is to the SDK.
        self.model = config.get("model") or "claude-sonnet-4-6"

        if self.provider == "anthropic":
            try:
                import anthropic  # type: ignore
            except ImportError as e:
                raise AIAnalystError(
                    "anthropic package not installed. Run: pip install anthropic"
                ) from e

            api_key = (
                config.get("api_key")
                or config.get("anthropic_api_key")
                or os.environ.get("ANTHROPIC_API_KEY", "")
            )
            if not api_key:
                raise AIAnalystError(
                    "No Anthropic API key provided. Pass ai.api_key in config "
                    "or set the ANTHROPIC_API_KEY environment variable."
                )

            self.client = anthropic.Anthropic(api_key=api_key)
            # keep self.model as Anthropic model name

        elif self.provider in ("azure", "azure_openai", "openai_azure"):
            try:
                from openai import AzureOpenAI  # type: ignore
            except ImportError as e:
                raise AIAnalystError(
                    "openai package not installed. Run: pip install openai>=1.0"
                ) from e

            api_key = (
                config.get("api_key")
                or config.get("azure_api_key")
                or os.environ.get("AZURE_OPENAI_API_KEY", "")
            )
            # Accept both the legacy "api_base" key and the schema field "azure_endpoint"
            api_base = (
                config.get("api_base")
                or config.get("azure_endpoint")
                or os.environ.get("AZURE_OPENAI_ENDPOINT", "")
            )
            if not api_key or not api_base:
                raise AIAnalystError(
                    "Azure OpenAI requires api_key and azure_endpoint. "
                    "Pass them in config or set AZURE_OPENAI_API_KEY / AZURE_OPENAI_ENDPOINT env vars."
                )

            api_version = config.get("api_version", "2024-02-01")
            self.client = AzureOpenAI(
                api_key=api_key,
                azure_endpoint=api_base,
                api_version=api_version,
            )
            # For Azure, use deployment name as model identifier if provided.
            # Accept both "deployment" (legacy CLI config key) and "azure_deployment"
            # (the schema field name sent from the UI).
            self.model = (
                config.get("deployment")
                or config.get("azure_deployment")
                or config.get("model")
                or "gpt-4o"
            )

        else:
            raise AIAnalystError(f"Unsupported AI provider: {self.provider}")

    def analyze(
        self,
        estate: ObservabilityEstate,
        findings: list[Finding],
        result: MaturityResult,
    ) -> AIAnalysis:
        """Run AI analysis and return structured AIAnalysis.

        Default mode is intentionally not JSON-dependent. The deterministic
        rules engine remains the source of truth for structured gaps, trends,
        recommendations, and scores. AI is used as an advisor layer to explain
        those deterministic findings in leadership/service-owner language.

        Set OBSERVASCORE_AI_STRICT_JSON=true only when you specifically want
        the older all-JSON LLM path. Even in strict mode, a JSON parse failure
        falls back to the safe advisor model instead of failing the report.
        """
        logger.info("Running AI analysis with provider=%s model=%s ...", self.provider, self.model)

        context = _build_context(estate, findings, result)
        strict_json = str(os.getenv("OBSERVASCORE_AI_STRICT_JSON", "false")).strip().lower() in {
            "1", "true", "yes", "y"
        }

        if strict_json:
            raw_text = ""
            try:
                raw_text = self._call_model_text(
                    system_prompt=_SYSTEM_PROMPT,
                    user_message=self._build_user_message(context),
                    max_tokens=self.max_tokens,
                )
                parsed = self._parse_response(raw_text)
                return self._build_analysis(parsed)
            except Exception as exc:
                logger.warning(
                    "Strict JSON AI analysis failed; falling back to safe advisor mode: %s",
                    exc,
                )

        try:
            advisor_text = self._call_model_text(
                system_prompt=_ADVISOR_SYSTEM_PROMPT,
                user_message=self._build_advisor_user_message(context),
                max_tokens=min(int(self.max_tokens or 1800), 1800),
            )
        except Exception as exc:
            logger.error("AI advisor call failed: %s", exc)
            return self._error_analysis(str(exc))

        return self._build_advisor_analysis(context, findings, result, advisor_text)

    def _call_model_text(self, system_prompt: str, user_message: str, max_tokens: int | None = None) -> str:
        """Call the configured LLM and return raw text. No JSON parsing here."""
        token_limit = int(max_tokens or self.max_tokens or 1800)

        if self.provider == "anthropic":
            request = {
                "model": self.model,
                "max_tokens": token_limit,
                "temperature": self.temperature,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_message}],
            }
            try:
                response = self.client.messages.create(**request)
            except TypeError:
                # Older Anthropic SDKs may not accept every optional parameter.
                request.pop("temperature", None)
                response = self.client.messages.create(**request)

            parts: list[str] = []
            for block in getattr(response, "content", []) or []:
                if hasattr(block, "text"):
                    parts.append(str(block.text))
                elif isinstance(block, dict) and block.get("text"):
                    parts.append(str(block["text"]))

            raw_text = "\n".join(part for part in parts if part).strip()
            if not raw_text:
                raw_text = str(response)

            try:
                tokens_used = response.usage.output_tokens
            except Exception:
                tokens_used = None
            logger.info("AI advisor complete (anthropic, %s tokens)", tokens_used)
            return raw_text

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=token_limit,
            temperature=self.temperature,
        )
        raw_text = response.choices[0].message.content or ""
        try:
            tokens_used = response.usage.total_tokens if response.usage else None
        except Exception:
            tokens_used = None
        logger.info("AI advisor complete (azure, %s tokens)", tokens_used)
        return raw_text.strip()

    def _build_advisor_user_message(self, context: dict[str, Any]) -> str:
        compact = {
            "client": context.get("client", {}),
            "configured_tools": context.get("configured_tools", []),
            "tool_inventory": context.get("tool_inventory", {}),
            "signal_coverage": context.get("signal_coverage", {}),
            "scrape_targets": context.get("scrape_targets", {}),
            "alert_portfolio": context.get("alert_portfolio", {}),
            "dashboards": context.get("dashboards", {}),
            "maturity_scores": context.get("maturity_scores", {}),
            "modern_stack_signals": context.get("modern_stack_signals", {}),
            "top_deterministic_findings": context.get("top_deterministic_findings", [])[:10],
            "extraction_errors": context.get("extraction_errors", [])[:5],
        }
        return (
            "Create the AI advisor narrative for this ObservaScore report. "
            "Use the deterministic facts below as the source of truth.\n\n"
            f"ESTATE SNAPSHOT:\n{json.dumps(compact, indent=2)}"
        )

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

    def _build_advisor_analysis(
        self,
        context: dict[str, Any],
        findings: list[Finding],
        result: MaturityResult,
        advisor_text: str | None,
    ) -> AIAnalysis:
        narrative = self._clean_narrative(advisor_text)
        if not narrative:
            narrative = self._deterministic_narrative(context, findings, result)

        return AIAnalysis(
            narrative=narrative,
            technical_gaps=self._deterministic_technical_gaps(findings),
            functional_gaps=self._deterministic_functional_gaps(context),
            trend_alignments=self._deterministic_trend_alignments(context),
            prioritized_recommendations=self._deterministic_recommendations(findings, context),
            trend_score=self._trend_score(context, result),
            strengths=self._deterministic_strengths(context),
            model_used=self.model,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    def _clean_narrative(self, text: str | None) -> str:
        if not text:
            return ""
        cleaned = str(text).strip()
        cleaned = cleaned.replace("```", "")
        cleaned = cleaned.replace("<json>", "").replace("</json>", "")
        banned = (
            "malformed json",
            "response parse",
            "api key",
            "i cannot",
            "i'm unable",
        )
        if any(token in cleaned.lower() for token in banned):
            return ""
        # Keep the report readable. The detailed tables carry the structure.
        if len(cleaned) > 2200:
            cleaned = cleaned[:2200].rsplit(" ", 1)[0] + "..."
        return cleaned

    def _severity_rank(self, severity: str | None) -> int:
        return {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}.get(
            (severity or "info").lower(),
            5,
        )

    def _finding_attr(self, finding: Finding, *names: str, default: Any = None) -> Any:
        for name in names:
            if hasattr(finding, name):
                value = getattr(finding, name)
                if value not in (None, "", []):
                    return value
        return default

    def _top_findings(self, findings: list[Finding], limit: int = 8) -> list[Finding]:
        return sorted(
            findings,
            key=lambda f: self._severity_rank(self._finding_attr(f, "severity", default="info")),
        )[:limit]

    def _finding_evidence(self, finding: Finding) -> list[str]:
        values: list[str] = []
        rule_id = self._finding_attr(finding, "rule_id", default=None)
        if rule_id:
            values.append(str(rule_id))

        raw_evidence = self._finding_attr(finding, "evidence", "examples", default=[])
        if isinstance(raw_evidence, list):
            for item in raw_evidence[:4]:
                values.append(str(item))
        elif raw_evidence:
            values.append(str(raw_evidence))

        return values[:5]

    def _finding_recommendation(self, finding: Finding) -> str:
        recommendation = self._finding_attr(
            finding,
            "recommendation",
            "recommended_action",
            "remediation",
            "action",
            default=None,
        )
        if recommendation:
            return str(recommendation)

        title = str(self._finding_attr(finding, "title", default="this gap"))
        return f"Assign an owner, remediate {title}, and validate the improvement in the next ObservaScore run."

    def _deterministic_technical_gaps(self, findings: list[Finding]) -> list[AIInsight]:
        gaps: list[AIInsight] = []
        for finding in self._top_findings(findings, limit=6):
            gaps.append(
                AIInsight(
                    category="technical_gap",
                    title=str(self._finding_attr(finding, "title", default="Observability gap")),
                    description=str(self._finding_attr(finding, "description", default="")),
                    severity=str(self._finding_attr(finding, "severity", default="medium")),
                    recommendation=self._finding_recommendation(finding),
                    evidence=self._finding_evidence(finding),
                )
            )
        return gaps

    def _deterministic_functional_gaps(self, context: dict[str, Any]) -> list[AIInsight]:
        gaps: list[AIInsight] = []
        alert_portfolio = context.get("alert_portfolio", {}) or {}
        dashboards = context.get("dashboards", {}) or {}
        modern = context.get("modern_stack_signals", {}) or {}

        runbook_pct = int(alert_portfolio.get("runbook_coverage_pct") or 0)
        if runbook_pct < 80:
            gaps.append(
                AIInsight(
                    category="functional_gap",
                    title="Runbook coverage needs improvement",
                    description=f"Only {runbook_pct}% of alerts have runbook coverage.",
                    severity="high" if runbook_pct < 50 else "medium",
                    recommendation="Add runbook_url annotations and generate first-draft runbooks for noisy and critical alerts.",
                    evidence=["alert_portfolio.runbook_coverage_pct"],
                )
            )

        if not alert_portfolio.get("has_pagerduty_or_opsgenie_routing"):
            gaps.append(
                AIInsight(
                    category="functional_gap",
                    title="Incident escalation integration is missing",
                    description="No PagerDuty or OpsGenie receiver was detected in Alertmanager routing.",
                    severity="high",
                    recommendation="Integrate Alertmanager with incident escalation tooling and define severity-based routing, acknowledgement, and escalation policies.",
                    evidence=["alert_portfolio.has_pagerduty_or_opsgenie_routing=false"],
                )
            )

        total_dashboards = int(dashboards.get("total") or 0)
        ownership_tags = int(dashboards.get("with_ownership_tags") or 0)
        if total_dashboards and ownership_tags < total_dashboards:
            gaps.append(
                AIInsight(
                    category="functional_gap",
                    title="Dashboard ownership is incomplete",
                    description=f"{ownership_tags}/{total_dashboards} dashboards have ownership tags.",
                    severity="medium",
                    recommendation="Tag dashboards with owning team, service, environment, and escalation contact so incident responders know who owns each view.",
                    evidence=["dashboards.with_ownership_tags"],
                )
            )

        if not modern.get("dora_metrics"):
            gaps.append(
                AIInsight(
                    category="functional_gap",
                    title="DORA metrics are not instrumented",
                    description="Deployment frequency, lead time, change failure rate, and MTTR were not detected.",
                    severity="medium",
                    recommendation="Emit DORA metrics from CI/CD and incident systems so leadership can connect reliability work to delivery performance.",
                    evidence=["modern_stack_signals.dora_metrics=false"],
                )
            )

        if not modern.get("cost_observability"):
            gaps.append(
                AIInsight(
                    category="functional_gap",
                    title="Cost observability is absent",
                    description="No cost or spend signals were detected in the configured observability estate.",
                    severity="low",
                    recommendation="Add OpenCost/Kubecost or cloud-cost metrics and tag costs by service, namespace, and owner.",
                    evidence=["modern_stack_signals.cost_observability=false"],
                )
            )

        return gaps[:6]

    def _deterministic_trend_alignments(self, context: dict[str, Any]) -> list[TrendAlignment]:
        modern = context.get("modern_stack_signals", {}) or {}
        trends = [
            ("OpenTelemetry Collector pipeline", "otel_collector_pipeline", "high"),
            ("OpenTelemetry semantic conventions", "otel_semantic_conventions", "high"),
            ("OTel-native tracing / Tempo", "otel_native_tracing", "medium"),
            ("Continuous profiling", "continuous_profiling", "medium"),
            ("Synthetic monitoring", "synthetic_monitoring", "medium"),
            ("Service mesh or eBPF telemetry", "service_mesh_telemetry", "medium"),
            ("Security observability", "security_observability", "medium"),
            ("Business KPI metrics", "business_kpi_metrics", "medium"),
            ("DORA metrics", "dora_metrics", "medium"),
            ("Cost observability", "cost_observability", "low"),
        ]

        alignments: list[TrendAlignment] = []
        for label, key, impact in trends:
            adopted = bool(modern.get(key))
            alignments.append(
                TrendAlignment(
                    trend=label,
                    status="adopted" if adopted else "absent",
                    impact=impact,
                    description=(
                        "Detected in the configured observability estate."
                        if adopted
                        else "Not detected in the configured observability estate."
                    ),
                )
            )
        return alignments

    def _deterministic_recommendations(self, findings: list[Finding], context: dict[str, Any]) -> list[str]:
        recs: list[str] = []
        for finding in self._top_findings(findings, limit=6):
            title = str(self._finding_attr(finding, "title", default="observability gap"))
            description = str(self._finding_attr(finding, "description", default=""))
            action = self._finding_recommendation(finding)
            recs.append(f"{title}: {action}" if action else f"Address {title}: {description}")

        for gap in self._deterministic_functional_gaps(context)[:4]:
            item = f"{gap.title}: {gap.recommendation}"
            if item not in recs:
                recs.append(item)

        return recs[:10]

    def _deterministic_strengths(self, context: dict[str, Any]) -> list[str]:
        tools = set(context.get("configured_tools", []) or [])
        signal_coverage = context.get("signal_coverage", {}) or {}
        strengths: list[str] = []

        if "prometheus" in tools:
            strengths.append("Prometheus metrics are available for deterministic maturity analysis.")
        if "grafana" in tools:
            strengths.append("Grafana dashboards and datasources are available for dashboard maturity checks.")
        if "jaeger" in tools or "tempo" in tools:
            strengths.append("Distributed tracing data is available for service visibility.")
        if "splunk" in tools or "elasticsearch" in tools or "loki" in tools:
            strengths.append("Log data sources are connected for broader observability coverage.")
        if signal_coverage.get("golden_signals_present", {}).get("latency"):
            strengths.append("Latency signals are present for golden-signal analysis.")
        if context.get("alert_portfolio", {}).get("total", 0):
            strengths.append("Alert rules are available for on-call and alert-quality assessment.")

        return strengths[:7] or ["ObservaScore collected enough evidence to produce a deterministic maturity assessment."]

    def _trend_score(self, context: dict[str, Any], result: MaturityResult) -> float:
        # Keep the report's existing score semantics stable for demo continuity.
        try:
            return float(round(result.overall_score, 1))
        except Exception:
            return float(context.get("maturity_scores", {}).get("overall", 0) or 0)

    def _deterministic_narrative(
        self,
        context: dict[str, Any],
        findings: list[Finding],
        result: MaturityResult,
    ) -> str:
        maturity = context.get("maturity_scores", {}) or {}
        top_titles = [
            str(self._finding_attr(f, "title", default="observability gap"))
            for f in self._top_findings(findings, limit=3)
        ]
        top_text = "; ".join(top_titles) if top_titles else "no critical deterministic gaps"
        return (
            f"The observability estate is assessed at Level {maturity.get('overall_level', result.overall_level)} "
            f"({maturity.get('overall_level_name', result.overall_level_name)}) with an overall score of "
            f"{maturity.get('overall', round(result.overall_score, 1))}/100. "
            f"The highest-priority areas for leadership attention are: {top_text}. "
            "Use the recommendations below to assign owners, validate remediation, and rerun the assessment after changes are deployed."
        )

    def _parse_response(self, raw: str) -> dict[str, Any]:
        """Extract JSON from the LLM response."""
        text = raw.strip()

        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        start = text.find("{")
        end = text.rfind("}")

        if start == -1 or end == -1 or end <= start:
            raise ValueError("No valid JSON object found in AI response")

        text = text[start:end + 1]
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
