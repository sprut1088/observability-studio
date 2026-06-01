"""Experimental MCP stdio server for AYOSA tools.

Run with:
    python -m mcp_server.server

The `mcp` Python package is **optional**. If it is not installed,
running this module prints a clear install hint and exits with code 2
without affecting any other AYOSA functionality.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

from mcp_server.schemas import TOOL_SCHEMAS, redact_for_log
from mcp_server.tools import TOOL_HANDLERS

logger = logging.getLogger("mcp_server")


_MISSING_MCP_MSG = (
    "The optional 'mcp' Python package is not installed.\n"
    "Install it with:\n"
    "    pip install mcp\n"
    "Then re-run:\n"
    "    python -m mcp_server.server\n"
)


def _import_mcp() -> tuple[Any, Any, Any] | None:
    """Try to import the MCP server SDK.

    Returns `(Server, stdio_server, types_module)` on success, or
    None when `mcp` is not installed. Any other ImportError is also
    treated as "MCP unavailable" so we never crash the host process.
    """
    try:
        from mcp.server import Server  # type: ignore[import-not-found]
        from mcp.server.stdio import stdio_server  # type: ignore[import-not-found]
        from mcp import types as mcp_types  # type: ignore[import-not-found]
    except ImportError:
        return None
    return Server, stdio_server, mcp_types


# ──────────────────────────────────────────────────────────────────────── #
# Tool dispatch — used by both the MCP server and any local test harness
# ──────────────────────────────────────────────────────────────────────── #
def dispatch_tool(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Invoke one tool by name. Never raises — returns a structured error."""
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return {"ok": False, "error": f"Unknown tool: {name}"}

    args = dict(arguments or {})
    try:
        return handler(args)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "mcp.tool=%s dispatch failed: %s — input=%s",
            name, exc, redact_for_log(args),
        )
        return {"ok": False, "error": str(exc)}


# ──────────────────────────────────────────────────────────────────────── #
# MCP wiring (only invoked when the optional dependency is present)
# ──────────────────────────────────────────────────────────────────────── #
async def _serve_stdio() -> None:
    imp = _import_mcp()
    if imp is None:
        print(_MISSING_MCP_MSG, file=sys.stderr)
        raise SystemExit(2)

    Server, stdio_server, mcp_types = imp
    server: Any = Server("ayosa-mcp")

    @server.list_tools()
    async def _list_tools():  # type: ignore[no-redef]
        return [
            mcp_types.Tool(
                name=s["name"],
                description=s["description"],
                inputSchema=s["input_schema"],
            )
            for s in TOOL_SCHEMAS
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any] | None):  # type: ignore[no-redef]
        result = await asyncio.to_thread(dispatch_tool, name, arguments)
        text = json.dumps(result, default=str, ensure_ascii=False)
        return [mcp_types.TextContent(type="text", text=text)]

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


def main() -> int:
    """Entry point for `python -m mcp_server.server`."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    if _import_mcp() is None:
        print(_MISSING_MCP_MSG, file=sys.stderr)
        return 2
    try:
        asyncio.run(_serve_stdio())
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover - manual entry point
    raise SystemExit(main())
