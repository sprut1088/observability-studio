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

import ast
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

RESPONSE FORMAT: Respond ONLY with a valid JSON object. No markdown, no explanation outside the JSON. The JSON must exactly match the schema provided in the user message. Escape all double quotes inside string values, or avoid double quotes inside string values entirely."""


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
        # Keep responses compact enough to stay valid JSON. Long LLM JSON responses
        # are much more likely to contain unescaped quotes or truncation.
        self.max_tokens = int(config.get("max_tokens") or 3200)
        self.max_tokens = max(1200, min(self.max_tokens, 5000))
        self.repair_max_tokens = int(config.get("repair_max_tokens") or 2200)
        self.temperature = float(config.get("temperature", 0.2))
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
        """Run AI analysis and return structured AIAnalysis."""
        logger.info("Running AI analysis with provider=%s model=%s ...", self.provider, self.model)

        context = _build_context(estate, findings, result)
        user_message = self._build_user_message(context)

        try:
            if self.provider == "anthropic":
                # Keep compatibility with existing Anthropic usage
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    system=_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_message}],
                )
                # Anthropic sdk shapes response.content as a list in some versions
                raw_text = ""
                if getattr(response, "content", None):
                    try:
                        raw_text = response.content[0].text
                    except Exception:
                        # fallback if response.content is different shape
                        raw_text = str(response)
                else:
                    raw_text = str(response)
                try:
                    tokens_used = response.usage.output_tokens
                except Exception:
                    tokens_used = None
                logger.info("AI analysis complete (anthropic, %s tokens)", tokens_used)

            else:
                # Azure OpenAI (openai v1.x AzureOpenAI client)
                messages = [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ]
                response = self.client.chat.completions.create(
                    model=self.model,  # deployment name
                    messages=messages,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                )
                raw_text = response.choices[0].message.content or ""
                try:
                    tokens_used = response.usage.total_tokens if response.usage else None
                except Exception:
                    tokens_used = None
                logger.info("AI analysis complete (azure, %s tokens)", tokens_used)

        except Exception as e:
            logger.error("AI analysis API call failed: %s", e)
            return self._error_analysis(str(e))

        try:
            parsed = self._parse_response(raw_text)
        except Exception as first_error:
            logger.warning("Failed to parse AI response, attempting JSON repair: %s", first_error)
            logger.debug("Raw AI response: %s", raw_text[:4000])
            try:
                repaired_text = self._repair_response(raw_text)
                parsed = self._parse_response(repaired_text)
                logger.info("AI response repaired successfully")
            except Exception as repair_error:
                logger.error("Failed to repair AI response: %s", repair_error)
                parsed = self._fallback_payload_from_context(
                    context=context,
                    raw_text=raw_text,
                    parse_error=f"{first_error}; repair failed: {repair_error}",
                )

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

JSON SAFETY RULES:
- Return compact JSON only.
- Do not wrap JSON in markdown fences.
- Do not use unescaped double quotes inside string values. Use apostrophes instead.
- Keep each description under 35 words.
- Keep each recommendation under 35 words.
- Use arrays of strings for evidence, strengths, and prioritized_recommendations.
- Use only valid JSON booleans, numbers, strings, arrays, and objects.

BE SPECIFIC. Do not give generic advice. Reference actual data from the estate (metric names, alert names, service names, tool configurations) wherever possible."""

    #def _parse_response(self, raw: str) -> dict[str, Any]:
    #    """Extract JSON from the LLM response."""
    #    # Strip markdown fences if present
    #    text = raw.strip()
    #    if text.startswith("```"):
    #        lines = text.split("\n")
    #        # Remove first and last fence lines
    #        text = "\n".join(lines[1:] if lines[0].startswith("```") else lines)
    #        if text.endswith("```"):
    #            text = text[: text.rfind("```")]
    #    text = text.strip()
    #    return json.loads(text)

    def _strip_markdown_fences(self, raw: str) -> str:
        text = (raw or "").strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        return text

    def _extract_balanced_json_object(self, text: str) -> str:
        """Return the first balanced JSON object from text.

        rfind('}') is unsafe when the model appends text or produces stray braces
        inside explanations. This scanner respects quoted strings and escapes.
        """
        start = text.find("{")
        if start == -1:
            raise ValueError("No JSON object start found in AI response")

        depth = 0
        in_string = False
        escape = False

        for index in range(start, len(text)):
            char = text[index]

            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start:index + 1]

        # Fall back to the broad slice so a useful parse error is raised.
        end = text.rfind("}")
        if end > start:
            return text[start:end + 1]

        raise ValueError("No balanced JSON object found in AI response")

    def _json_cleanup_candidates(self, text: str) -> list[str]:
        """Generate conservative cleanup variants for common LLM JSON mistakes."""
        base = text.strip()
        base = base.replace("\ufeff", "")
        base = base.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
        base = re.sub(r",\s*([}\]])", r"\1", base)

        candidates = [base]

        # Remove JavaScript-style comments if any appear.
        no_line_comments = re.sub(r"(?m)^\s*//.*$", "", base)
        no_block_comments = re.sub(r"/\*.*?\*/", "", no_line_comments, flags=re.S)
        no_block_comments = re.sub(r",\s*([}\]])", r"\1", no_block_comments)
        if no_block_comments not in candidates:
            candidates.append(no_block_comments)

        return candidates

    def _parse_response(self, raw: str) -> dict[str, Any]:
        """Extract and parse a JSON object from the LLM response."""
        text = self._strip_markdown_fences(raw)
        text = self._extract_balanced_json_object(text)

        last_error: Exception | None = None
        for candidate in self._json_cleanup_candidates(text):
            try:
                parsed = json.loads(candidate)
                if not isinstance(parsed, dict):
                    raise ValueError("AI JSON root must be an object")
                return parsed
            except Exception as exc:
                last_error = exc

        # Some providers occasionally return Python-style dicts with single quotes.
        # This is not ideal, but ast.literal_eval is safe for literals and improves
        # demo resilience without adding extra dependencies.
        try:
            parsed = ast.literal_eval(text)
            if isinstance(parsed, dict):
                return parsed
        except Exception as exc:
            last_error = exc

        raise ValueError(str(last_error or "Could not parse AI response as JSON"))

    def _repair_response(self, raw_text: str) -> str:
        """Ask the configured model to repair malformed JSON once."""
        broken = self._strip_markdown_fences(raw_text)
        broken = broken[:14000]

        repair_prompt = f"""Repair the following malformed JSON into valid compact JSON.

Rules:
- Return JSON only. No markdown.
- Keep the same meaning.
- Do not add new facts.
- Use the exact top-level keys from this schema: narrative, technical_gaps, functional_gaps, trend_alignments, prioritized_recommendations, trend_score, strengths.
- Escape any double quotes inside strings, or replace them with apostrophes.
- Keep descriptions short.

Schema example:
{json.dumps(_RESPONSE_SCHEMA, indent=2)}

Malformed JSON or response text:
{broken}
""".strip()

        if self.provider == "anthropic":
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.repair_max_tokens,
                temperature=0,
                system="You repair malformed JSON. Return valid JSON only.",
                messages=[{"role": "user", "content": repair_prompt}],
            )
            if getattr(response, "content", None):
                try:
                    return response.content[0].text
                except Exception:
                    return str(response)
            return str(response)

        messages = [
            {"role": "system", "content": "You repair malformed JSON. Return valid JSON only."},
            {"role": "user", "content": repair_prompt},
        ]
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=self.repair_max_tokens,
            temperature=0,
        )
        return response.choices[0].message.content or ""

    def _as_list(self, value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return [value]

    def _as_string_list(self, value: Any, limit: int | None = None) -> list[str]:
        rows = []
        for item in self._as_list(value):
            if isinstance(item, dict):
                rows.append("; ".join(f"{k}: {v}" for k, v in item.items() if v is not None))
            else:
                rows.append(str(item))
        if limit is not None:
            rows = rows[:limit]
        return rows

    def _normalise_gap(self, item: Any, category: str) -> dict[str, Any]:
        if isinstance(item, dict):
            return {
                "title": str(item.get("title") or item.get("name") or category.replace("_", " ").title()),
                "description": str(item.get("description") or item.get("summary") or ""),
                "severity": str(item.get("severity") or "medium").lower(),
                "recommendation": str(item.get("recommendation") or item.get("action") or "Review and remediate this gap."),
                "evidence": self._as_string_list(item.get("evidence", []), limit=8),
            }
        return {
            "title": category.replace("_", " ").title(),
            "description": str(item),
            "severity": "medium",
            "recommendation": "Review and remediate this gap.",
            "evidence": [],
        }

    def _normalise_trend(self, item: Any) -> dict[str, str]:
        if isinstance(item, dict):
            status = str(item.get("status") or "partial").lower()
            if status not in {"adopted", "partial", "absent"}:
                status = "partial"
            impact = str(item.get("impact") or "medium").lower()
            if impact not in {"high", "medium", "low"}:
                impact = "medium"
            return {
                "trend": str(item.get("trend") or item.get("name") or "Modern observability practice"),
                "status": status,
                "impact": impact,
                "description": str(item.get("description") or item.get("summary") or ""),
            }
        return {
            "trend": str(item),
            "status": "partial",
            "impact": "medium",
            "description": str(item),
        }

    def _normalise_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        """Coerce model JSON into the exact shape expected by _build_analysis."""
        if not isinstance(data, dict):
            raise ValueError("AI response must be a JSON object")

        return {
            "narrative": str(data.get("narrative") or data.get("executive_summary") or ""),
            "technical_gaps": [
                self._normalise_gap(item, "technical_gap")
                for item in self._as_list(data.get("technical_gaps", []))
            ],
            "functional_gaps": [
                self._normalise_gap(item, "functional_gap")
                for item in self._as_list(data.get("functional_gaps", []))
            ],
            "trend_alignments": [
                self._normalise_trend(item)
                for item in self._as_list(data.get("trend_alignments", []))
            ],
            "prioritized_recommendations": self._as_string_list(
                data.get("prioritized_recommendations") or data.get("recommendations") or [],
                limit=15,
            ),
            "trend_score": data.get("trend_score", 0),
            "strengths": self._as_string_list(data.get("strengths", []), limit=8),
        }

    def _fallback_payload_from_context(
        self,
        context: dict[str, Any],
        raw_text: str,
        parse_error: str,
    ) -> dict[str, Any]:
        """Create a safe structured advisor output when the LLM returns invalid JSON.

        This prevents the report from showing an API-key style error when the model
        response was usable but malformed. It uses deterministic context only.
        """
        maturity = context.get("maturity_scores", {}) or {}
        alert_portfolio = context.get("alert_portfolio", {}) or {}
        modern = context.get("modern_stack_signals", {}) or {}
        findings = context.get("top_deterministic_findings", []) or []
        tools = context.get("configured_tools", []) or []

        high_findings = [f for f in findings if f.get("severity") in {"critical", "high"}]
        selected_findings = (high_findings or findings)[:6]

        technical_gaps = []
        for finding in selected_findings[:5]:
            technical_gaps.append({
                "title": finding.get("title", "Observability gap"),
                "description": finding.get("description", "Deterministic rules identified an observability gap."),
                "severity": finding.get("severity", "medium"),
                "recommendation": "Prioritize remediation, assign an owner, and validate the fix in the next assessment cycle.",
                "evidence": [finding.get("rule_id", "deterministic finding")],
            })

        functional_gaps = []
        if alert_portfolio.get("runbook_coverage_pct", 100) < 80:
            functional_gaps.append({
                "title": "Runbook coverage needs improvement",
                "description": f"Only {alert_portfolio.get('runbook_coverage_pct', 0)}% of alerts have runbook coverage.",
                "severity": "high",
                "recommendation": "Add runbook_url annotations and generate first-draft runbooks for noisy and critical alerts.",
                "evidence": ["alert_portfolio.runbook_coverage_pct"],
            })
        if not alert_portfolio.get("has_pagerduty_or_opsgenie_routing"):
            functional_gaps.append({
                "title": "Incident escalation integration is missing",
                "description": "No PagerDuty or OpsGenie receiver was detected in Alertmanager routing.",
                "severity": "high",
                "recommendation": "Integrate Alertmanager with incident escalation tooling and define severity-based routing.",
                "evidence": ["alert_portfolio.has_pagerduty_or_opsgenie_routing=false"],
            })

        trend_map = [
            ("OpenTelemetry Collector pipeline", modern.get("otel_collector_pipeline"), "high"),
            ("OpenTelemetry semantic conventions", modern.get("otel_semantic_conventions"), "high"),
            ("Continuous profiling", modern.get("continuous_profiling"), "medium"),
            ("Synthetic monitoring", modern.get("synthetic_monitoring"), "medium"),
            ("Service mesh or eBPF telemetry", modern.get("service_mesh_telemetry"), "medium"),
            ("Security observability", modern.get("security_observability"), "medium"),
            ("Business KPI metrics", modern.get("business_kpi_metrics"), "medium"),
            ("DORA metrics", modern.get("dora_metrics"), "medium"),
            ("Cost observability", modern.get("cost_observability"), "low"),
        ]
        trend_alignments = [
            {
                "trend": name,
                "status": "adopted" if present else "absent",
                "impact": impact,
                "description": f"{'Detected' if present else 'Not detected'} in the configured observability estate.",
            }
            for name, present, impact in trend_map
        ]

        recommendations = []
        for finding in selected_findings[:8]:
            recommendations.append(
                f"Address {finding.get('title', 'observability gap')}: {finding.get('description', '')}"
            )
        if not recommendations:
            recommendations.append("Review deterministic findings with service owners and prioritize high-risk gaps.")

        strengths = []
        if "prometheus" in tools:
            strengths.append("Prometheus metrics are available for deterministic maturity analysis.")
        if "grafana" in tools:
            strengths.append("Grafana dashboards and datasources are available for dashboard maturity checks.")
        if "jaeger" in tools or "tempo" in tools:
            strengths.append("Distributed tracing data is available for service visibility.")
        if "splunk" in tools or "elasticsearch" in tools:
            strengths.append("Log data sources are connected for broader observability coverage.")
        if not strengths:
            strengths.append("The assessment produced deterministic findings that can guide remediation.")

        narrative = (
            f"The observability estate is assessed at Level {maturity.get('overall_level')} "
            f"({maturity.get('overall_level_name')}) with an overall score of "
            f"{maturity.get('overall')}/100. The deterministic engine identified "
            f"{len(findings)} prioritized findings across the maturity model. "
            "The AI provider returned malformed JSON, so ObservaScore used a safe advisor fallback based on deterministic evidence. "
            "This keeps the report actionable while avoiding a false API-key error."
        )

        return {
            "narrative": narrative,
            "technical_gaps": technical_gaps,
            "functional_gaps": functional_gaps[:5],
            "trend_alignments": trend_alignments,
            "prioritized_recommendations": recommendations,
            "trend_score": maturity.get("overall", 0) or 0,
            "strengths": strengths,
        }

    def _build_analysis(self, data: dict[str, Any]) -> AIAnalysis:
        """Convert parsed LLM JSON into AIAnalysis dataclass."""
        data = self._normalise_payload(data)

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

        try:
            trend_score = float(data.get("trend_score", 0))
        except Exception:
            trend_score = 0.0
        trend_score = max(0.0, min(trend_score, 100.0))

        return AIAnalysis(
            narrative=data.get("narrative", ""),
            technical_gaps=technical_gaps,
            functional_gaps=functional_gaps,
            trend_alignments=trend_alignments,
            prioritized_recommendations=data.get("prioritized_recommendations", []),
            trend_score=trend_score,
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
