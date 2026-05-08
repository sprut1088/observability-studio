from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


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
    total_services: int
    excellent_services: int
    good_services: int
    partial_services: int
    poor_services: int
    blind_spot_services: int
    overall_coverage_score: float
    weakest_services: list[str] = field(default_factory=list)
    strongest_services: list[str] = field(default_factory=list)
    service_profiles: list[ServiceGapProfile] = field(default_factory=list)
    tool_coverage_summary: list[ToolCoverageSummary] = field(default_factory=list)
    top_recommendations: list[GapRecommendation] = field(default_factory=list)
    extraction_errors: list[str] = field(default_factory=list)
    gap_map_score: float = 0.0
    service_blind_spots: int = 0
    missing_signal_counts: dict[str, int] = field(default_factory=dict)
    no_services_inferred: bool = False
    no_dashboards_found: bool = False
    raw_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
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
            "extraction_errors": self.extraction_errors,
            "gap_map_score": self.gap_map_score,
            "service_blind_spots": self.service_blind_spots,
            "missing_signal_counts": self.missing_signal_counts,
            "no_services_inferred": self.no_services_inferred,
            "no_dashboards_found": self.no_dashboards_found,
            "raw_counts": self.raw_counts,
        }
