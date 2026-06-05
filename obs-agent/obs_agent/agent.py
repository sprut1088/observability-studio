from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm


# # Mock tool implementation
# def get_current_time(city: str) -> dict:
#     """Returns the current time in a specified city."""
#     return {"status": "success", "city": city, "time": "10:30 AM"}

# root_agent = Agent(
#     model='gemini-flash-latest',
#     name='root_agent',
#     description="Tells the current time in a specified city.",
#     instruction="You are a helpful assistant that tells the current time in cities. Use the 'get_current_time' tool for this purpose.",
#     tools=[get_current_time],
# )

PROJECT_KNOWLEDGE = """
You are the Observability Studio knowledge assistant. Answer questions about the project using the
information below. Be concise but accurate. If a question is not covered by this knowledge base,
say so clearly rather than guessing.

---

## What is Observability Studio?

Observability Studio is a multi-accelerator platform for observability data extraction, maturity
assessment, and operations analysis. It combines:
- A FastAPI backend for extraction and report generation
- A React Hub UI for module-driven workflows
- Reusable adapter-based extraction through the ObservaScore Common Observability Model (COM)

---

## Modules (Hub UI)

The platform has five active modules:

1. **ObsCrawl** — Crawl and export observability estate data to Excel
2. **ObservaScore** — Deterministic maturity scoring with optional AI narrative
3. **RCA Agent** — Incident investigation and blast-radius analysis
4. **RED Panel Intelligence** — Service-centric RED dashboard coverage quality
5. **Observability Gap Map** — Application/service coverage mapping with debugging-path
   connectivity checks

---

## Core Capabilities

- Read-only data extraction from multiple observability tools
- Deterministic scoring for coverage/readiness workflows
- Optional AI enrichment for narrative sections (non-required)
- Offline-shareable HTML reports plus JSON artifacts
- Feature-flag-based accelerator enable/disable at runtime

---

## Supported Tool Adapters

Prometheus, Grafana, Loki, Jaeger, Alertmanager, Tempo, Elasticsearch, Datadog, Dynatrace,
AppDynamics, Splunk, OTel Collector

---

## Platform Architecture

Request flow:
  React Hub UI → FastAPI routes → Service layer orchestration → ObservaScore extraction (COM)
  → Accelerator analysis logic → Runtime artifact generation (HTML/JSON/XLSX)
  → Download/preview endpoints

Backend layers:
- **Routes**: request validation and HTTP contract
- **Services**: orchestration and runtime folder handling
- **Accelerators**: deterministic analysis logic and report generation
- **Adapters**: tool-specific read-only extraction

---

## API Surface

Health and Platform:
- GET /api/health
- GET /api/feature-flags

Hub v1 endpoints:
- POST /api/v1/validate
- POST /api/v1/crawl
- POST /api/v1/assess
- POST /api/v1/rca

Current platform endpoints:
- POST /api/observability-gap-map
- POST /api/red-intelligence

Legacy compatibility endpoints:
- POST /api/export
- POST /api/assess

Artifacts:
- GET /api/download/runtime/{{path}}
- GET /api/preview/runtime/{{path}}

---

## Observability Gap Map — Detail

**Coverage layer** (per service):
- Metrics, logs, traces, dashboards, alerts, RED readiness
- Coverage scoring, readiness bands, missing signal recommendations

**Signal Connectivity layer**:
- Checks per service: metrics_to_logs, logs_to_traces, alerts_to_dashboards,
  dashboards_to_logs, dashboards_to_traces
- Scoring: PASS=100, WARN=60, FAIL=0
- MTTR risk: low (>=80), medium (50–79), high (<50)
- JSON output fields: connectivity_results, connectivity_summary

---

## Frontend Hub

Feature-flag-controlled tiles: obscrawl, observascore, rca_agent, red_panel_intelligence,
observability_gap_map

Feature flags are configured in platform/config/feature_flags.yaml and enforced by
backend/app/main.py middleware.

---

## Running Locally

Prerequisites: Python 3.10+, Node.js 18+

```
pip install -e .
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --host 0.0.0.0 --port 8001 --reload

cd ui && npm install && npm run dev
```

---

## Outputs

Generated artifacts are written to runtime/<run_id>/<module>/ and include:
- HTML report (primary preview/download target)
- JSON report (structured output)
- XLSX export (for crawl/export workflows)

---

## Repository Map

- accelerators/       — domain logic for obscrawl, observascore insights, rca-agent
- backend/app/        — FastAPI app, routes, services, schemas
- ui/src/             — Hub UI, modals, API client, styles
- shared_core/        — shared flags and platform internals
- platform/config/    — feature flag configuration
- runtime/            — generated outputs (git-ignored in normal workflows)

---

## Notes

- All extraction is read-only toward source tools.
- Deterministic analysis is the baseline; AI is additive and optional where enabled.
- Legacy APIs are retained for compatibility while Hub v1 endpoints handle streamlined flows.
"""

root_agent = LlmAgent(
    name="observability_studio_agent",
    model=LiteLlm(model="anthropic/claude-sonnet-4-5"),
    description="Answers conceptual questions about the Observability Studio project.",
    instruction=PROJECT_KNOWLEDGE,
)
