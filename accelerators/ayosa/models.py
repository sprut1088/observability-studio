from typing import Any
from pydantic import BaseModel


class AyosaToolConfig(BaseModel):
    tool: str
    base_url: str
    auth_token: str | None = None


class AyosaChatRequest(BaseModel):
    message: str
    service: str | None = None
    time_range: str = "30m"
    tools: list[AyosaToolConfig]


class EvidenceItem(BaseModel):
    source: str
    signal: str
    finding: str
    query: str | None = None
    status: str
    raw: Any = None


class AyosaTimelineItem(BaseModel):
    timestamp: str | None = None
    source: str
    event: str
    severity: str | None = None


class AyosaChatResponse(BaseModel):
    answer: str
    service: str | None
    time_range: str
    confidence: float

    probable_root_cause: str
    impact: str
    detected_patterns: list[str]
    timeline: list[AyosaTimelineItem]
    related_artifacts: list[dict[str, Any]]

    evidence: list[EvidenceItem]
    suggested_actions: list[str]

    signal_coverage: dict[str, list[str]]
    missing_signals: list[str]