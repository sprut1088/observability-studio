# Observability Studio — MCP Tools Design

> **Purpose:** Reference document for building an MCP server on top of Observability Studio,
> enabling an AI-powered RCA / observability bot to call platform capabilities as composable tools.

---

## What is MCP?

Model Context Protocol (MCP) is an open standard by Anthropic that lets AI assistants communicate
with external tools and data sources via a standard protocol (JSON-RPC over stdio/SSE/HTTP).

An MCP server exposes three capability types:
- **Tools** — functions the AI can invoke (e.g. run a query, trigger an analysis)
- **Resources** — data the AI can read (e.g. a report, a signal snapshot)
- **Prompts** — reusable prompt templates

**Why it matters here:** Instead of a human clicking through the Hub UI, an AI bot can autonomously
call ObsCrawl, ObservaScore, the RCA Agent, etc., in natural language conversations.

---

## Design Philosophy: Fine-Grained > Coarse-Grained

A single `run_rca` tool works but limits the bot. Fine-grained tools let the bot:

1. Reason step-by-step ("alerts are firing AND traces are slow → high confidence")
2. Ask follow-up questions before committing to expensive operations
3. Surface intermediate results to the user during investigation
4. Selectively skip steps that aren't needed

---

## MCP Tool Catalogue

### Group 1 — Platform & Discovery

Fast, cheap. Called first to understand what's available.

```
list_available_tools
  └── Source : GET /api/feature-flags + config/tools.yaml
  └── Returns: enabled accelerators + supported tool names/ports
  └── Bot use: "What observability tools are configured and enabled?"

validate_tool_connection
  └── Source : POST /api/v1/validate → crawler_service.validate_connection()
  └── Input  : tool_name, base_url, auth_token (optional)
  └── Returns: reachable (bool), latency_ms, message
  └── Bot use: "Is Prometheus reachable right now?"
```

---

### Group 2 — Signal Collection (Fine-Grained)

Individual signal fetchers. All logic already exists in `signal_collector.py`
but is only exposed today as one bundled `collect_all()` call.

```
get_firing_alerts
  └── Source : SignalCollector._collect_prometheus() — /api/v1/alerts
  └── Input  : prometheus base_url, auth_token, time_window_minutes
  └── Returns: list of { name, severity, labels, annotations, starts_at }
  └── Bot use: "What alerts are currently firing?"

get_unhealthy_scrape_targets
  └── Source : SignalCollector._collect_prometheus() — /api/v1/targets
  └── Input  : prometheus base_url, auth_token
  └── Returns: list of { job, instance, error }
  └── Bot use: "Are any Prometheus scrape targets down?"

get_slow_traces
  └── Source : SignalCollector._collect_jaeger() — Jaeger /api/traces
  └── Input  : jaeger base_url, auth_token, service (optional), time_window_minutes
  └── Returns: list of slow spans { trace_id, service, operation, duration_ms }
  └── Bot use: "Which services had traces > 1s in the last 15 mins?"

get_error_logs
  └── Source : SignalCollector._collect_opensearch() — OpenSearch /_search
  └── Input  : opensearch base_url, auth_token, service (optional), time_window_minutes
  └── Returns: list of { timestamp, level, message, index }
  └── Bot use: "Show me error logs for the payment service"

get_dashboards
  └── Source : SignalCollector._collect_grafana() — /api/search
  └── Input  : grafana base_url, auth_token
  └── Returns: list of { title, uid, folder, tags }
  └── Bot use: "What Grafana dashboards exist for this service?"

get_service_list
  └── Source : SignalCollector.collect_all() → signals.services (from Jaeger)
  └── Input  : jaeger base_url, auth_token
  └── Returns: list of service names
  └── Bot use: "What services are currently visible in Jaeger?"
```

---

### Group 3 — Analysis (Composable Intelligence)

Higher-order tools that apply analytical logic on top of collected signals.

```
correlate_anomalies
  └── Source : CorrelationEngine.correlate(signals)
  └── Input  : CollectedSignals (output of Group 2 tools)
  └── Returns: ranked AnomalyFinding list with confidence scores, affected_services
  └── Bot use: "Given these signals, what is the most likely root cause?"

detect_blast_radius
  └── Source : CascadeDetector.detect_cascade()
  └── Input  : root_services[], observed_services[]
  └── Returns: { direct_dependents, indirect_dependents, cascade_chain, blast_radius }
  └── Bot use: "If PaymentService is the root cause, what else is affected?"

run_full_rca
  └── Source : POST /api/v1/rca → RCAAgent.run()
              (collect → correlate → cascade → LLM narrative → HTML report)
  └── Input  : tools[], incident { service, alert_name, description, time_window_minutes },
               ai_provider, ai_api_key, ai_model
  └── Returns: download_url, anomaly_count, firing_alert_count, blast_radius, run_id
  └── Bot use: End-to-end investigation with Claude-generated HTML report
```

---

### Group 4 — Maturity & Coverage

Periodic health checks and observability quality assessments.

