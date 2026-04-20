# Observability Studio — Claude Project Memory

## Project Overview

**Observability Studio** is a multi-accelerator SRE platform that crawls observability tools, scores maturity, and performs AI-driven root cause analysis. It exposes a FastAPI backend and a React+Vite frontend. All three accelerators share a common adapter pattern and a feature-flag gate.

**Live instance:** `http://20.193.248.157:8000` (backend) / `:5173` (UI)

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend API | Python 3.10+, FastAPI, Pydantic v2, uvicorn |
| Accelerator core | Python, click CLI, Jinja2, openpyxl, anthropic SDK |
| Frontend | React 19, Vite 8, Axios, vanilla CSS |
| AI | Anthropic Claude (`claude-sonnet-4-6` default) |
| Config | YAML (`tools.yaml`, `feature_flags.yaml`, `config.example.yaml`) |
| Testing | pytest (`tests/`) |

---

## Repository Layout

```
observability-studio/
├── accelerators/
│   ├── observascore/          # Maturity scoring engine + adapters
│   │   ├── adapters/          # Per-tool adapters (prometheus, grafana, jaeger …)
│   │   ├── model/__init__.py  # Common Observability Model (COM) dataclasses
│   │   ├── rules/             # YAML rule packs + Python check functions
│   │   ├── engine/scoring.py  # Mathematical scoring (100-point scale)
│   │   ├── ai/analyst.py      # Claude / Azure OpenAI gap analysis
│   │   ├── export/excel.py    # Multi-sheet XLSX export
│   │   ├── report/generator.py# Jinja2 HTML + JSON reports
│   │   └── cli.py             # click CLI: assess | export | check | list-rules
│   ├── obscrawl/              # Single-tool crawl & Excel export
│   │   └── service.py         # Re-exports from crawler_service
│   └── rca-agent/
│       ├── src/
│       │   ├── signal_collector.py  # Pulls Prometheus/Grafana/Jaeger/OpenSearch signals
│       │   ├── correlation_engine.py# Ranks anomalies → AnomalyFinding
│       │   ├── cascade_detector.py  # BFS blast-radius through service graph
│       │   ├── llm_formatter.py     # Claude → structured RCA JSON + HTML render
│       │   └── rca_agent.py         # Orchestrator; writes HTML to runtime/
│       ├── config/
│       │   ├── thresholds.yaml
│       │   └── correlation_rules.yaml
│       └── templates/rca_report_html.jinja2
├── backend/
│   └── app/
│       ├── main.py            # FastAPI app + CORS + feature-flag middleware
│       ├── models/            # Pydantic request/response schemas
│       │   ├── connection.py  # ConnectionSchema, ConnectionResponse
│       │   ├── assessment.py  # AssessmentRequest, AssessmentResponse
│       │   └── rca.py         # RCARequest, RCATool, RCAIncident, RCAResponse
│       ├── routes/
│       │   ├── v1/            # Preferred Hub v1 API
│       │   │   ├── __init__.py   # Registers validate, crawl, assess, rca routers
│       │   │   ├── validate.py   # POST /api/v1/validate
│       │   │   ├── crawl.py      # POST /api/v1/crawl
│       │   │   ├── assess.py     # POST /api/v1/assess
│       │   │   └── rca.py        # POST /api/v1/rca
│       │   ├── export.py         # POST /api/export  (legacy multi-tool)
│       │   ├── assess.py         # POST /api/assess  (legacy)
│       │   ├── download.py       # GET  /api/download/runtime/{path}
│       │   └── feature_flags.py  # GET  /api/feature-flags
│       ├── services/
│       │   ├── crawler_service.py # validate_connection(), run_crawl()
│       │   ├── scoring_service.py # run_scoring() → spawns observascore CLI
│       │   └── rca_service.py     # run_rca() → calls RCAAgent inline
│       └── config/tools.yaml  # Tool catalogue (health_endpoint, auth_methods …)
├── platform/
│   └── config/feature_flags.yaml  # observascore | obscrawl | rca_agent → bool
├── shared_core/
│   └── feature_flags/__init__.py  # load_feature_flags()
├── ui/src/
│   ├── api.js                # Axios client; exports v1Validate, v1Crawl, v1Assess, v1Rca
│   ├── components/
│   │   ├── HubPage.jsx       # Tile grid + feature-flag filtering
│   │   ├── CrawlModal.jsx    # ObsCrawl modal (teal theme)
│   │   ├── AssessModal.jsx   # ObservaScore modal (indigo theme)
│   │   └── RCAModal.jsx      # RCA Agent modal (amber theme)
│   └── styles.css            # Design system tokens + all component styles
├── runtime/                  # Auto-created; one subdir per run_id
├── tests/test_smoke.py
└── pyproject.toml            # Package: observascore, entry point `observascore`
```

---

## Architecture

### Request Flow (all three accelerators)

```
Browser (React)
  → POST /api/v1/{validate|crawl|assess|rca}
  → FastAPI route (routes/v1/)
  → Service layer (services/*.py)
  → Accelerator logic (accelerators/*/...)
  → Write artifact to runtime/<run_id>/
  → Return download_url → GET /api/download/runtime/...
  → Browser downloads file
```

