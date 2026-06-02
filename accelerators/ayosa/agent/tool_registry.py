"""AYOSA structured tool registry.

A declarative description of every observability tool the agent knows
about — its signal types, supported intents, required/optional fields,
example queries, and a stable `adapter_key` that maps into the existing
`accelerators.ayosa.registry.ADAPTERS` map.

The registry is the SINGLE source of truth used by the agent's Planner to:
  * select which configured tools cover which required signals,
  * derive human-readable suggestions for missing signals,
  * filter out disabled tools without touching adapter code.

It does NOT replace the adapter registry — adapter classes still live in
`accelerators.ayosa.adapters.*` and are wired up in
`accelerators.ayosa.registry.ADAPTERS`. This module is metadata only.
"""

from __future__ import annotations

from typing import Iterable, Literal, Optional

from pydantic import BaseModel, Field

SignalType = Literal["metrics", "logs", "alerts", "traces", "dashboards"]

# Intent names mirror those produced by
# `accelerators.ayosa.agent.intent_classifier.classify_intent`.
KNOWN_INTENTS = {
    "current_time",
    "healthy_services_list",
    "environment_health",
    "latency_issues",
    "latest_error",
    "active_alerts",
    "trace_lookup",
    "dashboard_lookup",
    "service_health",
    "error_investigation",
    "general_observability_question",
}


# ─────────────────────────────────────────────────────────────────────── #
# Schema
# ─────────────────────────────────────────────────────────────────────── #
class ToolDefinition(BaseModel):
    """Declarative metadata for a single observability tool."""

    name: str
    signal_types: list[SignalType] = []
    supported_intents: list[str] = []
    required_fields: list[str] = ["base_url"]
    optional_fields: list[str] = []
    example_queries: list[str] = []
    adapter_key: str
    enabled: bool = True

    # ── Convenience predicates ──────────────────────────────────────── #
    def covers(self, signal: SignalType) -> bool:
        return self.enabled and signal in self.signal_types

    def supports_intent(self, intent: str) -> bool:
        if not self.enabled:
            return False
        if not self.supported_intents:
            return True  # generic catch-all if not specified
        return intent in self.supported_intents


