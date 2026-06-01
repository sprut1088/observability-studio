# AYOSA MCP Server (experimental)

An experimental [Model Context Protocol](https://modelcontextprotocol.io)
stdio server that exposes AYOSA observability tools to MCP clients such as
**Claude Desktop**, **VS Code**, **Cursor**, and any other MCP-aware host.

This package is **optional** and isolated:

* It does NOT modify any existing `/api/ayosa/*` HTTP route.
* It does NOT require the `mcp` Python package to be installed for the
  rest of the codebase to work — `mcp_server.tools` and
  `mcp_server.schemas` are pure-Python and import cleanly without it.
* If you try to start the server without the `mcp` package, it prints a
  clear install hint and exits with code `2`.

---

## Exposed tools

| Tool | Description |
|---|---|
| `query_prometheus`     | Run an AYOSA investigation against a Prometheus endpoint |
| `query_elasticsearch`  | Run an AYOSA investigation against an Elasticsearch endpoint |
| `query_splunk`         | Run an AYOSA investigation against a Splunk endpoint |
| `query_alertmanager`   | Run an AYOSA investigation against an Alertmanager endpoint |
| `query_jaeger`         | Run an AYOSA investigation against a Jaeger endpoint |
| `ayosa_investigate`    | Run the full AYOSA agent across one or more tools |
| `ayosa_search_workspace` | Search the AYOSA workspace index |
| `ayosa_get_run`        | Fetch one persisted AYOSA run by id |

All tool inputs that carry credentials (`auth_token`) are flagged as
sensitive in the JSON Schema and are scrubbed before being written to logs
(see `mcp_server.schemas.redact_for_log`).

---

## Install

```bash
pip install mcp        # required only to RUN the server
```

The existing `pip install -e .` is enough for tests and schema imports.

---

## Run

```bash
python -m mcp_server.server
```

The server speaks MCP over stdio. Stop with `Ctrl+C`.

If `mcp` is not installed:

```text
The optional 'mcp' Python package is not installed.
Install it with:
    pip install mcp
Then re-run:
    python -m mcp_server.server
```

---

## Wiring into MCP clients

### Claude Desktop (`~/.config/Claude/claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "ayosa": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/absolute/path/to/observability-studio"
    }
  }
}
```

### VS Code / Cursor

Most MCP-aware extensions take a `command` + `args` pair. Use the same
values as above.

---

## Layout

```
mcp_server/
├── __init__.py    # Re-exports TOOL_SCHEMAS, TOOL_HANDLERS, TOOL_NAMES
├── schemas.py     # Pydantic + JSON Schema definitions (no mcp dep)
├── tools.py       # Tool implementations (no mcp dep)
├── server.py      # Stdio entry point (requires `mcp` to actually run)
└── README.md      # This file
```

`schemas.py` and `tools.py` are deliberately MCP-package-free so they can
be unit-tested and reused (e.g. behind an HTTP shim) without the SDK.

---

## Security notes

* `auth_token`, `api_key`, `password`, `secret`, `token`, `bearer` and
  `authorization` are treated as sensitive by `redact_for_log`. The
  central log call inside every tool handler uses it.
* Tool handlers never raise into the MCP layer — failures are returned
  as `{"ok": false, "error": "<message>"}`.
* The server runs only over stdio; there is no network listener.
