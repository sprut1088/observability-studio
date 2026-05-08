from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class RedPanelEvidence:
    category: str
    source_tool: str
    dashboard_uid: str
    dashboard_title: str
    panel_title: str | None
    query: str | None
    source: str
    matched_keyword: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RedDashboardAnalysis:
    source_tool: str
    dashboard_uid: str
    dashboard_title: str
    panel_count: int
    rate_present: bool
    errors_present: bool
    duration_present: bool
    red_score: int
    status: str
    recommendations: list[str] = field(default_factory=list)
    evidence: list[RedPanelEvidence] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence"] = [ev.to_dict() for ev in self.evidence]
        return data


@dataclass
class RedPanelIntelligenceResult:
    total_dashboards: int
    complete_dashboards: int
    partial_dashboards: int
    weak_dashboards: int
    non_operational_dashboards: int
    overall_red_score: float
    dashboard_analyses: list[RedDashboardAnalysis] = field(default_factory=list)
    dashboard_coverage_by_tool: dict[str, dict[str, float | int]] = field(default_factory=dict)
    top_recommendations: list[str] = field(default_factory=list)
    extraction_errors: list[str] = field(default_factory=list)
    no_dashboards_found: bool = False
    no_panels_found: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_dashboards": self.total_dashboards,
            "complete_dashboards": self.complete_dashboards,
            "partial_dashboards": self.partial_dashboards,
            "weak_dashboards": self.weak_dashboards,
            "non_operational_dashboards": self.non_operational_dashboards,
            "overall_red_score": self.overall_red_score,
            "dashboard_coverage_by_tool": self.dashboard_coverage_by_tool,
            "top_recommendations": self.top_recommendations,
            "dashboard_analyses": [analysis.to_dict() for analysis in self.dashboard_analyses],
            "extraction_errors": self.extraction_errors,
            "no_dashboards_found": self.no_dashboards_found,
            "no_panels_found": self.no_panels_found,
        }