# ─────────────────────────────────────────────────────────────────────── #
# Tool catalogue
# ─────────────────────────────────────────────────────────────────────── #
# Notes on intent assignment:
#   - `general_observability_question` is added wherever the tool produces
#     any of the four primary signals, so the broad fallback intent still
#     picks something up.
#   - No service-specific assumptions (no "checkout", "payment", etc.).
TOOL_DEFINITIONS: list[ToolDefinition] = [
    ToolDefinition(
        name="prometheus",
        signal_types=["metrics"],
        supported_intents=[
            "environment_health",
            "service_health",
            "healthy_services_list",
            "service_stability_ranking",
            "latency_issues",
            "error_investigation",
            "general_observability_question",
        ],
        required_fields=["base_url"],
        optional_fields=["auth_token"],
        example_queries=[
            "up",
            'rate(http_requests_total{status=~"5.."}[5m])',
            "histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket[5m])) by (le))",
        ],
        adapter_key="prometheus",
    ),
    ToolDefinition(
        name="alertmanager",
        signal_types=["alerts"],
        supported_intents=[
            "active_alerts",
            "environment_health",
            "service_health",
            "healthy_services_list",
            "error_investigation",
            "general_observability_question",
        ],
        required_fields=["base_url"],
        optional_fields=["auth_token"],
        example_queries=[
            "GET /api/v2/alerts?active=true",
            "GET /api/v2/silences",
        ],
        adapter_key="alertmanager",
    ),
    ToolDefinition(
        name="elasticsearch",
        signal_types=["logs"],
        supported_intents=[
            "latest_error",
            "error_investigation",
            "service_health",
            "general_observability_question",
        ],
        required_fields=["base_url"],
        optional_fields=["auth_token"],
        example_queries=[
            'GET /logs-*/_search { "query": { "match": { "level": "error" } } }',
        ],
        adapter_key="elasticsearch",
    ),
    ToolDefinition(
        name="opensearch",
        signal_types=["logs"],
        supported_intents=[
            "latest_error",
            "error_investigation",
            "service_health",
            "general_observability_question",
        ],
        required_fields=["base_url"],
        optional_fields=["auth_token"],
        example_queries=[
            'GET /logs-*/_search { "query": { "match": { "level": "error" } } }',
        ],
        # OpenSearch is API-compatible with Elasticsearch and reuses the
        # same adapter implementation in the AYOSA registry today.
        adapter_key="elasticsearch",
    ),
    ToolDefinition(
        name="splunk",
        signal_types=["logs", "alerts"],
        supported_intents=[
            "latest_error",
            "error_investigation",
            "active_alerts",
            "service_health",
            "general_observability_question",
        ],
        required_fields=["base_url", "auth_token"],
        optional_fields=[],
        example_queries=[
            'search index=* sourcetype=* error | head 50',
            'search index=* status>=500 | stats count by host',
        ],
        adapter_key="splunk",
    ),
    ToolDefinition(
        name="grafana",
        signal_types=["dashboards", "alerts"],
        supported_intents=[
            "dashboard_lookup",
            "active_alerts",
            "general_observability_question",
        ],
        required_fields=["base_url"],
        optional_fields=["auth_token"],
        example_queries=[
            "GET /api/search?type=dash-db",
            "GET /api/ruler/grafana/api/v1/rules",
        ],
        adapter_key="grafana",
    ),
    ToolDefinition(
        name="jaeger",
        signal_types=["traces"],
        supported_intents=[
            "trace_lookup",
            "latency_issues",
            "service_health",
            "general_observability_question",
        ],
        required_fields=["base_url"],
        optional_fields=["auth_token"],
        example_queries=[
            "GET /api/services",
            "GET /api/traces?service=&limit=20",
        ],
        adapter_key="jaeger",
    ),
    ToolDefinition(
        name="tempo",
        signal_types=["traces"],
        supported_intents=[
            "trace_lookup",
            "latency_issues",
            "service_health",
            "general_observability_question",
        ],
        required_fields=["base_url"],
        optional_fields=["auth_token"],
        example_queries=[
            "{ duration > 500ms }",
        ],
        adapter_key="tempo",
    ),
    ToolDefinition(
        name="loki",
        signal_types=["logs"],
        supported_intents=[
            "latest_error",
            "error_investigation",
            "service_health",
            "general_observability_question",
        ],
        required_fields=["base_url"],
        optional_fields=["auth_token"],
        example_queries=[
            '{job=~".+"} |= "error"',
            'sum by (level) (count_over_time({job=~".+"}[5m]))',
        ],
        adapter_key="loki",
    ),
    ToolDefinition(
        name="datadog",
        signal_types=["metrics", "logs", "traces", "dashboards", "alerts"],
        supported_intents=[
            "environment_health",
            "service_health",
            "healthy_services_list",
            "latency_issues",
            "latest_error",
            "error_investigation",
            "active_alerts",
            "trace_lookup",
            "dashboard_lookup",
            "general_observability_question",
        ],
        required_fields=["base_url", "auth_token"],
        optional_fields=[],
        example_queries=[
            "avg:trace.http.request.duration{*}",
            'logs("status:error").index("*").rollup("count").last("15m")',
        ],
        adapter_key="datadog",
    ),
    ToolDefinition(
        name="dynatrace",
        signal_types=["metrics", "logs", "traces", "dashboards", "alerts"],
        supported_intents=[
            "environment_health",
            "service_health",
            "healthy_services_list",
            "latency_issues",
            "latest_error",
            "error_investigation",
            "active_alerts",
            "trace_lookup",
            "dashboard_lookup",
            "general_observability_question",
        ],
        required_fields=["base_url", "auth_token"],
        optional_fields=[],
        example_queries=[
            'GET /api/v2/problems?problemSelector=status("open")',
            "GET /api/v2/metrics/query?metricSelector=builtin:service.response.time",
        ],
        adapter_key="dynatrace",
    ),
    ToolDefinition(
        name="appdynamics",
        signal_types=["metrics", "traces", "dashboards", "alerts"],
        supported_intents=[
            "environment_health",
            "service_health",
            "healthy_services_list",
            "latency_issues",
            "error_investigation",
            "active_alerts",
            "trace_lookup",
            "dashboard_lookup",
            "general_observability_question",
        ],
        required_fields=["base_url", "auth_token"],
        optional_fields=[],
        example_queries=[
            "GET /controller/rest/applications?output=JSON",
            "GET /controller/rest/applications/{app}/business-transactions",
        ],
        adapter_key="appdynamics",
    ),
]

# Indexed view — keyed by canonical tool name (lowercase).
TOOL_REGISTRY: dict[str, ToolDefinition] = {td.name: td for td in TOOL_DEFINITIONS}


