# Observability Studio

Observability Studio is a multi-accelerator platform for observability data extraction, maturity assessment, and operations analysis.

It combines:
- A FastAPI backend for extraction and report generation
- A React Hub UI for module-driven workflows
- Reusable adapter-based extraction through the ObservaScore Common Observability Model (COM)

## What This Repo Is Today

This repository has evolved from a single CLI assessment tool into a platform with five active modules in the Hub UI:

1. ObsCrawl: Crawl and export observability estate data to Excel
2. ObservaScore: Deterministic maturity scoring with optional AI narrative
3. RCA Agent: Incident investigation and blast-radius analysis
4. RED Panel Intelligence: Service-centric RED dashboard coverage quality
5. Observability Gap Map: Application/service coverage mapping with debugging-path connectivity checks

## Core Capabilities

- Read-only data extraction from multiple observability tools
- Deterministic scoring for coverage/readiness workflows
- Optional AI enrichment for narrative sections (non-required)
- Offline-shareable HTML reports plus JSON artifacts
- Feature-flag based accelerator enable/disable at runtime

## Supported Tool Adapters

Current adapter set includes:
- Prometheus
- Grafana
- Loki
- Jaeger
- Alertmanager
- Tempo
- Elasticsearch
- Datadog
- Dynatrace
- AppDynamics
- Splunk
- OTel Collector

## Platform Architecture

Request flow:

React Hub UI
-> FastAPI routes
-> Service layer orchestration
-> ObservaScore extraction (COM)
-> Accelerator analysis logic
-> Runtime artifact generation (HTML/JSON/XLSX)
-> Download/preview endpoints

Backend layers:
- Routes: request validation and HTTP contract
- Services: orchestration and runtime folder handling
- Accelerators: deterministic analysis logic and report generation
- Adapters: tool-specific read-only extraction

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
- GET /api/download/runtime/{path}
- GET /api/preview/runtime/{path}

## Observability Gap Map: Current Behavior

Gap Map remains focused on service-level signal coverage and now includes a separate debugging-path connectivity layer.

Coverage layer (existing):
- Metrics, logs, traces, dashboards, alerts, RED readiness per service
- Coverage scoring, readiness bands, missing signal recommendations

Signal Connectivity layer (new):
- Separate section in analysis/report, not merged into coverage matrix
- Service-level connectivity checks:
  - metrics_to_logs
  - logs_to_traces
  - alerts_to_dashboards
  - dashboards_to_logs
  - dashboards_to_traces
- Deterministic scoring:
  - PASS = 100
  - WARN = 60
  - FAIL = 0
- MTTR risk classification:
  - low: score >= 80
  - medium: score >= 50 and < 80
  - high: score < 50
- Added JSON fields:
  - connectivity_results
  - connectivity_summary

## Frontend Hub

Main Hub tile inventory:
- obscrawl
- observascore
- rca_agent
- red_panel_intelligence
- observability_gap_map

Feature flags control tile and API availability through:
- platform/config/feature_flags.yaml
- backend middleware enforcement in backend/app/main.py

## Running Locally

Prerequisites:
- Python 3.10+
- Node.js 18+

Install backend dependencies:

```bash
pip install -e .
pip install -r backend/requirements.txt
```

Run backend:

```bash
uvicorn backend.app.main:app --host 0.0.0.0 --port 8001 --reload
```

Run frontend:

```bash
cd ui
npm install
npm run dev
```

## Configuration

Runtime extraction config is generated per execution under runtime/<run_id>/.

Tool connection details are submitted from UI payloads and converted into runtime config files by backend services.

Splunk-specific URL derivation and auth mapping are supported in the config builder/service payload path.

## Outputs

Generated artifacts are written to runtime/<run_id>/<module>/ and typically include:
- HTML report (primary preview/download target)
- JSON report (structured output)
- XLSX export (for crawl/export workflows)

## Repository Map (High Level)

- accelerators/: domain logic for obscrawl, observascore insights, rca-agent
- backend/app/: FastAPI app, routes, services, schemas
- ui/src/: Hub UI, modals, API client, styles
- shared_core/: shared flags and platform internals
- platform/config/: feature flag configuration
- runtime/: generated outputs (git-ignored in normal workflows)

## Notes

- All extraction is read-only toward source tools.
- Deterministic analysis is the baseline; AI is additive and optional where enabled.
- Existing legacy APIs are retained for compatibility while Hub v1 endpoints handle streamlined module flows.
