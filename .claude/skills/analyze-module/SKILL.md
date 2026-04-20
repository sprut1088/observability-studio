# Skill: Analyze Module

**Purpose:** Understand a module's structure, contracts, and dependencies *before* writing any code. Prevents incorrect assumptions and avoids needing a full repo re-scan.

---

## When to Use

- Before modifying any existing accelerator, service, or component
- Before adding a new feature that touches an existing file
- When asked to "understand how X works"

---

## Steps

### 1. Read CLAUDE.md (if not already in context)
```
Read: .claude/CLAUDE.md
```
Locate the module in the **Repository Layout** section. Identify:
- Which layer it belongs to (adapter / service / route / component)
- What it depends on (other services, models, adapters)

### 2. Read the target file only
```
Read: <path/to/target_file.py or .jsx>
```
Do NOT read the entire directory. Read only the file you need to understand.

### 3. Check its direct dependencies (one level only)

For Python:
- If it imports from `observascore.model`, read: `accelerators/observascore/model/__init__.py` (first 80 lines cover all COM dataclasses)
- If it imports from `adapters/base`, read: `accelerators/observascore/adapters/base.py`
- If it uses `ConnectionSchema`, read: `backend/app/models/connection.py`

For JSX:
- If it imports from `../api`, read: `ui/src/api.js`
- If it uses CSS classes, grep `styles.css` for the class name

### 4. Identify the contract (inputs → outputs)

For backend service:
```
Input:  What Pydantic model does it accept?
Output: What dict/model does it return?
Side effects: Does it write to runtime/? Spawn subprocess? Call Claude?
```

For adapter:
```
extract() → what keys does the returned dict have?
health_check() → bool only
```

For React component:
```
Props: what does the component accept?
State: what useState calls exist?
API calls: which api.js exports does it use?
```

### 5. Check the config if tool-related

If modifying anything that touches a tool:
```
Read: backend/app/config/tools.yaml   (find the tool's entry)
```

### 6. Stop — you have enough context

Do not read sibling files "just in case". Make targeted reads only when a specific import or behaviour is unclear.

---

## Output of this Skill

After completing these steps you should know:
- What the module does
- What it expects as input
- What it produces
- What you need to change to implement your task
- What tests or downstream consumers might break

---

## Anti-Patterns to Avoid

- Reading all files in a directory to "orient yourself" — use CLAUDE.md instead
- Reading the CLI (`cli.py`) to understand adapters — they are independent
- Reading `main.py` to understand services — services are standalone
- Reading `styles.css` in full — grep for the specific class you need
