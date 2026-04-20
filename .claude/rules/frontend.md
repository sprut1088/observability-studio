# Frontend Rules

Applies to: `ui/src/**`

---

## Component Conventions

- **Functional components only** — no class components, no `React.Component`
- **One component per file** — file name = component name (PascalCase)
- **State**: `useState` for local UI state; `useEffect` for side effects only
- **No external state library** — no Redux, Zustand, or Context for now

## File Map

```
ui/src/
├── api.js              # ALL HTTP calls — only place that imports axios
├── components/
│   ├── HubPage.jsx     # Tile grid; feature-flag filtering; modal orchestration
│   ├── CrawlModal.jsx  # ObsCrawl (teal theme)
│   ├── AssessModal.jsx # ObservaScore (indigo theme)
│   └── RCAModal.jsx    # RCA Agent (amber theme)
├── styles.css          # Single CSS file — all tokens + all component styles
├── App.jsx             # Root; renders HubPage
└── main.jsx            # ReactDOM.createRoot entry
```

## API Calls

```jsx
// CORRECT — import from api.js
import { v1Validate, v1Rca, API_HOST } from "../api";
const res = await v1Validate({ tool_name, base_url, auth_token });

// WRONG — never import axios directly in components
import axios from "axios";
const res = await axios.post("/api/v1/validate", ...); // Don't do this
```

Always use the named exports from `api.js`. When adding a new endpoint, add it to `api.js` first, then import in the component.

## New API Endpoint Checklist

1. Add to `ui/src/api.js`:
   ```js
   export const v1MyThing = (payload) => api.post("/v1/my-thing", payload);
   ```
2. Import in the component that uses it
3. Handle `err?.response?.data?.detail || err.message` in catch blocks

## Download Pattern

```jsx
function triggerDownload(downloadPath) {
  if (!downloadPath) return;
  const url = downloadPath.startsWith("http") ? downloadPath : `${API_HOST}${downloadPath}`;
  const a = document.createElement("a");
  a.href = url; a.download = ""; a.style.display = "none";
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
}
```
Copy this function into any modal that needs to trigger a file download. Do not modify the pattern.

## Styling Rules

### Colour Themes per Accelerator

| Accelerator | CSS prefix | Colour variable |
|---|---|---|
| ObsCrawl | `teal` | `var(--teal)` `#06b6d4` |
| ObservaScore | `indigo` | `var(--accent)` `#6366f1` |
| RCA Agent | `amber` | `var(--amber)` `#f59e0b` |

Each accelerator tile needs: `tile-<colour>`, `badge-<colour>`, `btn-<colour>`, `modal-header-<colour>`, `tile-<colour>-cta` in `styles.css`.

### CSS Conventions

```css
/* Tokens: always use variables, never raw hex in components */
color: var(--text-primary);    /* NOT #1e2235 */
background: var(--bg-card);    /* NOT #ffffff */

/* Dark mode: add override when adding a new light-mode variable */
html[data-theme="dark"] .my-component { background: rgba(255,255,255,0.03); }

/* Class naming: kebab-case, component-prefixed */
.rca-step-label { … }
.rca-incident-grid { … }
.mtool-add-bar { … }      /* mtool = multi-tool, shared by all modals */
```

### Shared Modal Structure

All modals share:
- `.modal-overlay` → `.modal.modal-wide` (role=dialog)
- `.modal-header.modal-header-<colour>` → icon + title + subtitle + close button
- `.modal-body` → content
- `.modal-footer` → Cancel + Validate All + Primary action

Do not restructure this layout — the shared CSS depends on it.

### Busy State

```jsx
// All modals use this pattern — do not deviate
const busy = validatingId !== null || validatingAll || running;

// Disable ALL interactive elements when busy
<button disabled={busy}>...</button>
<input disabled={busy} />
<select disabled={busy} />
```

## Feature Flags in Frontend

```jsx
// HubPage loads flags once; tiles filtered by flags[tile.id] !== false
// Feature flag key must match tile.id exactly
{ id: "rca_agent", ... }   // → flags["rca_agent"] in feature_flags.yaml
```

When adding a new accelerator tile:
1. Add TILES entry in `HubPage.jsx` with correct `id` matching the YAML key
2. Add default value in the `useState` initialiser: `{ observascore: true, obscrawl: true, rca_agent: true, my_new: true }`
3. Add `{activeTile === "my_new" && <MyModal onClose={...} />}` in render

## Tool List Constants

`TOOL_OPTIONS` and `TOOL_ICONS` are defined in each modal. If you add a new tool, add it to **both** `CrawlModal.jsx` and `AssessModal.jsx` (and `RCAModal.jsx` if applicable). They are intentionally kept in sync manually — there is no shared source of truth for these arrays.

## Error Display Pattern

```jsx
// Status state shape — use this exact shape for all modals
const [status, setStatus] = useState(null); // { type, title, msg }

// Display
{status && (
  <div className={`modal-alert modal-alert-${status.type} animate-in`}>
    <span className="modal-alert-icon">{status.type === "success" ? "✓" : "✗"}</span>
    <div>
      <div className="modal-alert-title">{status.title}</div>
      <div className="modal-alert-msg">{status.msg}</div>
    </div>
  </div>
)}
```
