from typing import Any, Optional
from pydantic import BaseModel


class AyosaToolConfig(BaseModel):
    tool: str
    base_url: str
    auth_token: Optional[str] = None


class AyosaChatRequest(BaseModel):
    message: str
    service: Optional[str] = None
    time_range: str = "30m"
    tools: list[AyosaToolConfig]


class EvidenceItem(BaseModel):
    source: str
    signal: str
    finding: str
    query: Optional[str] = None
    status: str
    raw: Any = None


class AyosaChatResponse(BaseModel):
    answer: str
    service: Optional[str]
    time_range: str
    confidence: float
    evidence: list[EvidenceItem]
    suggested_actions: list[str]