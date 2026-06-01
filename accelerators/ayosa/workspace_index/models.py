"""Workspace-index data models.

Stable, additive Pydantic schemas. Adding a new entity kind only
requires extending `ENTITY_KINDS` and the relevant `_extract_*` helper
in `indexer.py`.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


EntityKind = Literal[
    "service",
    "dashboard",
    "alert",
    "metric",
    "log_index",
    "trace",
    "tool",
    "owner",
]

ENTITY_KINDS: tuple[str, ...] = (
    "service",
    "dashboard",
    "alert",
    "metric",
    "log_index",
    "trace",
    "tool",
    "owner",
)


class WorkspaceEntity(BaseModel):
    """One discovered artifact entry."""
    kind: str            # one of ENTITY_KINDS
    name: str            # canonical display name
    service: Optional[str] = None     # associated service if known
    source: Optional[str] = None      # producing tool e.g. "grafana"
    run_id: Optional[str] = None      # ObsCrawl run that produced it
    attributes: dict[str, Any] = Field(default_factory=dict)
    tokens: list[str] = Field(default_factory=list)  # lowercased, for search


class WorkspaceIndex(BaseModel):
    """One indexed snapshot. Persisted as JSON on disk."""
    run_id: Optional[str] = None
    artifact_path: Optional[str] = None
    created_at: Optional[str] = None
    entities: list[WorkspaceEntity] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.entities

    def by_kind(self, kind: str) -> list[WorkspaceEntity]:
        return [e for e in self.entities if e.kind == kind]


class ServiceContext(BaseModel):
    """Per-service view used by the agent's pre-plan retrieval."""
    service: str
    dashboards: list[str] = Field(default_factory=list)
    alerts: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    log_indexes: list[str] = Field(default_factory=list)
    traces: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    owners: list[str] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not any(
            (self.dashboards, self.alerts, self.metrics, self.log_indexes,
             self.traces, self.tools, self.owners)
        )


__all__ = [
    "ENTITY_KINDS",
    "EntityKind",
    "ServiceContext",
    "WorkspaceEntity",
    "WorkspaceIndex",
]