```
run_maturity_assessment
  └── Source : POST /api/v1/assess → scoring_service.run_scoring()
              (35+ rules across 10 dimensions → 1-5 maturity levels)
  └── Input  : tool_source, api_endpoint, auth_token, use_ai, ai_provider, ai_api_key
  └── Returns: download_url to HTML report with dimension scores and gap analysis
  └── Bot use: "Score my observability stack, tell me the top 3 gaps"

crawl_tool_estate
  └── Source : POST /api/v1/crawl → crawler_service.run_crawl()
  └── Input  : tool_name, base_url, auth_token
  └── Returns: download_url to multi-sheet Excel workbook
  └── Bot use: "Export everything from Grafana into Excel"

analyze_red_coverage
  └── Source : POST /api/red-intelligence → red_intelligence_service.run_red_intelligence()
  └── Input  : config (tool endpoints), application_name, environment, canonical_services[]
  └── Returns: download_url to RED coverage HTML report
  └── Bot use: "Which services don't have Rate/Error/Duration dashboards?"

analyze_observability_gaps
  └── Source : POST /api/observability-gap-map → gap_map_service.run_observability_gap_map()
  └── Input  : config (tool endpoints), application_name, services[]
  └── Returns: download_url to gap map HTML report
  └── Bot use: "For the checkout app, which services have no traces?"
```

---

### Group 5 — New Tools (Adapter Logic Exists, No Endpoint Yet)

These are NOT currently exposed. The adapter code already makes these API calls;
they just need to be wrapped into dedicated endpoints or called directly.

```
query_prometheus          (PromQL)
  └── Adapter: PrometheusAdapter → GET /api/v1/query or /api/v1/query_range
  └── Input  : promql expression, time range
  └── Returns: metric time-series or instant vector
  └── Bot use: "What is the p99 latency for PaymentService right now?"

search_traces             (Jaeger)
  └── Adapter: JaegerAdapter → GET /api/traces
  └── Input  : service, operation, tags, start_time, end_time, limit
  └── Returns: list of trace summaries
  └── Bot use: "Find all traces for order-service with errors in the last 5 mins"

query_logs                (OpenSearch / Elasticsearch)
  └── Adapter: ElasticsearchAdapter → POST /_search
  └── Input  : index, query string, time range, limit
  └── Returns: list of log entries
  └── Bot use: "Give me the last 20 error logs for order-service"

get_alert_rules           (Prometheus)
  └── Adapter: PrometheusAdapter → GET /api/v1/rules?type=alert
  └── Input  : prometheus base_url
  └── Returns: list of alert rules with expressions and severity
  └── Bot use: "Does an SLO burn-rate alert exist for PaymentService?"

get_recording_rules       (Prometheus)
  └── Adapter: PrometheusAdapter → GET /api/v1/rules?type=record
  └── Input  : prometheus base_url
  └── Returns: list of recording rules
  └── Bot use: "What pre-computed metrics exist for this service?"
```

---

## Tool Summary

| Group | Count | Status |
|---|---|---|
| Platform & Discovery | 2 | ✅ API exists today |
| Signal Collection | 6 | ⚙️ Logic exists, needs individual endpoint exposure |
| Analysis | 3 | ⚙️ RCA exposed; correlate + cascade need wrapping |
| Maturity & Coverage | 4 | ✅ API exists today |
| New (adapter-level) | 5 | 🔲 Logic in adapters, no endpoint yet |
| **Total** | **20** | |

---

## Example Bot Conversation (Why Composability Matters)

```
User:  "PaymentService is throwing 5xx errors, investigate"

Bot:
  Step 1  validate_tool_connection(prometheus)        → reachable ✓
  Step 2  get_firing_alerts()                         → "PaymentHighErrorRate" firing (critical)
  Step 3  get_slow_traces(service="payment-service")  → 42 traces > 2s in last 15 min
  Step 4  get_error_logs(service="payment-service")   → DB connection timeout errors
  Step 5  correlate_anomalies(signals from 2,3,4)     → root cause: DatabaseAdapter (confidence 0.91)
  Step 6  detect_blast_radius(root="database-adapter")→ affects: PaymentService, OrderService, CartService
  Step 7  run_full_rca(...)                           → HTML report with remediation plan

Bot reply:
  "Root cause: DatabaseAdapter connection pool exhaustion (91% confidence).
   Blast radius: 3 services affected.
   Immediate action: increase pool size from 10 → 50 in db-adapter config.
   Full RCA report: http://10.235.21.132:8001/api/download/runtime/abc123/rca/report.html"
```

---

## Suggested MCP Server Structure (Future Implementation)

```
observability-studio-mcp/
├── server.py                  # MCP server entry point (FastMCP or raw MCP SDK)
├── tools/
│   ├── discovery.py           # list_available_tools, validate_tool_connection
│   ├── signals.py             # get_firing_alerts, get_slow_traces, get_error_logs, …
│   ├── analysis.py            # correlate_anomalies, detect_blast_radius, run_full_rca
│   ├── maturity.py            # run_maturity_assessment, crawl_tool_estate, …
│   └── raw_queries.py         # query_prometheus, search_traces, query_logs, …
└── client.py                  # Thin HTTP client wrapping /api/v1/* endpoints
```

The MCP server can be implemented as a thin HTTP client over the existing FastAPI backend —
no changes to the core platform required for Groups 1, 3 (RCA), and 4.
Groups 2 and 5 require either new endpoints or direct Python imports of the adapter/service code.

---

*Created: May 2026 | Based on Observability Studio v0.3.0*