# ─────────────────────────────────────────────────────────────────────── #
# Public helpers — pure functions, unit-testable
# ─────────────────────────────────────────────────────────────────────── #
def get_tool(name: str) -> Optional[ToolDefinition]:
    """Return the registry entry for `name` (case-insensitive), or None."""
    if not name:
        return None
    return TOOL_REGISTRY.get(name.strip().lower())


def list_tools(*, enabled_only: bool = False) -> list[ToolDefinition]:
    """Return all tool definitions, optionally filtering out disabled ones."""
    if enabled_only:
        return [t for t in TOOL_DEFINITIONS if t.enabled]
    return list(TOOL_DEFINITIONS)


def tools_for_signal(signal: SignalType) -> list[ToolDefinition]:
    """All enabled tools that produce this signal (in registry order)."""
    return [t for t in TOOL_DEFINITIONS if t.covers(signal)]


def tools_for_intent(intent: str) -> list[ToolDefinition]:
    """All enabled tools that declare support for this intent."""
    return [t for t in TOOL_DEFINITIONS if t.supports_intent(intent)]


def select_tools_for_signals(
    required_signals: Iterable[SignalType],
    configured_tools: Iterable[str],
) -> tuple[list[str], list[str]]:
    """Match required signals against the configured tool set.

    Iteration order:
      * required_signals — caller-provided
      * registry order   — stable, deterministic

    Returns `(selected_tool_names, covered_signals)` where each element of
    `selected_tool_names` is a canonical lowercase tool name found in both
    the registry and `configured_tools`. A tool is selected at most once
    even if it covers multiple required signals.
    """
    configured = {(c or "").strip().lower() for c in configured_tools}
    selected: list[str] = []
    covered: list[str] = []
    for signal in required_signals:
        for td in tools_for_signal(signal):
            if td.name not in configured:
                continue
            if td.name not in selected:
                selected.append(td.name)
            if signal not in covered:
                covered.append(signal)
                break  # one tool is enough to cover the signal
    return selected, covered


def describe_missing_signals(
    required_signals: Iterable[SignalType],
    configured_tools: Iterable[str],
    *,
    max_suggestions_per_signal: int = 3,
) -> dict[str, list[str]]:
    """For each required signal NOT covered by the configured tool set,
    return up to `max_suggestions_per_signal` registry-driven tool
    suggestions.

    Output shape:
        { "logs": ["elasticsearch", "splunk", "loki"], ... }
    Signals already covered by a configured tool are omitted.
    """
    configured = {(c or "").strip().lower() for c in configured_tools}
    out: dict[str, list[str]] = {}
    for signal in required_signals:
        candidates = tools_for_signal(signal)
        if any(td.name in configured for td in candidates):
            continue  # signal already covered
        out[signal] = [td.name for td in candidates][:max_suggestions_per_signal]
    return out


def format_missing_signal_message(
    intent: str,
    required_signals: Iterable[SignalType],
    configured_tools: Iterable[str],
) -> str:
    """Build a single human-readable sentence describing what's missing,
    derived entirely from the registry.

    Returns an empty string when nothing is missing.
    """
    missing = describe_missing_signals(required_signals, configured_tools)
    if not missing:
        return ""
    pretty_intent = (intent or "").replace("_", " ") or "this question"
    parts = [
        f"{signal} (try: {', '.join(suggestions)})" if suggestions
        else f"{signal} (no known tools in registry)"
        for signal, suggestions in missing.items()
    ]
    return (
        f"To answer '{pretty_intent}' I need access to the following signal(s) "
        f"that none of your configured tools provide: " + "; ".join(parts) + "."
    )


def validate_tool_config(name: str, fields: dict) -> list[str]:
    """Return a list of missing required field names for the given tool.

    Returns an empty list if the tool is unknown (caller decides how to
    handle that) or if all required fields are present and non-empty.
    """
    td = get_tool(name)
    if td is None:
        return []
    missing: list[str] = []
    for f in td.required_fields:
        value = fields.get(f)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(f)
    return missing


__all__ = [
    "SignalType",
    "ToolDefinition",
    "TOOL_DEFINITIONS",
    "TOOL_REGISTRY",
    "KNOWN_INTENTS",
    "get_tool",
    "list_tools",
    "tools_for_signal",
    "tools_for_intent",
    "select_tools_for_signals",
    "describe_missing_signals",
    "format_missing_signal_message",
    "validate_tool_config",
]
