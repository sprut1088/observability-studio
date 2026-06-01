"""Agent backbone schemas.

Pure dataclasses / Pydantic models — no logic. Decoupled from the existing
`accelerators.ayosa.models` (chat-API contract) so the agent layer can evolve
its internal representation without touching the public chat response shape.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ──────────────────────────────────────────────────────────────────────── #
# Input contracts
# ──────────────────────────────────────────────────────────────────────── #
class AgentToolConfig(BaseModel):
    """Connection details for one observability tool."""
    tool: str
    base_url: str
    auth_token: Optional[str] = None


class AgentAIConfig(BaseModel):
    """LLM configuration. Used only by the synthesizer."""
    enabled: bool = False
    provider: Optional[str] = None  # "anthropic" | "azure" | "openrouter"
    api_key: Optional[str] = None
    model: Optional[str] = None
    azure_endpoint: Optional[str] = None
    azure_deployment: Optional[str] = None
    openrouter_model: Optional[str] = None


class AgentInput(BaseModel):
    """Single agent invocation."""
    message: str
    service: Optional[str] = None
    time_range: str = "30m"
    tools: list[AgentToolConfig] = []
    llm: Optional[AgentAIConfig] = None
    session_id: str = "default"


# ──────────────────────────────────────────────────────────────────────── #
# Plan
# ──────────────────────────────────────────────────────────────────────── #
class Plan(BaseModel):
    """Result of the Plan phase."""
    intent: str
    service: Optional[str]
    time_range: str
    required_signals: list[str] = []
    selected_tools: list[str] = []
    query_focus: str = ""
    should_query_metrics: bool = False
    should_query_logs: bool = False
    should_query_alerts: bool = False
    should_query_traces: bool = False
    should_query_dashboards: bool = False
    covered_signals: list[str] = []
    missing_signals: list[str] = []
    skipped_tools: list[str] = []
    explanation: str = ""


# ──────────────────────────────────────────────────────────────────────── #
# Act / Observe contracts
# ──────────────────────────────────────────────────────────────────────── #
ToolStepStatus = Literal["pending", "running", "done", "skipped", "error"]


class ToolStep(BaseModel):
    """One dispatched tool call."""
    index: int
    tool: str
    label: str
    status: ToolStepStatus = "pending"
    error: Optional[str] = None


class Observation(BaseModel):
    """One finding produced by a tool adapter."""
    source: str
    signal: str
    finding: str
    query: Optional[str] = None
    status: str = "ok"
    raw: Any = None


# ──────────────────────────────────────────────────────────────────────── #
# Reflect contract
# ──────────────────────────────────────────────────────────────────────── #
ReflectionStatus = Literal["missing", "empty", "partial", "sufficient"]


class ReflectionNote(BaseModel):
    """Per-signal sufficiency note produced after observation."""
    signal: str
    status: ReflectionStatus
    note: str


# ──────────────────────────────────────────────────────────────────────── #
# Final response
# ──────────────────────────────────────────────────────────────────────── #
class ChartSeries(BaseModel):
    title: str
    type: str = "line"
    signal: str
    source: str
    query: Optional[str] = None
    data: list[dict[str, Any]] = []


class TimelineEntry(BaseModel):
    timestamp: Optional[str] = None
    source: str
    event: str
    severity: Optional[str] = None


class IncidentSnapshot(BaseModel):
    root_cause: str = ""
    impact: str = ""
    confidence: float = 0.0
    coverage: dict[str, list[str]] = {}
    top_findings: list[str] = []
    recommended_actions: list[str] = []


class AgentResult(BaseModel):
    """Output of one agent invocation."""
    intent: str
    plan: Plan
    tool_steps: list[ToolStep] = []
    observations: list[Observation] = []
    reflections: list[ReflectionNote] = []

    final_response: str = ""
    confidence: float = 0.0

    charts: list[ChartSeries] = []
    timeline: list[TimelineEntry] = []
    evidence: list[Observation] = []  # alias of observations preserved for UI parity
    snapshot: Optional[IncidentSnapshot] = None

    llm_used: bool = False
    llm_analysis: Optional[dict[str, Any]] = None


# ──────────────────────────────────────────────────────────────────────── #
# Conversation memory
# ──────────────────────────────────────────────────────────────────────── #
class ConversationTurn(BaseModel):
    timestamp: str
    user_message: str
    intent: str
    plan_summary: str = ""
    answer: str = ""


class ConversationState(BaseModel):
    session_id: str
    turns: list[ConversationTurn] = Field(default_factory=list)
