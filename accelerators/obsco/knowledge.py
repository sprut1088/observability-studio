"""Built-in knowledge base for ObsCo (Observability Copilot).

Self-contained — no external imports. Used by the service layer to answer
questions about observability tools deterministically when no LLM key is
provided, or to seed the LLM with grounded context when one is.
"""

from __future__ import annotations

# Tool fact registry. Keys are lowercase canonical names matching tools.yaml.
TOOL_FACTS: dict[str, dict] = {
    "prometheus": {
        "display_name": "Prometheus",
        "category": "metrics",
        "purpose": (
            "Open-source time-series metrics database and monitoring system. "
            "Pulls metrics from instrumented targets over HTTP, stores them "
            "with multi-dimensional labels, and exposes a powerful query "
            "language (PromQL) for aggregation and alerting."
        ),
        "common_endpoints": [
            "/api/v1/query",
            "/api/v1/query_range",
            "/api/v1/targets",
            "/api/v1/rules",
            "/api/v1/alerts",
            "/-/healthy",
        ],
        "auth_methods": ["none", "bearer", "basic"],
        "common_queries": [
            'rate(http_requests_total[5m])',
            'histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[5m])) by (le))',
            'sum by (instance) (up == 0)  # find down targets',
        ],
        "useful_links": [
            "https://prometheus.io/docs/prometheus/latest/querying/basics/",
            "https://prometheus.io/docs/practices/instrumentation/",
        ],
        "troubleshooting_tips": [
            "Check /api/v1/targets to confirm scrape targets are UP.",
            "Use rate() over counters, not raw values.",
            "Cardinality explosions are usually caused by user-IDs or URLs as labels.",
        ],
    },
    "grafana": {
        "display_name": "Grafana",
        "category": "dashboards",
        "purpose": (
            "Visualization and dashboarding platform for time-series, log, and "
            "trace data. Supports many data sources (Prometheus, Loki, Tempo, "
            "Elasticsearch, etc.) and includes a unified alerting engine."
        ),
        "common_endpoints": [
            "/api/datasources",
            "/api/folders",
            "/api/search",
            "/api/ruler/grafana/api/v1/rules",
            "/api/health",
        ],
        "auth_methods": ["bearer", "basic", "api_key"],
        "common_queries": [
            "GET /api/search?type=dash-db — list dashboards",
            "GET /api/datasources — list configured sources",
        ],
        "useful_links": [
            "https://grafana.com/docs/grafana/latest/dashboards/",
            "https://grafana.com/docs/grafana/latest/alerting/",
        ],
        "troubleshooting_tips": [
            "Use Service Account Tokens (Grafana 9+) over legacy API keys.",
            "Folder permissions cascade — set them at folder, not dashboard level.",
            "For unified alerting, rules live under /api/ruler/grafana/api/v1/rules.",
        ],
    },
    "loki": {
        "display_name": "Loki",
        "category": "logs",
        "purpose": (
            "Horizontally scalable log aggregation system inspired by "
            "Prometheus. Indexes only labels, not full content, which makes it "
            "cost-efficient. Queried with LogQL."
        ),
        "common_endpoints": [
            "/loki/api/v1/query",
            "/loki/api/v1/query_range",
            "/loki/api/v1/labels",
            "/ready",
        ],
        "auth_methods": ["none", "bearer", "basic"],
        "common_queries": [
            '{app="payments"} |= "error"',
            'sum by (level) (count_over_time({app="payments"}[5m]))',
        ],
        "useful_links": [
            "https://grafana.com/docs/loki/latest/logql/",
        ],
        "troubleshooting_tips": [
            "Keep label cardinality low — never put trace IDs or user IDs in labels.",
            "Use line filters (|=, !=) before parsers for performance.",
        ],
    },
    "jaeger": {
        "display_name": "Jaeger",
        "category": "traces",
        "purpose": (
            "End-to-end distributed tracing system. Collects and visualises "
            "spans across microservices, helping pinpoint latency bottlenecks "
            "and inter-service dependencies."
        ),
        "common_endpoints": [
            "/api/services",
            "/api/traces",
            "/api/dependencies",
        ],
        "auth_methods": ["none", "bearer"],
        "common_queries": [
            "GET /api/services — list instrumented services",
            "GET /api/traces?service=payments&limit=20",
        ],
        "useful_links": [
            "https://www.jaegertracing.io/docs/latest/",
        ],
        "troubleshooting_tips": [
            "If services are missing, ensure the SDK is reporting to the right collector endpoint.",
            "Use tags (http.status_code=500) to find error traces.",
        ],
    },
    "tempo": {
        "display_name": "Tempo",
        "category": "traces",
        "purpose": (
            "Grafana Labs distributed tracing backend, optimised for object "
            "storage. Designed to ingest 100% of traces cheaply; queried via "
            "TraceQL and integrated tightly with Grafana, Loki and Mimir."
        ),
        "common_endpoints": [
            "/api/traces/{traceID}",
            "/api/search",
            "/ready",
        ],
        "auth_methods": ["none", "bearer"],
        "common_queries": [
            '{ resource.service.name = "payments" && duration > 500ms }',
        ],
        "useful_links": [
            "https://grafana.com/docs/tempo/latest/traceql/",
        ],
        "troubleshooting_tips": [
            "Tempo does not index by attributes — use TraceQL with span filters.",
            "Pair with Grafana for trace-to-log correlation via exemplars.",
        ],
    },
    "elasticsearch": {
        "display_name": "Elasticsearch",
        "category": "logs",
        "purpose": (
            "Distributed search and analytics engine commonly used for log "
            "storage as part of the ELK / Elastic Stack. Schemaless documents "
            "indexed in near real-time and queried with Query DSL."
        ),
        "common_endpoints": [
            "/_cluster/health",
            "/_cat/indices",
            "/{index}/_search",
        ],
        "auth_methods": ["basic", "bearer", "api_key"],
        "common_queries": [
            'GET /logs-*/_search { "query": { "match": { "level": "error" } } }',
            "GET /_cat/indices?v — index sizes & health",
        ],
        "useful_links": [
            "https://www.elastic.co/guide/en/elasticsearch/reference/current/query-dsl.html",
        ],
        "troubleshooting_tips": [
            "Yellow cluster status = unassigned replicas (usually a single-node dev cluster).",
            "Set ILM policies — unbounded log indices will eat disk.",
        ],
    },
    "splunk": {
        "display_name": "Splunk",
        "category": "logs",
        "purpose": (
            "Enterprise platform for searching, monitoring, and analysing "
            "machine-generated data. Uses SPL (Search Processing Language) to "
            "transform events into dashboards, alerts and reports."
        ),
        "common_endpoints": [
            "/services/search/jobs",
            "/services/server/info",
            "/services/data/indexes",
        ],
        "auth_methods": ["basic", "bearer", "session"],
        "common_queries": [
            'search index=app sourcetype=access_combined status=500 | stats count by host',
            'index=_internal source=*metrics.log group=per_sourcetype_thruput',
        ],
        "useful_links": [
            "https://docs.splunk.com/Documentation/Splunk/latest/SearchReference/WhatsInThisManual",
        ],
        "troubleshooting_tips": [
            "Always pin a time range; unbounded searches will hammer the cluster.",
            "Use `tstats` against accelerated data models for fast aggregations.",
        ],
    },
    "datadog": {
        "display_name": "Datadog",
        "category": "apm",
        "purpose": (
            "SaaS observability platform unifying metrics, traces, logs, RUM, "
            "synthetics and security signals. Strong out-of-the-box dashboards "
            "and ML-driven anomaly / outlier detection."
        ),
        "common_endpoints": [
            "/api/v1/validate",
            "/api/v1/dashboard",
            "/api/v1/monitor",
            "/api/v2/logs/events/search",
        ],
        "auth_methods": ["api_key+app_key"],
        "common_queries": [
            'avg:trace.http.request.duration{service:payments} by {resource_name}',
            'logs("service:payments status:error").index("*").rollup("count").last("15m")',
        ],
        "useful_links": [
            "https://docs.datadoghq.com/api/latest/",
        ],
        "troubleshooting_tips": [
            "Datadog charges per host AND per custom metric — beware tag cardinality.",
            "Use the /validate endpoint to confirm both API and APP keys are accepted.",
        ],
    },
    "dynatrace": {
        "display_name": "Dynatrace",
        "category": "apm",
        "purpose": (
            "Full-stack APM and infrastructure observability built around the "
            "OneAgent and Davis AI. Auto-discovers services, processes and "
            "host topology with deterministic root-cause analysis."
        ),
        "common_endpoints": [
            "/api/v1/entity/services",
            "/api/v2/problems",
            "/api/v2/metrics/query",
        ],
        "auth_methods": ["api_token"],
        "common_queries": [
            "GET /api/v2/problems?problemSelector=status(\"open\")",
            "GET /api/v2/metrics/query?metricSelector=builtin:service.response.time",
        ],
        "useful_links": [
            "https://docs.dynatrace.com/docs/dynatrace-api",
        ],
        "troubleshooting_tips": [
            "API tokens have fine-grained scopes — entities.read, metrics.read, problems.read.",
            "Davis problem IDs link directly to the root-cause graph in the UI.",
        ],
    },
    "appdynamics": {
        "display_name": "AppDynamics",
        "category": "apm",
        "purpose": (
            "Cisco's enterprise APM tool. Auto-instruments Java/.NET/Node apps, "
            "models business transactions, and detects performance regressions "
            "against learned baselines."
        ),
        "common_endpoints": [
            "/controller/rest/applications",
            "/controller/rest/applications/{app}/business-transactions",
            "/controller/rest/applications/{app}/events",
        ],
        "auth_methods": ["basic", "oauth"],
        "common_queries": [
            "GET /controller/rest/applications?output=JSON",
            "GET /controller/rest/applications/{app}/metric-data?metric-path=...",
        ],
        "useful_links": [
            "https://docs.appdynamics.com/display/PRO45/AppDynamics+APIs",
        ],
        "troubleshooting_tips": [
            "Always use the account-qualified username: user@account when authenticating.",
            "Health rule violations are the canonical way to surface alerts.",
        ],
    },
    "alertmanager": {
        "display_name": "Alertmanager",
        "category": "alerts",
        "purpose": (
            "Companion to Prometheus that handles alerts: deduplication, "
            "grouping, silencing, inhibition and routing to receivers "
            "(email, Slack, PagerDuty, webhook, etc.)."
        ),
        "common_endpoints": [
            "/api/v2/alerts",
            "/api/v2/silences",
            "/api/v2/status",
        ],
        "auth_methods": ["none", "basic"],
        "common_queries": [
            "GET /api/v2/alerts?active=true",
            "GET /api/v2/silences",
        ],
        "useful_links": [
            "https://prometheus.io/docs/alerting/latest/configuration/",
        ],
        "troubleshooting_tips": [
            "Group alerts by service/severity to avoid notification storms.",
            "Use inhibition rules to suppress secondary alerts during major incidents.",
        ],
    },
    "opensearch": {
        "display_name": "OpenSearch",
        "category": "logs",
        "purpose": (
            "Apache-2.0 fork of Elasticsearch/Kibana maintained by AWS. "
            "API-compatible with Elasticsearch 7.10; commonly used for log "
            "analytics and full-text search."
        ),
        "common_endpoints": [
            "/_cluster/health",
            "/_cat/indices",
            "/{index}/_search",
        ],
        "auth_methods": ["basic", "bearer"],
        "common_queries": [
            'GET /logs-*/_search { "query": { "match": { "level": "error" } } }',
        ],
        "useful_links": [
            "https://opensearch.org/docs/latest/",
        ],
        "troubleshooting_tips": [
            "OpenSearch and Elasticsearch clients are mostly interchangeable for 7.x APIs.",
            "Use ISM policies to roll over and delete time-series indices.",
        ],
    },
}


# Aliases so common phrasings still resolve.
ALIASES: dict[str, str] = {
    "prom": "prometheus",
    "promql": "prometheus",
    "es": "elasticsearch",
    "elastic": "elasticsearch",
    "kibana": "elasticsearch",
    "logql": "loki",
    "traceql": "tempo",
    "dd": "datadog",
    "dt": "dynatrace",
    "appd": "appdynamics",
    "am": "alertmanager",
}


def detect_tools(text: str) -> list[str]:
    """Return canonical tool names mentioned in `text` (lowercase)."""
    lower = (text or "").lower()
    found: list[str] = []
    for key in TOOL_FACTS:
        if key in lower and key not in found:
            found.append(key)
    for alias, canonical in ALIASES.items():
        # Word boundary check: alias must appear as standalone token-ish
        if alias in lower.split() and canonical not in found:
            found.append(canonical)
    return found


def get_facts(tool: str) -> dict | None:
    """Return facts for a tool (handles aliases)."""
    key = (tool or "").lower().strip()
    key = ALIASES.get(key, key)
    return TOOL_FACTS.get(key)
