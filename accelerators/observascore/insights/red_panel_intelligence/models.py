from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class RedEvidence:
    category: str
    source_tool: str
    service: str
    dashboard_uid: str
    dashboard_title: str
    panel_title: str
    query: str
    source: str
    matched_keyword: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RedSignalCoverage:
    found: bool
    evidence: list[RedEvidence] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "found": self.found,
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass
class RedServiceCoverage:
    service: str
    normalized_service: str
    auto_discovered: bool
    rate: RedSignalCoverage
    errors: RedSignalCoverage
    duration: RedSignalCoverage
    red_score: int
    status: str
    tools: list[str] = field(default_factory=list)
    dashboards: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "normalized_service": self.normalized_service,
            "auto_discovered": self.auto_discovered,
            "rate": self.rate.to_dict(),
            "errors": self.errors.to_dict(),
            "duration": self.duration.to_dict(),
            "red_score": self.red_score,
            "status": self.status,
            "tools": self.tools,
            "dashboards": self.dashboards,
            "recommendations": self.recommendations,
        }


@dataclass
class RedDashboardAppendix:
    source_tool: str
    dashboard_uid: str
    dashboard_title: str
    panel_count: int
    rate_present: bool
    errors_present: bool
    duration_present: bool
    red_score: int
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RedPanelIntelligenceResult:
    application_name: str
    environment: str
    canonical_services: list[str]
    auto_discovered_services: list[str]
    auto_discover_services_enabled: bool
    fallback_to_auto_discovery: bool
    services_assessed: int
    fully_covered_services: int
    partial_services: int
    blind_services: int
    overall_red_coverage_score: float
    service_coverage: list[RedServiceCoverage] = field(default_factory=list)
    critical_blind_spots: list[str] = field(default_factory=list)
    dashboard_appendix: list[RedDashboardAppendix] = field(default_factory=list)
    dashboard_coverage_by_tool: dict[str, dict[str, float | int]] = field(default_factory=dict)
    top_recommendations: list[str] = field(default_factory=list)
    extraction_errors: list[str] = field(default_factory=list)
    no_dashboards_found: bool = False
    no_panels_found: bool = False
    guidance: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "application_name": self.application_name,
            "environment": self.environment,
            "canonical_services": self.canonical_services,
            "auto_discovered_services": self.auto_discovered_services,
            "auto_discover_services_enabled": self.auto_discover_services_enabled,
            "fallback_to_auto_discovery": self.fallback_to_auto_discovery,
            "services_assessed": self.services_assessed,
            "fully_covered_services": self.fully_covered_services,
            "partial_services": self.partial_services,
            "blind_services": self.blind_services,
            "overall_red_coverage_score": self.overall_red_coverage_score,
            "service_coverage": [item.to_dict() for item in self.service_coverage],
            "critical_blind_spots": self.critical_blind_spots,
            "dashboard_appendix": [item.to_dict() for item in self.dashboard_appendix],
            "dashboard_coverage_by_tool": self.dashboard_coverage_by_tool,
            "top_recommendations": self.top_recommendations,
            "extraction_errors": self.extraction_errors,
            "no_dashboards_found": self.no_dashboards_found,
            "no_panels_found": self.no_panels_found,
            "guidance": self.guidance,
        }
        payload["red_intelligence"] = {
            "overall_red_coverage_score": self.overall_red_coverage_score,
            "services_assessed": self.services_assessed,
            "fully_covered_services": self.fully_covered_services,
            "partial_services": self.partial_services,
            "blind_services": self.blind_services,
            "critical_blind_spots": self.critical_blind_spots,
            "service_coverage": [item.to_dict() for item in self.service_coverage],
        }
        return payload
