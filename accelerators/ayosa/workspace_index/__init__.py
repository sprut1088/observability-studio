"""Lightweight semantic workspace index for AYOSA.

Builds a local JSON-backed catalogue of services, dashboards, alerts,
metrics, log indexes, traces, tool names, and owners discovered in
ObsCrawl / ObservaScore artifacts under `runtime/<run_id>/`.

No external vector DB — pure-Python token scoring. The schema is stable
and additive so a future ChromaDB-backed retriever can drop in behind
the same `search_workspace` / `get_service_context` API.
"""

from accelerators.ayosa.workspace_index.indexer import (
    DEFAULT_INDEX_DIR,
    index_workspace,
    load_index,
    rebuild_aggregate,
)
from accelerators.ayosa.workspace_index.retriever import (
    EMPTY_INDEX_MESSAGE,
    get_service_context,
    is_available,
    search_workspace,
    workspace_overview,
)
from accelerators.ayosa.workspace_index.models import (
    EntityKind,
    ENTITY_KINDS,
    ServiceContext,
    WorkspaceEntity,
    WorkspaceIndex,
)

__all__ = [
    "DEFAULT_INDEX_DIR",
    "EMPTY_INDEX_MESSAGE",
    "ENTITY_KINDS",
    "EntityKind",
    "ServiceContext",
    "WorkspaceEntity",
    "WorkspaceIndex",
    "index_workspace",
    "load_index",
    "rebuild_aggregate",
    "search_workspace",
    "get_service_context",
    "is_available",
    "workspace_overview",
]
