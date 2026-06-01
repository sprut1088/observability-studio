"""Pure-Python tool schemas for the AYOSA MCP server.

This module is **MCP-package-free** so it can be imported and tested
without the optional `mcp` dependency installed. The shapes are
expressed twice:

1. Pydantic models (`*Input`) — used for runtime validation inside
   `mcp_server.tools`.
2. JSON Schema dicts (`TOOL_SCHEMAS`) — the contract the MCP server
   advertises to clients (Claude Desktop, Cursor, VS Code, …).

Sensitive fields are tagged via `SENSITIVE_INPUT_FIELDS`. The MCP
server (and any logger that handles a tool payload) MUST consult that
set before writing input to logs.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


# ──────────────────────────────────────────────────────────────────────── #
# Field-name registry — values for these keys must never hit logs verbatim.
# ──────────────────────────────────────────────────────────────────────── #
SENSITIVE_INPUT_FIELDS: frozenset[str] = frozenset({
    "auth_token", "api_key", "password", "secret", "token",
    "bearer", "authorization",
})

REDACTED_PLACEHOLDER: str = "***redacted***"


# ──────────────────────────────────────────────────────────────────────── #
# Per-adapter inputs
# ──────────────────────────────────────────────────────────────────────── #
class _ToolQueryInputBase(BaseModel):
    """Shared shape for `query_<tool>` MCP calls."""

    base_url: str = Field(
        ..., description="Root URL of the tool (no trailing slash).",
    )
    auth_token: Optional[str] = Field(
        None,
        description="Bearer / API token. Treated as sensitive; never logged.",
    )
    service: Optional[str] = Field(
        None, description="Service to investigate (optional).",
    )
    time_range: str = Field(
        "30m", description="Lookback window, e.g. '15m', '1h', '24h'.",
    )
    message: str = Field(
        "", description="Free-text question or intent description.",
    )


class QueryPrometheusInput(_ToolQueryInputBase):
    pass


class QueryElasticsearchInput(_ToolQueryInputBase):
    pass


class QuerySplunkInput(_ToolQueryInputBase):
    pass


class QueryAlertmanagerInput(_ToolQueryInputBase):
    pass


class QueryJaegerInput(_ToolQueryInputBase):
    pass


# ──────────────────────────────────────────────────────────────────────── #
# AYOSA-level inputs
# ──────────────────────────────────────────────────────────────────────── #
class _ToolConfigInput(BaseModel):
    tool: str = Field(..., description="Adapter key, e.g. 'prometheus'.")
    base_url: str = Field(..., description="Root URL of the tool.")
    auth_token: Optional[str] = Field(
        None,
        description="Bearer / API token. Treated as sensitive; never logged.",
    )


class AyosaInvestigateInput(BaseModel):
    message: str = Field(..., description="Question or incident description.")
    service: Optional[str] = Field(None, description="Service under investigation.")
    time_range: str = Field("30m", description="Lookback window.")
    tools: list[_ToolConfigInput] = Field(
        ...,
        min_length=1,
        description="Tool endpoints to query (at least one required).",
    )
    session_id: Optional[str] = Field(
        None, description="Optional session id to continue a prior chat.",
    )


class AyosaSearchWorkspaceInput(BaseModel):
    query: str = Field(..., description="Free-text search query.")
    limit: int = Field(10, ge=1, le=100, description="Max results to return.")


class AyosaGetRunInput(BaseModel):
    run_id: str = Field(..., min_length=1, description="Persisted run id.")


# ──────────────────────────────────────────────────────────────────────── #
# JSON Schema export — what MCP clients see
# ──────────────────────────────────────────────────────────────────────── #
def _query_tool_schema(name: str, description: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": {
                "base_url": {
                    "type": "string",
                    "description": "Root URL of the tool.",
                },
                "auth_token": {
                    "type": "string",
                    "description": "Bearer / API token. Sensitive — never logged.",
                    "sensitive": True,
                },
                "service": {
                    "type": ["string", "null"],
                    "description": "Service to investigate (optional).",
                },
                "time_range": {
                    "type": "string",
                    "default": "30m",
                    "description": "Lookback window like '15m', '1h'.",
                },
                "message": {
                    "type": "string",
                    "default": "",
                    "description": "Free-text question or intent.",
                },
            },
            "required": ["base_url"],
            "additionalProperties": False,
        },
    }


def _tool_config_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "tool": {"type": "string"},
            "base_url": {"type": "string"},
            "auth_token": {
                "type": ["string", "null"],
                "sensitive": True,
                "description": "Sensitive — never logged.",
            },
        },
        "required": ["tool", "base_url"],
        "additionalProperties": False,
    }


TOOL_SCHEMAS: list[dict[str, Any]] = [
    _query_tool_schema(
        "query_prometheus",
        "Run an AYOSA investigation against a Prometheus endpoint.",
    ),
    _query_tool_schema(
        "query_elasticsearch",
        "Run an AYOSA investigation against an Elasticsearch endpoint.",
    ),
    _query_tool_schema(
        "query_splunk",
        "Run an AYOSA investigation against a Splunk endpoint.",
    ),
    _query_tool_schema(
        "query_alertmanager",
        "Run an AYOSA investigation against an Alertmanager endpoint.",
    ),
    _query_tool_schema(
        "query_jaeger",
        "Run an AYOSA investigation against a Jaeger endpoint.",
    ),
    {
        "name": "ayosa_investigate",
        "description": (
            "Run the full AYOSA agent loop across one or more observability "
            "tools and return a synthesized investigation result."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "service": {"type": ["string", "null"]},
                "time_range": {"type": "string", "default": "30m"},
                "tools": {
                    "type": "array",
                    "items": _tool_config_schema(),
                    "minItems": 1,
                },
                "session_id": {"type": ["string", "null"]},
            },
            "required": ["message", "tools"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ayosa_search_workspace",
        "description": (
            "Search the AYOSA workspace index (services, dashboards, alerts, "
            "metrics, log indexes, traces, tools, owners)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 10,
                          "minimum": 1, "maximum": 100},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ayosa_get_run",
        "description": "Fetch one persisted AYOSA run by its run_id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "minLength": 1},
            },
            "required": ["run_id"],
            "additionalProperties": False,
        },
    },
]


TOOL_NAMES: tuple[str, ...] = tuple(s["name"] for s in TOOL_SCHEMAS)


# ──────────────────────────────────────────────────────────────────────── #
# Redaction helper — single source of truth for log scrubbing
# ──────────────────────────────────────────────────────────────────────── #
def redact_for_log(payload: Any) -> Any:
    """Return a deep copy of `payload` with sensitive values replaced.

    Recurses through dicts and lists. Any key whose lowercased name is
    in `SENSITIVE_INPUT_FIELDS` gets its value replaced with
    `REDACTED_PLACEHOLDER`. Non-container values pass through unchanged.
    """
    if isinstance(payload, dict):
        out: dict[str, Any] = {}
        for k, v in payload.items():
            if isinstance(k, str) and k.lower() in SENSITIVE_INPUT_FIELDS:
                out[k] = REDACTED_PLACEHOLDER if v is not None else None
            else:
                out[k] = redact_for_log(v)
        return out
    if isinstance(payload, (list, tuple)):
        cls = list if isinstance(payload, list) else tuple
        return cls(redact_for_log(item) for item in payload)
    return payload


__all__ = [
    "AyosaGetRunInput",
    "AyosaInvestigateInput",
    "AyosaSearchWorkspaceInput",
    "QueryAlertmanagerInput",
    "QueryElasticsearchInput",
    "QueryJaegerInput",
    "QueryPrometheusInput",
    "QuerySplunkInput",
    "REDACTED_PLACEHOLDER",
    "SENSITIVE_INPUT_FIELDS",
    "TOOL_NAMES",
    "TOOL_SCHEMAS",
    "redact_for_log",
]
