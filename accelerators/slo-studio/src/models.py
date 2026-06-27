from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SignalEvidence:
    source: str
    signal_type: str
    service: str
    title: str
    value: str
    query: str = ""
    interpretation: str = ""
    confidence: float = 0.5
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ServiceProfile:
    name: str
    criticality: str = "unknown"
    owners: list[str] = field(default_factory=list)
    entrypoints: list[str] = field(default_factory=list)
    operations: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    user_journeys: list[str] = field(default_factory=list)
    evidence: list[SignalEvidence] = field(default_factory=list)


@dataclass
class SLICandidate:
    service: str
    name: str
    sli_type: str
    description: str
    good_query: str
    total_query: str
    threshold: str = ""
    evidence: list[SignalEvidence] = field(default_factory=list)


@dataclass
class SLORecommendation:
    service: str
    name: str
    sli_type: str
    objective: float
    window: str
    description: str
    good_query: str
    total_query: str
    rationale: str
    confidence: float
    page_alert: bool = True
    evidence: list[SignalEvidence] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)


@dataclass
class SLOFinding:
    service: str
    severity: str
    title: str
    description: str
    recommendation: str
    evidence: list[SignalEvidence] = field(default_factory=list)