# API Rules

Applies to: `backend/app/routes/**`, `backend/app/models/**`, `ui/src/api.js`

---

## Endpoint Inventory

| Method | Path | Purpose | Auth |
|---|---|---|---|
| GET | `/api/health` | Liveness probe | None |
| GET | `/api/feature-flags` | Feature flag map | None |
| POST | `/api/v1/validate` | Tool connectivity probe | None |
| POST | `/api/v1/crawl` | Single-tool data extraction → XLSX | None |
| POST | `/api/v1/assess` | Maturity assessment → HTML report | None |
| POST | `/api/v1/rca` | Root cause analysis → HTML report | None |
| GET | `/api/download/runtime/{path}` | Serve generated files | None |
| POST | `/api/export` | Multi-tool extraction (legacy) | None |
| POST | `/api/assess` | Assessment (legacy) | None |

**Prefer `/api/v1/*` routes for all new work.** Legacy routes are kept for backwards compat only.

---

## Request / Response Contracts

### POST /api/v1/validate
```json
Request:  { "tool_name": "prometheus", "base_url": "http://host:9090", "auth_token": null }
Response: { "tool_name": "prometheus", "reachable": true, "message": "...", "latency_ms": 42.1 }
```

### POST /api/v1/crawl
```json
Request:  { "tool_name": "grafana", "base_url": "http://host:3000", "auth_token": "eyJ..." }
Response: { "success": true, "message": "...", "download_url": "http://.../api/download/...", "run_id": "abc123" }
```

### POST /api/v1/assess
```json
Request:  {
  "tool_source": "prometheus", "api_endpoint": "http://host:9090", "auth_token": null,
  "use_ai": true, "ai_provider": "anthropic", "ai_api_key": "sk-ant-..."
}
Response: { "success": true, "message": "...", "download_url": "http://..." }
```

### POST /api/v1/rca
```json
Request: {
  "tools": [{ "tool_name": "prometheus", "base_url": "http://...", "auth_token": null }],
  "incident": {
    "service": "PaymentService", "alert_name": "HighLatency",
    "description": "p99 latency spiked", "time_window_minutes": 15
  },
  "ai_api_key": "sk-ant-...",
  "ai_model": "claude-sonnet-4-6"
}
Response: {
  "success": true, "message": "...", "download_url": "http://...", "run_id": "...",
  "anomaly_count": 5, "firing_alert_count": 2, "error_log_count": 47, "blast_radius": 3
}
```

---

## API Design Rules

1. **Noun-based URLs** — `/api/v1/rca` not `/api/v1/run-rca`
2. **POST for all operations** that have a request body (even reads with complex params)
3. **No versioned response changes** — add new fields as optional; never remove or rename
4. **download_url always absolute** — constructed as `{BASE_URL}/api/download/runtime/{rel_path}`
5. **Error responses**: always `{ "detail": "human-readable message" }` — FastAPI's default
6. **Timeouts**: tool probes = 15s; extraction = unlimited (subprocess); LLM = 120s default

## CORS Configuration (main.py)

Current allowed origins:
```python
allow_origins=["http://localhost:5173", "http://20.193.248.157:5173"]
```
When deploying to a new domain, add the new origin here. Do not use `allow_origins=["*"]` in production.

## Feature Flag Middleware (main.py)

Pattern to enforce flags:
```python
if path == "/api/v1/my-new-endpoint":
    _require_flag("my_accelerator")
```
Add this block in `enforce_feature_flags()` whenever adding a new accelerator route.

## Pydantic Model Conventions

```python
# models/my_thing.py
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel

class MyThingRequest(BaseModel):
    required_field: str
    optional_field: Optional[str] = None
    list_field: list[str] = []

class MyThingResponse(BaseModel):
    success: bool
    message: str
    download_url: Optional[str] = None
    run_id: Optional[str] = None
```

- Request models: named `<Resource>Request`
- Response models: named `<Resource>Response`
- All optional fields default to `None` or empty collection
- Always `Optional[str] = None` not `str | None = None` in models (Pydantic compatibility)

## Download URL Construction

```python
# Always use this pattern in services
rel = html_path.relative_to(RUNTIME_DIR)
download_url = f"{BASE_URL}/api/download/runtime/{rel.as_posix()}"
```

`BASE_URL = "http://20.193.248.157:8000"` — hardcoded in both `crawler_service.py` and `scoring_service.py`. If changing the host, update both files (TODO: move to env var).

## Frontend API Client (ui/src/api.js)

```js
// Adding a new endpoint — always append here, never inline in components
export const v1MyThing = (payload) => api.post("/v1/my-thing", payload);
```

The `api` axios instance has `baseURL = "${API_HOST}/api"`. Paths passed to `api.post()` are relative to `/api` — so `/v1/rca` maps to `/api/v1/rca`.

## Authentication

- **Tool auth**: passed per-request as `auth_token` (Bearer) or `api_key`
- **Backend**: no auth layer on FastAPI — public by design (lab/demo environment)
- **AI keys**: passed in request body for ObservaScore and RCA; never stored persistently
- **TLS**: `verify=False` for tool connections (lab self-signed certs) — intentional
