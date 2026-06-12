from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel, Field


class SLOStudioRequest(BaseModel):
    service: Optional[str] = None
    environment: Optional[str] = None
    objective: Optional[float] = 99.9
    window_days: int = 30
    include_yaml: bool = True
    include_ai: bool = False
    tools: list[dict[str, Any]] = Field(default_factory=list)


class SLOStudioResponse(BaseModel):
    success: bool
    report_url: Optional[str] = None
    json_url: Optional[str] = None
    yaml_url: Optional[str] = None
    summary: Optional[dict[str, Any]] = None
    error: Optional[str] = None