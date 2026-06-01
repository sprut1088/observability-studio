# Observability Studio

Observability Studio is a multi-accelerator platform for observability data
extraction, maturity assessment, root-cause analysis, dashboard quality
analysis, gap mapping, and conversational investigation.

It combines:
- A FastAPI backend that exposes module-scoped APIs and serves generated artifacts
- A React Hub UI that drives each accelerator through a dedicated modal
- A reusable adapter layer (ObservaScore Common Observability Model — COM) for read-only extraction across tools
- A floating Observability Copilot (ObsCo) chat bot available globally in the UI

## What This Repo Is Today

The Hub UI exposes six accelerator tiles plus one always-on copilot:

1. **ObsCrawl** — Crawl any tool and export the estate as a multi-sheet Excel workbook
2. **ObservaScore** — Deterministic maturity scoring (35+ rules, 10 dimensions) with optional AI gap analysis
3. **RCA Agent** — Multi-tool signal collection, anomaly correlation, blast-radius analysis, Claude-powered RCA report
4. **RED Panel Intelligence** — Service-centric Rate/Errors/Duration dashboard coverage scoring
5. **Observability Gap Map** — Per-application service coverage + debugging-path signal connectivity checks
6. **AYOSA** — *Ask Your Observability Stack Anything* — live cross-signal investigation copilot (chat + SSE streaming)
7. **ObsCo** — Floating Observability Copilot chat bot pinned to the bottom-right of every screen; answers questions about supported tools using a built-in knowledge base, optionally enhanced by Claude

## Core Capabilities

- Read-only data extraction from multiple observability tools
- Deterministic scoring for coverage and readiness workflows
- Optional AI enrichment for narrative / RCA sections (never required)
- Offline-shareable HTML reports plus structured JSON and XLSX artifacts
- Server-Sent Events streaming for AYOSA live investigations
- Feature-flag gated accelerator enable/disable at runtime (UI + backend)

## Supported Tool Adapters

Read-only adapters available through ObservaScore / AYOSA:

- Prometheus
- Grafana
- Loki
- Jaeger
- Tempo
- Alertmanager
- Elasticsearch (and OpenSearch via the same client)
- Datadog
- Dynatrace
- AppDynamics
- Splunk
- OTel Collector

## Platform Architecture

Request flow:

```
React Hub UI
  └─> FastAPI route (routes/v1/* or routes/*)
      └─> Service layer (orchestration, runtime dir, subprocess / inline)
          └─> Accelerator logic (accelerators/<name>/)
              └─> Adapters (read-only HTTP to tools)
              └─> Runtime artifact written to runtime/<run_id>/<module>/
      └─> Response with download_url
  <─ GET /api/download/runtime/<path>  ─> file delivered to browser
```

Backend layers:

| Layer       | Path                              | Responsibility |
|-------------|-----------------------------------|----------------|
| Route       | `backend/app/routes/`             | Request validation, HTTP contract, thin handler |
| Service     | `backend/app/services/`           | Async orchestration, runtime folder handling, subprocess/inline calls |
| Accelerator | `accelerators/<name>/`            | Domain logic, report generation |
| Adapter     | `accelerators/observascore/adapters/` and `accelerators/ayosa/adapters/` | Tool-specific read-only HTTP extraction |
| Model       | `backend/app/models/`             | Pydantic v2 request/response schemas |

## API Surface

### Health and Platform
- `GET  /api/health`
- `GET  /api/feature-flags`

### Hub v1 endpoints
- `POST /api/v1/validate` — probe a tool's health endpoint
- `POST /api/v1/crawl` — single-tool extraction → XLSX
- `POST /api/v1/assess` — maturity assessment → HTML/JSON
- `POST /api/v1/rca` — root cause analysis → HTML report
- `POST /api/v1/obsco/chat` — ObsCo Q&A (knowledge base + optional Claude)

### AYOSA (live investigation copilot)
- `POST /api/ayosa/chat` — synchronous investigation (set `agent_mode=true` to use the planner/dispatcher/synthesiser loop)
- `POST /api/ayosa/chat/stream` — Server-Sent Events stream. Deterministic events (`step`, `llm_chunk`, `result`, `error`) plus agent-mode events (`session_start`, `intent`, `plan`, `tool_start`, `tool_result`, `observation`, `chart`, `timeline_event`, `final_snapshot`, `done`)
- `POST /api/ayosa/runbook` — generate a runbook from an investigation result
- `GET  /api/ayosa/runs` — list persisted runs (filter by `session_id`, `service`, `limit`); degrades to `{available:false}` if persistence is disabled
- `GET  /api/ayosa/runs/{run_id}` — fetch one persisted run
- `GET  /api/ayosa/runs/compare?left=&right=` — field-level diff between two runs

### Other platform endpoints
- `POST /api/observability-gap-map`
- `POST /api/red-intelligence`

### Legacy compatibility endpoints
- `POST /api/export`
- `POST /api/assess`

