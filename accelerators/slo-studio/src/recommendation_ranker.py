from __future__ import annotations

from models import SLORecommendation


CRITICAL_SERVICE_HINTS = [
    "checkout",
    "payment",
    "cart",
    "frontend",
    "frontend-proxy",
    "gateway",
    "product-catalog",
]


def score_recommendation(rec: SLORecommendation) -> float:
    score = 0.0

    score += rec.confidence * 40.0

    service_lower = rec.service.lower()
    name_lower = rec.name.lower()

    if any(token in service_lower for token in CRITICAL_SERVICE_HINTS):
        score += 20.0

    if rec.sli_type in {"journey_availability", "availability"}:
        score += 15.0

    if "checkout" in name_lower:
        score += 15.0

    if rec.evidence:
        score += min(15.0, len(rec.evidence) * 5.0)

    if rec.page_alert:
        score += 5.0

    return round(score, 2)


def split_recommendations(
    recommendations: list[SLORecommendation],
    production_confidence_threshold: float = 0.70,
) -> tuple[list[SLORecommendation], list[SLORecommendation], list[SLORecommendation]]:
    for rec in recommendations:
        setattr(rec, "priority_score", score_recommendation(rec))

    ranked = sorted(
        recommendations,
        key=lambda item: getattr(item, "priority_score", 0),
        reverse=True,
    )

    production_ready = [
        rec for rec in ranked
        if rec.confidence >= production_confidence_threshold
        and rec.evidence
        and any(ev.signal_type in {"availability_trend", "latency_trend"} for ev in rec.evidence)
    ]

    candidate_only = [
        rec for rec in ranked
        if rec not in production_ready
    ]

    top_recommendations = production_ready[:10]

    return top_recommendations, production_ready, candidate_only