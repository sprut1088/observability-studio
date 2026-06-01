from typing import Any, Optional
from pydantic import BaseModel


class ChartDataPoint(BaseModel):
    timestamp: str
    value: float


class ChartData(BaseModel):
    title: str
    type: str = "line"
    signal: str
    source: str
    query: Optional[str] = None
    data: list[ChartDataPoint] = []


class LLMAnalysis(BaseModel):
    executive_summary: str = ""
    reasoning: str = ""
    missing_information: list[str] = []
    recommended_next_steps: list[str] = []
    provider: Optional[str] = None
    model: Optional[str] = None
    error: Optional[str] = None


class IncidentSnapshot(BaseModel):
    root_cause: str = ""
    impact: str = ""
    confidence: float = 0.0
    coverage: dict[str, list[str]] = {}
    top_findings: list[str] = []
    recommended_actions: list[str] = []
    timeline_summary: list[dict[str, Any]] = []


class AyosaToolConfig(BaseModel):
    tool: str
    base_url: str
    auth_token: str | None = None


class AyosaAIConfig(BaseModel):
    enabled: bool = False
    provider: Optional[str] = None          # "anthropic" | "azure" | "openrouter"
    api_key: Optional[str] = None
    azure_endpoint: Optional[str] = None
    azure_deployment: Optional[str] = None
    openrouter_model: Optional[str] = None
    model: Optional[str] = None


class AyosaChatRequest(BaseModel):
    message: str
    service: str | None = None
    time_range: str = "30m"
    tools: list[AyosaToolConfig]
    ai: Optional[AyosaAIConfig] = None
    agent_mode: bool = False
    session_id: Optional[str] = None
    reset_session: bool = False


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

    ai_analysis: Optional[dict[str, Any]] = None
    charts: list[ChartData] = []
    llm_analysis: Optional[LLMAnalysis] = None
    incident_snapshot: Optional[IncidentSnapshot] = None
    intent: Optional[str] = None
    plan: Optional[dict[str, Any]] = None

    # ── Agent-mode additions (always present so the UI can introspect) ──
    mode: str = "deterministic"  # "deterministic" | "agent"
    tool_steps: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    session_id: Optional[str] = None
    workspace_context: Optional[dict[str, Any]] = None