### Artifacts
- `GET /api/download/runtime/{path}`

## Module Notes

### ObsCo — Observability Copilot (always-on)

ObsCo is a floating chat widget rendered globally at the bottom-right of every
view. It is fully self-contained (lives in `accelerators/obsco/`) and never
imports from any other accelerator or adapter.

- Built-in knowledge base of 12 tools (purpose, endpoints, auth methods, sample queries, troubleshooting tips, doc links)
- Detects tools mentioned in the user's question and intersects with configured tools
- Works offline (deterministic answer from the knowledge base)
- If the user supplies an Anthropic API key in the widget, the answer is enhanced by Claude grounded in the same tool facts
- Honours the `obsco` feature flag (auto-hides when disabled)

### AYOSA — Ask Your Observability Stack Anything

AYOSA is a chat-driven investigation copilot:

- Pulls live signals from connected tools (metrics, logs, traces, alerts, dashboards)
- Plans tool queries from the user's intent (11 intent types) via a 12-tool structured registry
- Streams investigation steps and LLM-generated narrative via SSE
- Generates RCA summaries, evidence timelines, charts, and runbooks
- Lives in `accelerators/ayosa/` with its own router, models, service, adapters, and LLM analyst

AYOSA agent-mode add-ons (`agent_mode=true`):

- **Session memory** (`agent/session_store.py`) — `SessionStore` persists turns under `runtime/ayosa_sessions/` and infers blank `service` / `time_range` from prior turns in the same `session_id`.
- **Workspace awareness** (`workspace_index/`) — lightweight JSON-on-disk index of services, dashboards, alerts, metrics, log indexes, traces, tools, and owners harvested from ObsCrawl / ObservaScore artifacts. Indexed with `index_workspace(run_id, artifact_path)`; queried via `search_workspace`, `get_service_context`, and `workspace_overview`. The planner attaches `workspace_context` to every plan; when the index is empty the response says `workspace index unavailable` and never fabricates facts.
- **Run persistence** (`persistence/`) — SQLite (`runtime/ayosa.db`) with `ayosa_sessions`, `ayosa_messages`, `ayosa_runs`, `ayosa_tool_steps`, `ayosa_snapshots`. Every agent-mode chat is recorded best-effort via `persist_agent_result`; chat keeps working when persistence fails. Surfaces through `GET /api/ayosa/runs[/{id}|/compare]`.

### Observability Gap Map

Coverage layer:
- Metrics, logs, traces, dashboards, alerts, and RED readiness per service
- Coverage scoring, readiness bands, missing-signal recommendations

Signal Connectivity layer:
- Separate section in analysis/report, not merged into the coverage matrix
- Service-level checks: `metrics_to_logs`, `logs_to_traces`, `alerts_to_dashboards`, `dashboards_to_logs`, `dashboards_to_traces`
- Deterministic scoring: `PASS = 100`, `WARN = 60`, `FAIL = 0`
- MTTR risk classification: `low` (≥ 80), `medium` (50–79), `high` (< 50)
- JSON fields: `connectivity_results`, `connectivity_summary`

### RCA Agent

- Multi-tool signal collection (Prometheus, Grafana, Jaeger, OpenSearch)
- Anomaly correlation engine + cascade/blast-radius BFS through service graph
- Claude-formatted RCA JSON rendered via Jinja2 template
- Runs inline (no subprocess) — service adds `accelerators/rca-agent/src` to `sys.path`

### MCP Server (experimental)

An opt-in Model Context Protocol stdio server that exposes AYOSA tools to MCP
clients such as Claude Desktop, VS Code, and Cursor. Lives in `mcp_server/`.

Exposed tools:

- `query_prometheus`, `query_elasticsearch`, `query_splunk`, `query_alertmanager`, `query_jaeger`
- `ayosa_investigate` — full agent loop across one or more tools
- `ayosa_search_workspace` — query the workspace index
- `ayosa_get_run` — fetch a persisted run by id

Key properties:

- The `mcp` Python package is **optional**. `mcp_server.schemas` and `mcp_server.tools` import cleanly without it.
- `python -m mcp_server.server` prints an install hint and exits `2` when `mcp` is missing — never affects any other AYOSA functionality.
- All `auth_token` / `api_key` / `password` / `secret` / `token` / `bearer` / `authorization` fields are flagged `sensitive: true` in the JSON Schema and scrubbed by `redact_for_log` before any log emission.
- Tool handlers reuse the existing `accelerators/ayosa/registry.py` adapters and `agent_bridge.run_agent_chat` — no business-logic duplication.

See [mcp_server/README.md](mcp_server/README.md) for client wiring (Claude Desktop / VS Code / Cursor).

## Frontend Hub

Tile inventory (each tile has a colour theme):