### Feature Flag Gate (middleware in main.py)

```
enforce_feature_flags middleware:
  /api/v1/{crawl,validate}, /api/export → require flag "obscrawl"
  /api/{assess}, /api/v1/assess         → require flag "observascore"
  /api/v1/rca                           → require flag "rca_agent"
```

Flags live in `platform/config/feature_flags.yaml`.

### Adapter Pattern (observascore)

```
BaseAdapter (adapters/base.py)
  ├── PrometheusAdapter   — /api/v1/{targets,rules,query}
  ├── GrafanaAdapter      — /api/{folders,datasources,search,ruler/…}
  ├── JaegerAdapter       — /api/{services,traces}
  ├── ElasticsearchAdapter— /_cluster/health, /_cat/indices
  └── … (Loki, Tempo, Datadog, Dynatrace, AppDynamics, OtelCollector)
All return dict that is merged into ObservabilityEstate (COM dataclasses)
```

### RCA Agent Pipeline

```
SignalCollector → CollectedSignals
CorrelationEngine → CorrelationResult (ranked AnomalyFinding list)
CascadeDetector → blast-radius dict
LLMFormatter → Claude RCA JSON → Jinja2 HTML
```

### Subprocess vs Inline

- **ObsCrawl / ObservaScore**: backend services spawn `python -m observascore.cli` subprocess
- **RCA Agent**: runs inline (`asyncio.to_thread`) — no subprocess; imports directly from `rca-agent/src/`

---

## Key Identifiers

| Concept | Value |
|---|---|
| Default Claude model | `claude-sonnet-4-6` |
| BASE_URL (hardcoded) | `http://20.193.248.157:8000` |
| API_HOST (frontend) | `http://20.193.248.157:8000` |
| Runtime artifacts | `runtime/<run_id>/{rca/,reports/,exports/}` |
| Observascore package | `accelerators/observascore/` installed via `pyproject.toml` |

---

## Build / Run Commands

```bash
# Backend (from repo root)
pip install -e .                          # install observascore package
pip install -r backend/requirements.txt  # FastAPI deps
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload

# Frontend
cd ui && npm install && npm run dev       # dev on :5173
cd ui && npm run build                    # production build

# Observascore CLI (standalone)
python -m observascore.cli assess --config config/config.example.yaml --ai
python -m observascore.cli export --config config/config.example.yaml
python -m observascore.cli check  --config config/config.example.yaml

# Tests
pytest tests/ -v
```

---

## Coding Conventions

### Python (backend + accelerators)

- **Imports**: `from __future__ import annotations` on every new file
- **Types**: full type hints on all public functions; `Optional[X]` via `X | None` syntax
- **Pydantic**: v2 style (`model.dict()` → `model.model_dump()`)
- **Async**: services are `async def`; blocking calls wrapped in `asyncio.to_thread()`
- **Error handling**: catch specific exceptions; never swallow silently; log with `logger.error()`
- **Logging**: `logger = logging.getLogger(__name__)` at module top
- **Path operations**: use `pathlib.Path` exclusively; never string concatenation for paths
- **Config**: read from YAML, never hardcode business logic values

### Frontend (React + Vite)

- **Components**: functional only; hooks allowed; no class components
- **State**: `useState` / `useEffect`; no external state library
- **API calls**: always via `ui/src/api.js` exports — never raw `axios` inline
- **Styling**: CSS classes from `styles.css`; no inline style objects except one-off layout tweaks
- **Theme colours**: teal=ObsCrawl, indigo=ObservaScore, amber=RCA Agent — do not mix

### Naming

| Thing | Convention |
|---|---|
| Python files | `snake_case.py` |
| Python classes | `PascalCase` |
| React components | `PascalCase.jsx` |
| CSS classes | `kebab-case` |
| API endpoints | `/api/v1/noun` (no verbs) |
| YAML keys | `snake_case` |

---

## Do / Don't

**DO:**
- Add new tools by creating an adapter in `observascore/adapters/` + entry in `tools.yaml`
- Add new accelerators by: new folder in `accelerators/`, new route + service in `backend/app/`, new feature flag, new modal + tile in frontend
- Keep service layer thin — route handlers call service, service calls accelerator
- Use `asyncio.to_thread()` for any synchronous blocking call inside an `async def`
- Check feature flags before adding any new backend route

**DON'T:**
- Never put business logic inside route handlers (`routes/v1/*.py`)
- Never import backend modules from accelerator code
- Never hardcode API keys — pass via request payload or env vars
- Never call `requests.*` directly in `async def` without `asyncio.to_thread`
- Never modify `pyproject.toml` package list without testing `pip install -e .`
- Never add new CSS variables without adding the dark-mode override

---

## Repo Workflow (safe modification sequence)

1. **Read** `CLAUDE.md` (this file) — understand scope
2. **Read** only the relevant module (adapter, service, or component) — not the whole repo
3. **Check** `tools.yaml` if touching tool integration
4. **Check** `feature_flags.yaml` if adding an accelerator
5. **Run** `pytest tests/` before and after changes
6. **Run** backend: verify `/api/health` returns `{"status":"ok"}`
7. **Check** frontend builds without errors: `cd ui && npm run build`
