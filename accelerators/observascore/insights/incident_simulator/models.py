from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


IncidentType = Literal[
    "high_latency",
    "error_spike",
    "traffic_drop",
    "traffic_surge",
    "service_down",
    "dependency_failure",
]

CheckCategory = Literal["detection", "visibility", "diagnosis", "response"]
CheckStatus = Literal["pass", "warn", "fail"]
ReadinessStatus = Literal["Ready", "Mostly Ready", "At Risk", "High MTTR Risk", "Not Ready"]


@dataclass
class IncidentRequestContext:
    application_name: str
    environment: str
    service_name: str
    incident_type: IncidentType
    description: str = ""
    canonical_services: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IncidentEvidence:
    source_tool: str
    object_type: str
    object_name: str
    query_snippet: str = ""
    matched_keywords: list[str] = field(default_factory=list)
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IncidentCheck:
    category: CheckCategory
    name: str
    status: CheckStatus
    score: int
    explanation: str
    evidence: list[IncidentEvidence] = field(default_factory=list)
    recommendation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "name": self.name,
            "status": self.status,
            "score": self.score,
            "explanation": self.explanation,
            "evidence": [ev.to_dict() for ev in self.evidence],
            "recommendation": self.recommendation,
        }


@dataclass
class IncidentSimulationResult:
    application_name: str
    environment: str
    service_name: str
    incident_type: IncidentType
    overall_readiness_score: float
    readiness_status: ReadinessStatus
    detection_score: float
    visibility_score: float
    diagnosis_score: float
    response_score: float
    checks: list[IncidentCheck] = field(default_factory=list)
    evidence: dict[str, list[IncidentEvidence]] = field(default_factory=dict)
    gaps: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    ai_summary: str = ""
    ai_recommendations: list[str] = field(default_factory=list)
    extraction_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "application_name": self.application_name,
            "environment": self.environment,
            "service_name": self.service_name,
            "incident_type": self.incident_type,
            "overall_readiness_score": self.overall_readiness_score,
            "readiness_status": self.readiness_status,
            "detection_score": self.detection_score,
            "visibility_score": self.visibility_score,
            "diagnosis_score": self.diagnosis_score,
            "response_score": self.response_score,
            "checks": [check.to_dict() for check in self.checks],
            "evidence": {k: [ev.to_dict() for ev in v] for k, v in self.evidence.items()},
            "gaps": self.gaps,
            "recommendations": self.recommendations,
            "ai_summary": self.ai_summary,
            "ai_recommendations": self.ai_recommendations,
            "extraction_errors": self.extraction_errors,
        }
