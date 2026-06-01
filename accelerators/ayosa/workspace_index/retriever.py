"""Workspace retriever — pure-Python token-overlap scoring.

No vector DB. The same public API will back a ChromaDB implementation
later. Callers must treat an empty/missing index as an explicit
"workspace_index_unavailable" — the retriever never fabricates entries.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from accelerators.ayosa.workspace_index.indexer import (
    DEFAULT_INDEX_DIR,
    load_index,
)
from accelerators.ayosa.workspace_index.models import (
    ServiceContext,
    WorkspaceEntity,
    WorkspaceIndex,
)


EMPTY_INDEX_MESSAGE = "workspace index unavailable"

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def is_available(*, index_dir: str | Path | None = None) -> bool:
    """True iff the on-disk index has at least one entity across any run."""
    idx = load_index(run_id=None, index_dir=index_dir)
    return not idx.is_empty()


def search_workspace(
    query: str,
    limit: int = 10,
    *,
    index_dir: str | Path | None = None,
    index: WorkspaceIndex | None = None,
) -> dict[str, Any]:
    """Token-overlap search across every indexed run.

    Returns a dict with:
      * available: bool
      * message:   present only when available=False
      * total:     count of matches
      * results:   list of {kind, name, service, source, score, attributes}
    """
    if index is None:
        index = load_index(run_id=None, index_dir=index_dir)
    if index.is_empty():
        return {"available": False, "message": EMPTY_INDEX_MESSAGE,
                "total": 0, "results": []}

    q_tokens = _tokenise(query)
    if not q_tokens:
        # Empty query — return a deterministic preview of the index head.
        results = [_entity_to_hit(e, score=0.0) for e in index.entities[:limit]]
        return {"available": True, "total": len(results), "results": results}

    scored: list[tuple[float, WorkspaceEntity]] = []
    for e in index.entities:
        score = _score(e, q_tokens)
        if score > 0:
            scored.append((score, e))
    scored.sort(key=lambda t: t[0], reverse=True)

    top = scored[: max(0, int(limit))]
    return {
        "available": True,
        "total": len(scored),
        "results": [_entity_to_hit(e, score=s) for s, e in top],
    }


def get_service_context(
    service: str,
    *,
    index_dir: str | Path | None = None,
    index: WorkspaceIndex | None = None,
) -> dict[str, Any]:
    """Return all known artifacts for one service.

    Returns a dict with:
      * available: bool
      * service:   echoed back
      * context:   ServiceContext.model_dump() if available
      * message:   present only when available=False
    """
    if index is None:
        index = load_index(run_id=None, index_dir=index_dir)
    if index.is_empty():
        return {
            "available": False,
            "service": service,
            "message": EMPTY_INDEX_MESSAGE,
        }

    service_l = (service or "").strip().lower()
    if not service_l:
        return {
            "available": True,
            "service": service,
            "context": ServiceContext(service=service or "").model_dump(),
        }

    ctx = ServiceContext(service=service)
    for e in index.entities:
        if not _matches_service(e, service_l):
            continue
        bucket = _bucket_for_kind(e.kind, ctx)
        if bucket is not None and e.name not in bucket:
            bucket.append(e.name)
    return {"available": True, "service": service, "context": ctx.model_dump()}


def workspace_overview(
    *,
    index_dir: str | Path | None = None,
    index: WorkspaceIndex | None = None,
) -> dict[str, Any]:
    """Return a compact, agent-friendly summary of the entire index."""
    if index is None:
        index = load_index(run_id=None, index_dir=index_dir)
    if index.is_empty():
        return {"available": False, "message": EMPTY_INDEX_MESSAGE}

    counts: dict[str, int] = {}
    services: list[str] = []
    for e in index.entities:
        counts[e.kind] = counts.get(e.kind, 0) + 1
        if e.kind == "service" and e.name not in services:
            services.append(e.name)

    return {
        "available": True,
        "counts": counts,
        "services": services[:50],
        "total": len(index.entities),
    }


# ──────────────────────────────────────────────────────────────────────── #
# Internals
# ──────────────────────────────────────────────────────────────────────── #
def _tokenise(text: str) -> list[str]:
    return [m.lower() for m in _TOKEN_RE.findall(text or "")]


def _score(entity: WorkspaceEntity, q_tokens: list[str]) -> float:
    """Simple token-overlap score with a small kind-name boost."""
    if not q_tokens:
        return 0.0
    tokens = set(entity.tokens)
    hits = sum(1 for t in q_tokens if t in tokens)
    if hits == 0:
        return 0.0
    # Reward exact name match
    name_l = entity.name.lower()
    name_bonus = 1.0 if any(t == name_l for t in q_tokens) else 0.0
    return hits / max(len(q_tokens), 1) + name_bonus


def _matches_service(entity: WorkspaceEntity, service_l: str) -> bool:
    if entity.service and entity.service.lower() == service_l:
        return True
    if entity.kind == "service" and entity.name.lower() == service_l:
        return True
    # Heuristic: name contains "<service>." or "<service>_" prefix
    name_l = entity.name.lower()
    return name_l.startswith(f"{service_l}.") or name_l.startswith(f"{service_l}_")


def _bucket_for_kind(kind: str, ctx: ServiceContext) -> list[str] | None:
    return {
        "dashboard": ctx.dashboards,
        "alert": ctx.alerts,
        "metric": ctx.metrics,
        "log_index": ctx.log_indexes,
        "trace": ctx.traces,
        "tool": ctx.tools,
        "owner": ctx.owners,
    }.get(kind)


def _entity_to_hit(entity: WorkspaceEntity, *, score: float) -> dict[str, Any]:
    return {
        "kind": entity.kind,
        "name": entity.name,
        "service": entity.service,
        "source": entity.source,
        "score": round(float(score), 4),
        "attributes": entity.attributes,
    }


__all__ = [
    "EMPTY_INDEX_MESSAGE",
    "get_service_context",
    "is_available",
    "search_workspace",
    "workspace_overview",
]
