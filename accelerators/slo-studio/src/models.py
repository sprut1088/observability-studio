from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ServiceSLO:
    service: str
    sli_type: str
    objective: float
    window: str
    description: str
    query_good: str
    query_total: str
    alerting: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SLOFinding:
    service: str
    severity: str
    title: str
    description: str
    recommendation: str