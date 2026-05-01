# Skill: Safe Refactor

**Purpose:** Modify existing code without breaking contracts, API surface, or downstream consumers.

---

## When to Use

- Renaming functions, classes, or variables
- Extracting shared logic into helpers
- Changing internal implementation while keeping the same external interface
- Reorganising module structure

---

## Steps

### 1. Apply analyze-module first
Run the **analyze-module** skill on every file you intend to change.
Know the input/output contract before touching anything.

### 2. Identify all callers (grep before editing)

```bash
# Find all call sites for a function
Grep pattern="function_name" path="." type="py"

# Find all imports of a module
Grep pattern="from module_path import" path="."

# Find all JSX component usages
Grep pattern="<ComponentName" path="ui/src"
```

Do not edit until you have a complete list of callers.

### 3. Define the change boundary

Classify the change:
- **Internal only** (private helpers, implementation details): safe to change without caller updates
- **Public API change** (function signature, return type, Pydantic model field): requires updating all callers
- **File rename / move**: requires updating all imports + pyproject.toml if it's a package

### 4. For public API changes — update in this order

1. Update the implementation
2. Update all callers (imports, call sites)
3. Update Pydantic models if the API contract changed
4. Update frontend `api.js` if the HTTP contract changed
5. Update `CLAUDE.md` module breakdown if a file was moved/renamed

### 5. Verify Pydantic model compatibility

```python
# If removing a field: make it Optional first, deploy, then remove
# If renaming a field: add the new name as Optional, keep the old one, migrate callers, then drop old
# Never remove a required field in one step
```

### 6. Adapter refactors — preserve the extract() contract

The `extract()` method must always return a dict with these top-level keys:
```python
{
  "alert_rules": [],
  "recording_rules": [],
  "scrape_targets": [],
  "signals": [],
  "dashboards": [],
  "datasources": [],
  "errors": [],
}
```
Even if a key is empty, it must be present. The `cli.py` and `ExcelExporter` iterate these keys unconditionally.

### 7. Run tests after each logical change

```bash
pytest tests/ -v
```

If tests don't cover the changed code, manually verify the affected endpoint:
```bash
curl -X POST http://localhost:8001/api/v1/validate \
  -H "Content-Type: application/json" \
  -d '{"tool_name":"prometheus","base_url":"http://localhost:9090"}'
```

### 8. Frontend refactors — check CSS class names

If renaming a CSS class:
```bash
# Find all usages before renaming
Grep pattern="old-class-name" path="ui/src"
```
Then update all JSX files that reference the class.

### 9. Verify feature flags still work

If moving or renaming a route:
- Update the path string in `backend/app/main.py` `enforce_feature_flags` middleware
- Test that disabling a flag returns 503

---

## Rollback Plan

Before any significant refactor:
```bash
git stash          # save current state
# make changes
git diff           # review all changes
pytest tests/ -v   # confirm tests pass
# if bad: git stash pop
```

---

## Anti-Patterns

- Renaming a Pydantic field without updating all JSON payload consumers
- Moving a Python module without fixing `pyproject.toml` package discovery
- Changing `extract()` return keys without updating `ExcelExporter` sheet mapping
- Changing a CSS class in `styles.css` without updating all JSX that uses it
- Removing a `tools.yaml` entry without checking if `crawler_service.py` references it
