from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ApplicationContext:
    name: str
    environment: str
    services: list[str] = field(default_factory=list)
    include_auto_discovered: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SignalCoverage:
    metrics_present: bool = False
    logs_present: bool = False
    traces_present: bool = False
    dashboards_present: bool = False
    alerts_present: bool = False
    rate_present: bool = False
    errors_present: bool = False
    duration_present: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GapRecommendation:
    service: str
    missing_signal: str
    action: str
    expected_value: str
    impact: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ServiceGapProfile:
    service: str
    coverage: SignalCoverage
    coverage_score: int
    readiness_status: str
    missing_signals: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "coverage": self.coverage.to_dict(),
            "coverage_score": self.coverage_score,
            "readiness_status": self.readiness_status,
            "missing_signals": self.missing_signals,
            "tools": self.tools,
        }


@dataclass
class ToolCoverageSummary:
    tool_name: str
    metrics_services: int = 0
    logs_services: int = 0
    traces_services: int = 0
    dashboards_services: int = 0
    alerts_services: int = 0
    red_complete_services: int = 0
    total_services: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ObservabilityGapMapResult:
    application_name: str
    environment: str
    canonical_services: list[str]
    auto_discovered_candidates: list[str]
    ignored_candidates: list[str]
    total_services: int
    overall_coverage_score: float
    service_profiles: list[ServiceGapProfile] = field(default_factory=list)
    tool_coverage_summary: list[ToolCoverageSummary] = field(default_factory=list)
    top_recommendations: list[GapRecommendation] = field(default_factory=list)
    raw_counts: dict[str, int] = field(default_factory=dict)
    extraction_errors: list[str] = field(default_factory=list)
    excellent_services: int = 0
    good_services: int = 0
    partial_services: int = 0
    poor_services: int = 0
    blind_spot_services: int = 0
    strongest_services: list[str] = field(default_factory=list)
    weakest_services: list[str] = field(default_factory=list)
    gap_map_score: float = 0.0
    service_blind_spots: int = 0
    missing_signal_counts: dict[str, int] = field(default_factory=dict)
    discovery_mode: bool = False
    no_dashboards_found: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "application_name": self.application_name,
            "environment": self.environment,
            "canonical_services": self.canonical_services,
            "auto_discovered_candidates": self.auto_discovered_candidates,
            "ignored_candidates": self.ignored_candidates,
            "overall_coverage_score": self.overall_coverage_score,
            "total_services": self.total_services,
            "excellent_services": self.excellent_services,
            "good_services": self.good_services,
            "partial_services": self.partial_services,
            "poor_services": self.poor_services,
            "blind_spot_services": self.blind_spot_services,
            "services": [profile.to_dict() for profile in self.service_profiles],
            "weakest_services": self.weakest_services,
            "strongest_services": self.strongest_services,
            "tool_coverage_summary": [item.to_dict() for item in self.tool_coverage_summary],
            "top_recommendations": [rec.to_dict() for rec in self.top_recommendations],
            "raw_counts": self.raw_counts,
            "extraction_errors": self.extraction_errors,
            "gap_map_score": self.gap_map_score,
            "service_blind_spots": self.service_blind_spots,
            "missing_signal_counts": self.missing_signal_counts,
            "discovery_mode": self.discovery_mode,
            "no_dashboards_found": self.no_dashboards_found,
        }
