"""Scoring engine.

Converts findings into per-dimension scores and an overall maturity level.
Logic: every dimension starts at 100. Each finding subtracts
(weight * severity_multiplier). Dimension score maps to maturity level via bands.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from observascore.rules import Finding


SEVERITY_MULTIPLIER = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
    "info": 0,
}

DIMENSIONS = [
    "signal_coverage",
    "golden_signals",
    "slo_maturity",
    "alert_quality",
    "incident_response",
    "automation",
    "governance",
]

DIMENSION_LABELS = {
    "signal_coverage": "Signal Coverage",
    "golden_signals": "Golden Signals",
    "slo_maturity": "SLO Maturity",
    "alert_quality": "Alert Quality & On-Call",
    "incident_response": "Incident Response",
    "automation": "Automation",
    "governance": "Governance & Resilience",
}


def score_to_level(score: float) -> int:
    """Map a 0-100 score to a 1-5 maturity level."""
    if score >= 90:
        return 5
    if score >= 75:
        return 4
    if score >= 55:
        return 3
    if score >= 30:
        return 2
    return 1


LEVEL_NAMES = {
    1: "Reactive",
    2: "Instrumented",
    3: "Standardized",
    4: "Proactive",
    5: "Optimized",
}


@dataclass
class DimensionScore:
    dimension: str
    label: str
    score: float
    level: int
    level_name: str
    findings_count: int
    penalty: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "label": self.label,
            "score": round(self.score, 1),
            "level": self.level,
            "level_name": self.level_name,
            "findings_count": self.findings_count,
            "penalty": round(self.penalty, 1),
        }


@dataclass
class MaturityResult:
    overall_score: float
    overall_level: int
    overall_level_name: str
    dimension_scores: list[DimensionScore] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_score": round(self.overall_score, 1),
            "overall_level": self.overall_level,
            "overall_level_name": self.overall_level_name,
            "dimension_scores": [d.to_dict() for d in self.dimension_scores],
            "findings": [f.to_dict() for f in self.findings],
        }


class ScoringEngine:
    """Computes dimension and overall scores from findings."""

    def score(self, findings: list[Finding]) -> MaturityResult:
        # Group findings by dimension
        by_dim: dict[str, list[Finding]] = {d: [] for d in DIMENSIONS}
        for f in findings:
            by_dim.setdefault(f.dimension, []).append(f)

        dim_scores: list[DimensionScore] = []
        for dim in DIMENSIONS:
            dim_findings = by_dim.get(dim, [])
            penalty = sum(
                f.weight * SEVERITY_MULTIPLIER.get(f.severity, 1)
                for f in dim_findings
            )
            # Cap penalty so a dimension can't go below 0
            score = max(0.0, 100.0 - penalty)
            level = score_to_level(score)
            dim_scores.append(
                DimensionScore(
                    dimension=dim,
                    label=DIMENSION_LABELS.get(dim, dim),
                    score=score,
                    level=level,
                    level_name=LEVEL_NAMES[level],
                    findings_count=len(dim_findings),
                    penalty=penalty,
                )
            )

        overall_score = sum(d.score for d in dim_scores) / len(dim_scores)
        overall_level = score_to_level(overall_score)

        return MaturityResult(
            overall_score=overall_score,
            overall_level=overall_level,
            overall_level_name=LEVEL_NAMES[overall_level],
            dimension_scores=dim_scores,
            findings=findings,
        )
