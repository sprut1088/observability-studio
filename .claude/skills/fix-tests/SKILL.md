# Skill: Fix Tests

**Purpose:** Diagnose and fix failing tests without unnecessary file exploration.

---

## When to Use

- `pytest` reports failures or errors
- A CI pipeline fails on the test step
- After a refactor, tests that previously passed now fail

---

## Test Inventory

```
tests/
└── test_smoke.py    # Smoke tests — import checks + basic endpoint assertions
```

Current test coverage is minimal (smoke only). Most validation is manual via the running backend.

---

## Steps

### 1. Run tests and capture output

```bash
pytest tests/ -v 2>&1
```

Read the full output. Identify:
- **ERROR** (collection/import failure) vs **FAILED** (assertion failure)
- Which test function failed
- The exact error message and traceback

### 2. Classify the failure

| Failure type | Likely cause | Where to look |
|---|---|---|
| `ImportError` / `ModuleNotFoundError` | Package not installed, or moved file | `pyproject.toml`, affected `__init__.py` |
| `AttributeError` on a Pydantic model | Field renamed or removed | `backend/app/models/*.py` |
| `404` on an endpoint | Route path changed or router not registered | `backend/app/routes/v1/__init__.py`, `main.py` |
| `503` | Feature flag disabled | `platform/config/feature_flags.yaml` |
| `422 Unprocessable Entity` | Request payload doesn't match Pydantic model | `backend/app/models/*.py` |
| `KeyError` in service | Dict key removed from accelerator output | `accelerators/*/src/*.py` service contract |
| Jinja2 `TemplateNotFound` | Template file missing or path wrong | `accelerators/*/templates/` |

### 3. Fix import errors first

If `ModuleNotFoundError: No module named 'observascore'`:
```bash
pip install -e .
```

If `ModuleNotFoundError: No module named 'signal_collector'` (RCA agent):
- Check that `_RCA_SRC` is in `sys.path` in `rca_service.py`
- Verify the file exists at `accelerators/rca-agent/src/signal_collector.py`

### 4. Fix assertion / HTTP failures

For endpoint failures:
1. Start the backend: `uvicorn backend.app.main:app --port 8001 --reload`
2. Test the failing endpoint manually with curl
3. Read the relevant route → service → accelerator chain (use analyze-module skill)
4. Fix at the lowest layer first (adapter → service → route)

### 5. Fix Pydantic validation errors

```bash
# Test payload manually
curl -X POST http://localhost:8001/api/v1/rca \
  -H "Content-Type: application/json" \
  -d '{"tools":[],"incident":{"service":"test"}}'
# Read the 422 detail to see which field failed validation
```

Check `backend/app/models/rca.py` — ensure the model matches what the frontend sends.

### 6. Fix feature flag issues

```bash
# Confirm flag is enabled
cat platform/config/feature_flags.yaml
```

If a test hits a 503:
- Either enable the flag for testing
- Or mock `load_feature_flags()` to return `{flag: True}`

### 7. Add a targeted test for the regression

After fixing:
```python
# tests/test_smoke.py pattern
def test_my_endpoint(client):
    resp = client.post("/api/v1/my-endpoint", json={...})
    assert resp.status_code == 200
    assert resp.json()["success"] is True
```

Use FastAPI's `TestClient`:
```python
from fastapi.testclient import TestClient
from backend.app.main import app
client = TestClient(app)
```

### 8. Verify fix

```bash
pytest tests/ -v             # all tests pass
pytest tests/ -k "my_test"  # run only the new/fixed test
```

---

## Common Quick Fixes

| Symptom | Fix |
|---|---|
| `observascore` not importable | `pip install -e .` from repo root |
| `runtime/` permission error | `mkdir -p runtime` or check working directory |
| Frontend build fails after CSS change | Check unclosed rule or invalid variable name in `styles.css` |
| RCA agent import fails | Verify `sys.path.insert` in `rca_service.py` uses correct path |
| Template not found | Check `_TEMPLATES_DIR` path in `llm_formatter.py` |