| Tile id                    | Module                     | Colour |
|----------------------------|----------------------------|--------|
| `obscrawl`                 | ObsCrawl                   | teal   |
| `observascore`             | ObservaScore               | indigo |
| `rca_agent`                | RCA Agent                  | amber  |
| `red_panel_intelligence`   | RED Panel Intelligence     | rose   |
| `observability_gap_map`    | Observability Gap Map      | cyan   |
| `ayosa`                    | AYOSA                      | violet |
| `obsco` *(floating)*       | ObsCo Copilot              | emerald|

Feature flags control tile visibility AND backend availability via:
- `studio_platform/config/feature_flags.yaml`
- Middleware enforcement in `backend/app/main.py` (`enforce_feature_flags`)

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

The UI runs on `:5173` and points to `${VITE_API_BASE_URL or http://10.235.21.132:8001}` by default.

### Docker

A `Dockerfile.backend` and `docker-compose.yml` are provided at the repo root
for containerised local runs. The `ui/Dockerfile` builds the React app.

## Configuration

- Runtime extraction config is generated per execution under `runtime/<run_id>/`.
- Tool connection details are submitted from UI payloads and converted into runtime config files by backend services.
- Splunk-specific URL derivation and auth mapping are supported in the config builder/service payload path.
- AI API keys (Anthropic) are passed per-request in the payload — never stored persistently.
- Tool TLS verification is intentionally disabled (`verify=False`) because the lab uses self-signed certs.

## Outputs

Generated artifacts are written to `runtime/<run_id>/<module>/` and typically include:
- HTML report (primary preview/download target)
- JSON report (structured output)
- XLSX export (for crawl/export workflows)

## Repository Map

```
accelerators/
  ayosa/             # AYOSA chat copilot: router, service, adapters, LLM analyst
    agent/             # Planner, dispatcher, synthesiser, tool_registry, session_store
    workspace_index/   # JSON-on-disk index of services/dashboards/alerts/metrics/...
    persistence/       # SQLite repository for run history (runtime/ayosa.db)
  obsco/             # ObsCo Q&A copilot (knowledge base + optional LLM)
  obscrawl/          # ObsCrawl single-tool extraction (re-exports from crawler service)
  observascore/      # Maturity scoring engine, COM model, adapters, rules, AI analyst, CLI
  rca-agent/         # RCA agent: signal collector, correlation, cascade detector, LLM formatter
mcp_server/          # Experimental MCP stdio server exposing AYOSA tools (optional mcp dep)
backend/app/
  main.py            # FastAPI app, CORS, feature-flag middleware
  routes/            # systems, export, assess, red_intelligence, observability_gap_map,
                     # download, feature_flags, v1/{validate,crawl,assess,rca,obsco}
  services/          # crawler, scoring, rca, red_intelligence, observability_gap_map services
  models/            # Pydantic v2 request/response schemas
  config/            # tools.yaml — tool catalogue
shared_core/         # feature_flags loader, accelerator registry, shared models/connectors
studio_platform/
  config/
    feature_flags.yaml   # accelerator on/off switches
  service_api/           # obscrawl + observascore service wrappers
  cli/                   # platform CLI entry
ui/src/
  api.js                 # axios client; all endpoint exports live here
  App.jsx                # root: mounts HubPage + global <ObsCoBot />
  components/
    HubPage.jsx          # tile grid + feature-flag filtering + modal orchestration
    CrawlModal.jsx, AssessModal.jsx, RCAModal.jsx
    RedIntelligenceModal.jsx, GapMapModal.jsx, AYOSAModal.jsx
    AyosaChartCard, AyosaChatMessage, AyosaChatShell, AyosaEvidenceCard,
    AyosaIncidentSnapshot, AyosaTimeline
    ObsCoBot.jsx         # floating bottom-right copilot widget
    GlobalToolConnectivity.jsx
  styles.css             # design tokens + all component styles
runtime/                  # generated outputs (git-ignored)
tests/                    # pytest smoke + insight tests
```

## CLI (ObservaScore standalone)

The ObservaScore engine is also installable as a CLI via `pyproject.toml`
(entry point `observascore`):

```bash
python -m observascore.cli assess --config config/config.example.yaml --ai
python -m observascore.cli export --config config/config.example.yaml
python -m observascore.cli check  --config config/config.example.yaml
python -m observascore.cli list-rules
```

## Testing

```bash
pytest tests/ -v
```

Current suite: **210 tests** covering:

- `tests/ayosa/` — agent backbone, chat contract, streaming contract, tool registry, session memory, workspace index, run persistence
- `tests/mcp/` — MCP tool-schema and redaction tests (no `mcp` dependency required)
- `tests/` — platform smoke, observability gap map, RED panel intelligence

## Notes

- All extraction is read-only toward source tools.
- Deterministic analysis is the baseline; AI is additive and optional everywhere it appears.
- Legacy `/api/export` and `/api/assess` endpoints are retained for backwards compatibility; new work should target the `/api/v1/*` routes.
- New accelerators should follow the pattern documented in `.claude/CLAUDE.md` and `.claude/rules/{api,backend,frontend}.md`.

