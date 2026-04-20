# Backend Rules

Applies to: `backend/app/**`, `accelerators/*/src/**`, `shared_core/**`

---

## Layer Responsibilities

| Layer | File pattern | Allowed to do | Must NOT do |
|---|---|---|---|
| **Route** | `routes/v1/*.py` | Parse request, call one service function, raise HTTPException | Business logic, DB calls, file I/O |
| **Service** | `services/*.py` | Orchestrate accelerator calls, write to `runtime/`, return structured dict | Import from other services, UI concerns |
| **Accelerator** | `accelerators/*/src/*.py` | Core logic, adapter calls, AI calls | Import from `backend/` |
| **Adapter** | `observascore/adapters/*.py` | HTTP GET to tool, normalize to COM | Write files, call Claude, import FastAPI |
| **Model** | `models/*.py` | Pydantic schemas only | Logic, I/O |

---

## Service Layer Rules

```python
# CORRECT — async service delegates blocking work to thread pool
async def run_something(req: MyRequest) -> MyResponse:
    result = await asyncio.to_thread(_sync_blocking_fn, req)
    return MyResponse(**result)

# WRONG — blocks the event loop
async def run_something(req: MyRequest) -> MyResponse:
    result = requests.get(...)   # blocks!
    return MyResponse(**result)
```

- Every public service function must be `async def`
- Blocking code (requests, file I/O, subprocess) → `asyncio.to_thread()`
- Subprocess calls: use `asyncio.create_subprocess_exec`, await `.communicate()`
- Always create `runtime/<run_id>/` dir via `workdir.mkdir(parents=True, exist_ok=True)`
- Return plain `dict` from services; route handlers convert to Pydantic response model

## Route Rules

```python
# CORRECT — thin handler
@router.post("/rca", response_model=RCAResponse)
async def run_rca_analysis(req: RCARequest) -> RCAResponse:
    if not req.tools:
        raise HTTPException(status_code=422, detail="At least one tool required.")
    try:
        result = await run_rca(req)
        return RCAResponse(**result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
```

- One service call per handler; no logic before/after
- Always `raise ... from exc` to preserve traceback
- Validate business rules (empty lists, missing fields) with `HTTPException(422)`
- Server errors → `HTTPException(500)`

## Adding a New Tool

1. Create `accelerators/observascore/adapters/<toolname>.py` extending `BaseAdapter`
2. Implement `health_check() -> bool` and `extract() -> dict`
3. Register in `observascore/cli.py` adapter map
4. Add entry to `backend/app/config/tools.yaml`:
   ```yaml
   toolname:
     display_name: "Tool Display Name"
     category: metrics|logs|traces|apm|platform
     health_endpoint: "/api/health"
     auth_methods: [none, bearer]
     capabilities: [metrics, alerts]
     default_port: 9090
     timeout_seconds: 15
   ```
5. No frontend changes needed — tool options are static lists in modal components

## Adding a New Accelerator

1. `accelerators/<name>/src/` — core logic
2. `backend/app/models/<name>.py` — Pydantic request/response
3. `backend/app/services/<name>_service.py` — `async def run_<name>(req)`
4. `backend/app/routes/v1/<name>.py` — router with `@router.post("/<name>")`
5. Register router in `backend/app/routes/v1/__init__.py`
6. Add feature flag enforcement in `backend/app/main.py` middleware
7. Add `<name>: true` to `platform/config/feature_flags.yaml`

## Pydantic v2 Rules

```python
# Correct v2 usage
model.model_dump()          # not model.dict()
model.model_validate(data)  # not model.parse_obj(data)
MyModel(**data)             # construction is unchanged

# Field declaration
class Foo(BaseModel):
    items: list[str] = []          # OK
    token: str | None = None       # OK (not Optional[str])
```

## Error Handling Pattern

```python
# Adapter level — raise AdapterError
try:
    resp = self.session.get(url, timeout=self.timeout)
    resp.raise_for_status()
    return resp.json()
except requests.exceptions.RequestException as e:
    raise AdapterError(f"{self.tool_name} request failed: {e}") from e

# Service level — log + return error dict or re-raise
try:
    result = agent.run(incident)
except Exception as exc:
    logger.error("Agent failed: %s", exc, exc_info=True)
    raise  # let route handler convert to HTTPException

# Route level — convert to HTTPException
except Exception as exc:
    raise HTTPException(status_code=500, detail=str(exc)) from exc
```

## Path Handling

```python
# Always use pathlib — never os.path or string joins
from pathlib import Path
RUNTIME_DIR = Path("runtime")
workdir = RUNTIME_DIR / run_id / "rca"
workdir.mkdir(parents=True, exist_ok=True)
html_path = workdir / f"report-{ts}.html"
```

## sys.path for RCA Agent

The RCA agent is imported directly (not via subprocess). The service adds its source to sys.path:
```python
_RCA_SRC = Path(__file__).resolve().parents[3] / "accelerators" / "rca-agent" / "src"
if str(_RCA_SRC) not in sys.path:
    sys.path.insert(0, str(_RCA_SRC))
```
Do this insertion in the service file, not inside the agent itself.
