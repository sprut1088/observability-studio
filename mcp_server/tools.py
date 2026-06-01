"""Tool implementations for the AYOSA MCP server.

Each `call_<tool>` is a pure, sync function that:

- Validates input via the pydantic schemas in `mcp_server.schemas`.
- Reuses existing AYOSA adapters / services / repository — never
  duplicates business logic.
- Returns a JSON-serialisable dict.
- Logs at most a redacted view of its input (no auth tokens).

This module is **MCP-package-free** so it can be imported and tested
without the optional `mcp` dependency installed.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from accelerators.ayosa.registry import ADAPTERS
from accelerators.ayosa.workspace_index import (
    is_available as workspace_index_available,
    search_workspace,
)
from accelerators.ayosa.persistence import get_default_repository
from mcp_server.schemas import (
    AyosaGetRunInput,
    AyosaInvestigateInput,
    AyosaSearchWorkspaceInput,
    QueryAlertmanagerInput,
    QueryElasticsearchInput,
    QueryJaegerInput,
    QueryPrometheusInput,
    QuerySplunkInput,
    redact_for_log,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────── #
# Per-adapter `query_<tool>` calls
# ──────────────────────────────────────────────────────────────────────── #
def _run_adapter(adapter_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Shared implementation for the five `query_<tool>` MCP entries."""
    logger.info(
        "mcp.tool=%s input=%s", f"query_{adapter_key}", redact_for_log(payload),
    )

    cls = ADAPTERS.get(adapter_key)
    if cls is None:
        return {"ok": False, "tool": adapter_key,
                "error": f"Unknown adapter: {adapter_key}"}

    try:
        adapter = cls(
            base_url=payload["base_url"],
            auth_token=payload.get("auth_token"),
        )
        observations = adapter.investigate(
            payload.get("service"),
            payload.get("time_range") or "30m",
            payload.get("message") or "",
        )
    except Exception as exc:  # noqa: BLE001 — surface as a structured error
        logger.warning("mcp.tool=%s failed: %s", adapter_key, exc)
        return {"ok": False, "tool": adapter_key, "error": str(exc)}

    return {
        "ok": True,
        "tool": adapter_key,
        "observations": list(observations or []),
    }


def call_query_prometheus(payload: dict[str, Any]) -> dict[str, Any]:
    return _run_adapter("prometheus", QueryPrometheusInput(**payload).model_dump())


def call_query_elasticsearch(payload: dict[str, Any]) -> dict[str, Any]:
    return _run_adapter(
        "elasticsearch", QueryElasticsearchInput(**payload).model_dump()
    )


def call_query_splunk(payload: dict[str, Any]) -> dict[str, Any]:
    return _run_adapter("splunk", QuerySplunkInput(**payload).model_dump())


def call_query_alertmanager(payload: dict[str, Any]) -> dict[str, Any]:
    return _run_adapter(
        "alertmanager", QueryAlertmanagerInput(**payload).model_dump()
    )


def call_query_jaeger(payload: dict[str, Any]) -> dict[str, Any]:
    return _run_adapter("jaeger", QueryJaegerInput(**payload).model_dump())


# ──────────────────────────────────────────────────────────────────────── #
# AYOSA-level tools
# ──────────────────────────────────────────────────────────────────────── #
def call_ayosa_investigate(payload: dict[str, Any]) -> dict[str, Any]:
    """Run the full AYOSA agent loop and return the chat response dict."""
    parsed = AyosaInvestigateInput(**payload)
    logger.info("mcp.tool=ayosa_investigate input=%s",
                redact_for_log(parsed.model_dump()))

    # Imported lazily so callers that only need `query_<tool>` don't pull
    # in the FastAPI/agent stack.
    from accelerators.ayosa.agent_bridge import run_agent_chat
    from accelerators.ayosa.models import (
        AyosaChatRequest, AyosaToolConfig,
    )

    try:
        request = AyosaChatRequest(
            message=parsed.message,
            service=parsed.service,
            time_range=parsed.time_range or "30m",
            tools=[
                AyosaToolConfig(
                    tool=t.tool, base_url=t.base_url, auth_token=t.auth_token,
                )
                for t in parsed.tools
            ],
            agent_mode=True,
            session_id=parsed.session_id,
        )
        response = run_agent_chat(request)
    except Exception as exc:  # noqa: BLE001
        logger.warning("mcp.tool=ayosa_investigate failed: %s", exc)
        return {"ok": False, "error": str(exc)}

    return {"ok": True, "response": response}


def call_ayosa_search_workspace(payload: dict[str, Any]) -> dict[str, Any]:
    parsed = AyosaSearchWorkspaceInput(**payload)
    logger.info("mcp.tool=ayosa_search_workspace input=%s",
                redact_for_log(parsed.model_dump()))
    if not workspace_index_available():
        return {
            "ok": True, "available": False,
            "message": "workspace index unavailable",
            "results": [],
        }
    result = search_workspace(parsed.query, limit=parsed.limit)
    return {"ok": True, **result}


def call_ayosa_get_run(payload: dict[str, Any]) -> dict[str, Any]:
    parsed = AyosaGetRunInput(**payload)
    logger.info("mcp.tool=ayosa_get_run input=%s",
                redact_for_log(parsed.model_dump()))
    repo = get_default_repository()
    if repo is None:
        return {"ok": False, "error": "Persistence unavailable"}
    run = repo.get_run(parsed.run_id)
    if run is None:
        return {"ok": False, "error": f"Run not found: {parsed.run_id}"}
    return {"ok": True, "run": run.model_dump()}


# ──────────────────────────────────────────────────────────────────────── #
# Dispatch table — single source of truth for `server.py`
# ──────────────────────────────────────────────────────────────────────── #
TOOL_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "query_prometheus":      call_query_prometheus,
    "query_elasticsearch":   call_query_elasticsearch,
    "query_splunk":          call_query_splunk,
    "query_alertmanager":    call_query_alertmanager,
    "query_jaeger":          call_query_jaeger,
    "ayosa_investigate":     call_ayosa_investigate,
    "ayosa_search_workspace": call_ayosa_search_workspace,
    "ayosa_get_run":         call_ayosa_get_run,
}


__all__ = [
    "TOOL_HANDLERS",
    "call_ayosa_get_run",
    "call_ayosa_investigate",
    "call_ayosa_search_workspace",
    "call_query_alertmanager",
    "call_query_elasticsearch",
    "call_query_jaeger",
    "call_query_prometheus",
    "call_query_splunk",
]
