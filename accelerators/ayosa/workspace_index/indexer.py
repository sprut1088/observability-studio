"""Workspace indexer.

Reads ObsCrawl / ObservaScore JSON artifacts and produces a compact,
search-friendly catalogue persisted under
`runtime/workspace_index/{run_id}.json`. An aggregate `index.json`
points at every per-run index so retrieval can scan everything in one
shot.

Input shapes accepted (all optional, any subset works):
    {
      "services":      [{"name": "...", "owner": "...", "team": "..."}, ...] | ["..."],
      "dashboards":    [{"name": "...", "service": "...", "url": "..."}, ...],
      "alerts":        [{"name": "...", "severity": "...", "service": "..."}, ...],
      "metrics":       [{"name": "...", "type": "...", "service": "..."}, ...],
      "log_indexes":   [{"name": "...", "tool": "elasticsearch"}, ...],
      "traces":        [{"service": "...", "operation": "..."}, ...],
      "tools":         [{"name": "prometheus", "base_url": "..."}, ...] | ["..."],
      "owners":        ["team-payments", ...]
    }

The indexer is tolerant of missing/extra keys. Unknown keys are
silently ignored. Strings are also accepted in place of dicts.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from accelerators.ayosa.workspace_index.models import (
    ENTITY_KINDS,
    WorkspaceEntity,
    WorkspaceIndex,
)

logger = logging.getLogger(__name__)


DEFAULT_INDEX_DIR: Path = Path("runtime") / "workspace_index"
_AGGREGATE_FILE = "index.json"

# Map artifact keys → entity kind.
_KEY_TO_KIND: dict[str, str] = {
    "services": "service",
    "dashboards": "dashboard",
    "alerts": "alert",
    "metrics": "metric",
    "log_indexes": "log_index",
    "log_index": "log_index",
    "logs": "log_index",
    "traces": "trace",
    "tools": "tool",
    "owners": "owner",
    "teams": "owner",
}


# ──────────────────────────────────────────────────────────────────────── #
# Public API
# ──────────────────────────────────────────────────────────────────────── #
def index_workspace(
    run_id: str,
    artifact_path: str | Path,
    *,
    index_dir: str | Path | None = None,
) -> WorkspaceIndex:
    """Build (or refresh) the workspace index for one ObsCrawl run.

    `artifact_path` may be a single JSON file or a directory containing
    multiple JSON files. The resulting index is written to
    `{index_dir}/{run_id}.json` and the aggregate `index.json` is
    refreshed. Returns the in-memory `WorkspaceIndex`.
    """
    base = _resolve_index_dir(index_dir)
    base.mkdir(parents=True, exist_ok=True)

    raw_documents = list(_iter_json_documents(Path(artifact_path)))
    entities: list[WorkspaceEntity] = []
    for doc in raw_documents:
        entities.extend(_extract_entities(doc, run_id=run_id))

    # Dedupe by (kind, name, service, source) — same artifact may be
    # referenced multiple times across documents.
    deduped: dict[tuple, WorkspaceEntity] = {}
    for e in entities:
        key = (e.kind, e.name.lower(), e.service or "", e.source or "")
        deduped.setdefault(key, e)

    index = WorkspaceIndex(
        run_id=run_id,
        artifact_path=str(artifact_path),
        created_at=datetime.now(timezone.utc).isoformat(),
        entities=list(deduped.values()),
    )

    _write_index(base, run_id, index)
    rebuild_aggregate(index_dir=base)
    return index


def load_index(
    run_id: str | None = None,
    *,
    index_dir: str | Path | None = None,
) -> WorkspaceIndex:
    """Load a per-run index, or the aggregate (all runs merged) if `run_id` is None."""
    base = _resolve_index_dir(index_dir)
    if run_id:
        path = base / f"{_safe_id(run_id)}.json"
        if not path.exists():
            return WorkspaceIndex()
        return _read_index(path)

    # Aggregate: merge every per-run index in the dir.
    if not base.exists():
        return WorkspaceIndex()
    merged: list[WorkspaceEntity] = []
    for path in sorted(base.glob("*.json")):
        if path.name == _AGGREGATE_FILE:
            continue
        try:
            idx = _read_index(path)
        except Exception as exc:  # noqa: BLE001 — bad files must not crash
            logger.warning("Skipping unreadable index %s: %s", path, exc)
            continue
        merged.extend(idx.entities)
    return WorkspaceIndex(entities=merged)


def rebuild_aggregate(*, index_dir: str | Path | None = None) -> Path:
    """Rewrite `{index_dir}/index.json` from every per-run file."""
    base = _resolve_index_dir(index_dir)
    base.mkdir(parents=True, exist_ok=True)
    agg = load_index(run_id=None, index_dir=base)
    out = base / _AGGREGATE_FILE
    out.write_text(
        json.dumps(agg.model_dump(), default=str, ensure_ascii=False),
        encoding="utf-8",
    )
    return out


# ──────────────────────────────────────────────────────────────────────── #
# Extraction — pure, exported for unit testing
# ──────────────────────────────────────────────────────────────────────── #
def _extract_entities(doc: Any, *, run_id: str) -> list[WorkspaceEntity]:
    """Walk one JSON document and emit entities for every recognised key."""
    if not isinstance(doc, dict):
        return []

    out: list[WorkspaceEntity] = []
    for raw_key, value in doc.items():
        key = raw_key.lower().strip()
        kind = _KEY_TO_KIND.get(key)
        if kind is None:
            continue
        if not isinstance(value, (list, tuple)):
            continue
        for item in value:
            ent = _to_entity(item, kind=kind, run_id=run_id)
            if ent is not None:
                out.append(ent)
                # Owner harvest from service entries (services may carry
                # an "owner" / "team" attribute referencing a team name).
                if kind == "service":
                    for owner in _owners_from_service(item):
                        out.append(
                            WorkspaceEntity(
                                kind="owner",
                                name=owner,
                                service=ent.name,
                                source=ent.source,
                                run_id=run_id,
                                attributes={"derived_from": ent.name},
                                tokens=_tokenise(owner, ent.name),
                            )
                        )
    return out


def _to_entity(item: Any, *, kind: str, run_id: str) -> WorkspaceEntity | None:
    if isinstance(item, str):
        name = item.strip()
        if not name:
            return None
        return WorkspaceEntity(
            kind=kind,
            name=name,
            run_id=run_id,
            tokens=_tokenise(name),
        )
    if not isinstance(item, dict):
        return None

    name = (
        item.get("name") or item.get("title") or item.get("id")
        or item.get("query") or item.get("service")
    )
    if not isinstance(name, str) or not name.strip():
        return None

    service = item.get("service") or item.get("svc") or item.get("app")
    source = item.get("tool") or item.get("source") or item.get("provider")
    # Strip secret-shaped attributes
    safe_attrs = {
        k: v for k, v in item.items()
        if k.lower() not in {"api_key", "auth_token", "token", "password",
                             "secret", "bearer", "authorization"}
    }
    return WorkspaceEntity(
        kind=kind,
        name=name.strip(),
        service=service.strip() if isinstance(service, str) else None,
        source=source.strip() if isinstance(source, str) else None,
        run_id=run_id,
        attributes=safe_attrs,
        tokens=_tokenise(
            name, service if isinstance(service, str) else None,
            source if isinstance(source, str) else None,
            *(str(v) for v in safe_attrs.values() if isinstance(v, (str, int, float))),
        ),
    )


def _owners_from_service(item: Any) -> list[str]:
    if not isinstance(item, dict):
        return []
    out: list[str] = []
    for k in ("owner", "team", "owners", "teams"):
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
        elif isinstance(v, (list, tuple)):
            for s in v:
                if isinstance(s, str) and s.strip():
                    out.append(s.strip())
    return out


_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def _tokenise(*parts: Any) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        if p is None:
            continue
        for m in _TOKEN_RE.findall(str(p).lower()):
            if m not in seen:
                seen.add(m)
                out.append(m)
    return out


# ──────────────────────────────────────────────────────────────────────── #
# Internal IO
# ──────────────────────────────────────────────────────────────────────── #
def _iter_json_documents(path: Path) -> Iterable[Any]:
    if not path.exists():
        return []

    def _read_one(p: Path) -> Any | None:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — non-fatal
            logger.warning("Workspace indexer skipped %s: %s", p, exc)
            return None

    if path.is_file():
        doc = _read_one(path)
        return [doc] if doc is not None else []

    docs: list[Any] = []
    for p in sorted(path.rglob("*.json")):
        doc = _read_one(p)
        if doc is not None:
            docs.append(doc)
    return docs


def _resolve_index_dir(index_dir: str | Path | None) -> Path:
    return Path(index_dir) if index_dir else DEFAULT_INDEX_DIR


def _safe_id(run_id: str) -> str:
    """Refuse anything that could escape the index dir."""
    cleaned = "".join(c for c in run_id if c.isalnum() or c in "-_")
    if not cleaned or cleaned != run_id:
        raise ValueError(f"Unsafe run_id: {run_id!r}")
    return cleaned


def _write_index(base: Path, run_id: str, index: WorkspaceIndex) -> Path:
    path = base / f"{_safe_id(run_id)}.json"
    path.write_text(
        json.dumps(index.model_dump(), default=str, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _read_index(path: Path) -> WorkspaceIndex:
    data = json.loads(path.read_text(encoding="utf-8"))
    return WorkspaceIndex(**data)


__all__ = [
    "DEFAULT_INDEX_DIR",
    "ENTITY_KINDS",
    "index_workspace",
    "load_index",
    "rebuild_aggregate",
]
