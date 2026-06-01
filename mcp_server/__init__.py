"""AYOSA MCP server package (experimental).

This package is import-safe without the optional `mcp` dependency.
Only `mcp_server.server.main()` requires the `mcp` package at runtime.
"""

from __future__ import annotations

from mcp_server.schemas import TOOL_NAMES, TOOL_SCHEMAS
from mcp_server.tools import TOOL_HANDLERS

__all__ = ["TOOL_HANDLERS", "TOOL_NAMES", "TOOL_SCHEMAS"]
