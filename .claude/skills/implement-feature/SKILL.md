# Skill: Implement Feature

**Purpose:** Standard process for adding new features — from a blank-slate requirement to a working, wired implementation.

---

## When to Use

- Adding a new accelerator (new tile in the hub)
- Adding a new tool to an existing accelerator
- Adding a new field to an existing API endpoint
- Adding a new scoring rule or dimension to ObservaScore

---

## Decision Tree

```
New feature type?
│
├─ New accelerator (full tile)         → follow Section A
├─ New tool adapter                    → follow Section B
├─ New API field (extend existing)     → follow Section C
├─ New ObservaScore rule               → follow Section D
└─ New frontend-only feature           → follow Section E
```

---

## Section A: New Accelerator (Full Tile)

### Backend

**1. Create accelerator package**
```
accelerators/<name>/
├── src/
│   ├── __init__.py
│   └── <name>_agent.py     # Main orchestrator
└── templates/
    └── <name>_report.jinja2
```

**2. Create Pydantic models** (`backend/app/models/<name>.py`)
```python
class <Name>Request(BaseModel):
    tools: list[RCATool]   # reuse existing if same shape
    ...

class <Name>Response(BaseModel):
    success: bool
    message: str
    download_url: Optional[str] = None
    run_id: Optional[str] = None
```

**3. Create service** (`backend/app/services/<name>_service.py`)
```python
async def run_<name>(req: <Name>Request) -> dict:
    result = await asyncio.to_thread(_sync_fn, ...)
    return {"success": True, "download_url": ..., "run_id": ...}
```

**4. Create route** (`backend/app/routes/v1/<name>.py`)
```python
router = APIRouter()

@router.post("/<name>", response_model=<Name>Response)
async def run_<name>_handler(req: <Name>Request) -> <Name>Response:
    try:
        result = await run_<name>(req)
        return <Name>Response(**result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
```

**5. Register router** (`backend/app/routes/v1/__init__.py`)
```python
from .<name> import router as <name>_router
router.include_router(<name>_router)
```

**6. Add feature flag enforcement** (`backend/app/main.py`)
```python
if path == "/api/v1/<name>":
    _require_flag("<name>")
```

**7. Add feature flag** (`platform/config/feature_flags.yaml`)
```yaml
<name>: true
```

### Frontend

**8. Add API endpoint** (`ui/src/api.js`)
```js
export const v1<Name> = (payload) => api.post("/v1/<name>", payload);
```

**9. Create modal** (`ui/src/components/<Name>Modal.jsx`)
- Copy structure from `RCAModal.jsx` (most complete example)
- Change: theme colour, step labels, form fields, API call, payload shape

**10. Add CSS theme** (`ui/src/styles.css`)
- Add `--<colour>`, `--<colour>-dark`, `--<colour>-glow` variables in `:root`
- Add `.tile-<colour>`, `.badge-<colour>`, `.btn-<colour>`, `.modal-header-<colour>`, `.tile-<colour>-cta`
- Add dark-mode overrides

**11. Add tile** (`ui/src/components/HubPage.jsx`)
```jsx
import <Name>Modal from "./<Name>Modal";

// In TILES array:
{
  id: "<name>",                    // must match feature_flags.yaml key
  icon: "🔬",
  title: "<Display Name>",
  tagline: "Verb & Noun",
  description: "...",
  accentClass: "tile-<colour>",
  features: ["...", "...", "..."],
  badge: "Label",
  badgeClass: "badge-<colour>",
}

// In useState initialiser — add default true:
const [flags, setFlags] = useState({ observascore: true, obscrawl: true, rca_agent: true, <name>: true });

// In render:
{activeTile === "<name>" && <NameModal onClose={() => setActiveTile(null)} />}
```

---

## Section B: New Tool Adapter

1. Create `accelerators/observascore/adapters/<toolname>.py`
   - Extend `BaseAdapter`
   - Implement `health_check() -> bool` and `extract() -> dict`
   - `extract()` must return dict with all standard keys (see safe-refactor skill)

2. Add to `backend/app/config/tools.yaml` (required fields: `display_name`, `category`, `health_endpoint`, `auth_methods`, `capabilities`)

3. Register in `observascore/cli.py` adapter_map:
   ```python
   from observascore.adapters.<toolname> import <Tool>Adapter
   adapter_map["<toolname>"] = <Tool>Adapter
   ```

4. Add to `TOOL_OPTIONS` in `CrawlModal.jsx` and `AssessModal.jsx`

5. Add to `TOOL_ICONS` dict and `DEFAULT_USAGES` dict in both modals

---

## Section C: Extend Existing API Endpoint

1. **Add field to Pydantic model** — always `Optional` with a default:
   ```python
   class RCARequest(BaseModel):
       new_field: Optional[str] = None   # safe — won't break existing callers
   ```

2. **Use it in the service** — guard with `if req.new_field:`

3. **Update frontend payload** in the modal's handler function

4. **Add UI control** for the new field in the modal body

5. **Do NOT remove existing fields** — mark as deprecated with a comment first

---

## Section D: New ObservaScore Rule

1. Create check function in `accelerators/observascore/rules/checks.py` or vendor file:
   ```python
   @register("my_new_rule")
   def check_my_thing(estate: ObservabilityEstate) -> list[dict]:
       findings = []
       for alert in estate.alert_rules:
           if <condition>:
               findings.append({
                   "title": "...",
                   "description": "...",
                   "severity": "medium",  # critical|high|medium|low|info
                   "dimension": "alert_quality",  # one of 10 dimensions
                   "weight": 5,
               })
       return findings
   ```

2. Add rule metadata to the relevant pack YAML in `rules/packs/`:
   ```yaml
   - id: my_new_rule
     name: "Human readable name"
     description: "What this rule checks"
     dimension: alert_quality
     severity: medium
     weight: 5
   ```

3. No registration needed — `RulesEngine` auto-discovers `@register` decorators

---

## Section E: Frontend-Only Feature

1. Run the analyze-module skill on the target component
2. Make the change
3. Check CSS: grep for any new class in `styles.css` before adding it
4. Build test: `cd ui && npm run build`
5. Dev test: `npm run dev`, exercise the feature manually

---

## Verification Checklist (all features)

- [ ] `pytest tests/ -v` passes
- [ ] Backend health: `curl http://localhost:8000/api/health`
- [ ] New endpoint returns expected shape
- [ ] Feature flag disables the endpoint (returns 503)
- [ ] Frontend: tile appears when flag is true, hidden when false
- [ ] Modal opens, tool can be added, primary action works
- [ ] Report downloads and opens correctly
- [ ] Dark mode: new CSS elements look correct
- [ ] `cd ui && npm run build` succeeds without warnings
