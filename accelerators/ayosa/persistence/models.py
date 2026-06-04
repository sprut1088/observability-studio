"""Pydantic schemas for the persistence layer.

These mirror the row shapes used by the SQLite repository so the public
API surface and tests can refer to typed objects rather than raw dicts.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class RunSummary(BaseModel):
    """One row of `list_runs()` — compact, list-view friendly."""

    run_id: str
    session_id: Optional[str] = None
    message: str = ""
    intent: Optional[str] = None
    service: Optional[str] = None
    time_range: Optional[str] = None
    tools_used: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    created_at: str = ""
    iterations: int = 1


class PersistedRun(BaseModel):
    """Full run record returned by `get_run()`."""

    run_id: str
    session_id: Optional[str] = None
    message: str = ""
    intent: Optional[str] = None
    service: Optional[str] = None
    time_range: Optional[str] = None
    tools_used: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    answer: str = ""
    snapshot: Optional[dict[str, Any]] = None
    evidence_summary: Optional[dict[str, Any]] = None
    tool_steps: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str = ""
    iterations: int = 1
    replan_reason: Optional[str] = None


class RunComparison(BaseModel):
    """Output of `compare_runs(a, b)`.

    `differences` is a small, agent-friendly dict highlighting fields that
    diverged between the two runs. Missing runs are reported via
    `missing_run_ids`.
    """

    left: Optional[PersistedRun] = None
    right: Optional[PersistedRun] = None
    missing_run_ids: list[str] = Field(default_factory=list)
    differences: dict[str, Any] = Field(default_factory=dict)


__all__ = ["PersistedRun", "RunComparison", "RunSummary"]
