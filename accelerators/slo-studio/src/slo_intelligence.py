from __future__ import annotations

import re

from models import ServiceProfile, SignalEvidence, SLICandidate, SLOFinding, SLORecommendation


def _evidence_value(evidence: list[SignalEvidence], signal_type: str) -> SignalEvidence | None:
    for item in evidence:
        if item.signal_type == signal_type:
            return item
    return None


def _availability_from_evidence(ev: SignalEvidence | None) -> float | None:
    if not ev:
        return None
    raw = ev.raw or {}
    value = raw.get("estimated_availability")
    try:
        return float(value)
    except Exception:
        pass

    match = re.search(r"availability=([0-9.]+)%", ev.value)
    if match:
        return float(match.group(1))
    return None


def choose_objective(observed_availability: float | None, style: str, criticality: str) -> float:
    style = (style or "balanced").lower()
    criticality = (criticality or "balanced").lower()

    if observed_availability is None:
        if criticality in {"critical", "high"}:
            return 99.9
        return 99.5

    if style == "aggressive":
        candidate = min(99.95, observed_availability + 0.05)
    elif style == "conservative":
        candidate = max(95.0, observed_availability - 0.2)
    else:
        candidate = max(95.0, observed_availability - 0.05)

    if criticality in {"critical", "high"}:
        candidate = max(candidate, 99.0)

    if candidate >= 99.95:
        return 99.95
    if candidate >= 99.9:
        return 99.9
    if candidate >= 99.5:
        return 99.5
    if candidate >= 99.0:
        return 99.0
    return round(candidate, 2)


def generate_sli_candidates(profile: ServiceProfile) -> list[SLICandidate]:
    candidates: list[SLICandidate] = []

    traffic = _evidence_value(profile.evidence, "traffic_trend")
    availability = _evidence_value(profile.evidence, "availability_trend")
    latency = _evidence_value(profile.evidence, "latency_trend")

    if traffic or availability:
        candidates.append(
            SLICandidate(
                service=profile.name,
                name=f"{profile.name}-availability",
                sli_type="availability",
                description=f"{profile.name} successful requests divided by total requests.",
                good_query=f'sum(rate(http_requests_total{{service="{profile.name}",status!~"5.."}}[5m]))',
                total_query=f'sum(rate(http_requests_total{{service="{profile.name}"}}[5m]))',
                evidence=[ev for ev in [traffic, availability] if ev],
            )
        )

    if latency:
        candidates.append(
            SLICandidate(
                service=profile.name,
                name=f"{profile.name}-latency",
                sli_type="latency",
                description=f"{profile.name} requests served within the agreed latency threshold.",
                good_query=f'sum(rate(http_request_duration_seconds_bucket{{service="{profile.name}",le="1"}}[5m]))',
                total_query=f'sum(rate(http_request_duration_seconds_count{{service="{profile.name}"}}[5m]))',
                threshold="1s",
                evidence=[latency],
            )
        )

    for journey in profile.user_journeys:
        safe_journey = journey.lower().replace(" ", "-").replace("_", "-")
        candidates.append(
            SLICandidate(
                service=profile.name,
                name=f"{profile.name}-{safe_journey}-journey-availability",
                sli_type="journey_availability",
                description=f"Availability of the {journey} user journey involving {profile.name}.",
                good_query=f'sum(rate(http_requests_total{{service="{profile.name}",route=~".*{journey}.*",status!~"5.."}}[5m]))',
                total_query=f'sum(rate(http_requests_total{{service="{profile.name}",route=~".*{journey}.*"}}[5m]))',
                evidence=profile.evidence[:5],
            )
        )

    return candidates


def recommend_slos(
    profiles: list[ServiceProfile],
    existing_slos: list[dict],
    objective_style: str,
    window_days: int,
) -> tuple[list[SLORecommendation], list[SLOFinding]]:
    existing_services = {item.get("service") for item in existing_slos if item.get("service")}
    recommendations: list[SLORecommendation] = []
    findings: list[SLOFinding] = []

    for profile in profiles:
        candidates = generate_sli_candidates(profile)

        if profile.name not in existing_services:
            findings.append(
                SLOFinding(
                    service=profile.name,
                    severity="high" if profile.criticality in {"critical", "high"} else "medium",
                    title="Missing service-level SLO",
                    description=f"No existing SLO was detected for service '{profile.name}'.",
                    recommendation="Define at least one availability SLO and one latency or journey SLO if telemetry supports it.",
                    evidence=profile.evidence[:3],
                )
            )

        for candidate in candidates:
            availability_ev = _evidence_value(candidate.evidence, "availability_trend")
            observed_availability = _availability_from_evidence(availability_ev)
            objective = choose_objective(observed_availability, objective_style, profile.criticality)

                        score = 0.25
            assumptions: list[str] = []

            traffic_ev = _evidence_value(profile.evidence, "traffic_trend")
            latency_ev = _evidence_value(profile.evidence, "latency_trend")
            trace_ev = _evidence_value(profile.evidence, "trace_operations")
            repo_ev = _evidence_value(profile.evidence, "service_profile")

            if traffic_ev:
                score += 0.20
            else:
                assumptions.append("Request traffic metric was not found; generated query may require metric-name adjustment.")

            if availability_ev:
                score += 0.30
            else:
                assumptions.append("Historical availability could not be measured from Prometheus.")

            if latency_ev:
                score += 0.15

            if trace_ev or profile.operations:
                score += 0.10

            if repo_ev or profile.entrypoints:
                score += 0.05

            if profile.criticality in {"critical", "high"}:
                score += 0.05

            # Journey SLOs must have stronger evidence than generic service SLOs.
            if candidate.sli_type == "journey_availability" and not (traffic_ev and availability_ev):
                score -= 0.10
                assumptions.append("Journey-specific SLO needs route/span-level validation before production rollout.")

            confidence = max(0.25, min(0.95, round(score, 2)))

            if candidate.sli_type == "latency":
                objective = 95.0

            rationale_parts = [
                f"Service '{profile.name}' was discovered from observability data.",
            ]
            if observed_availability is not None:
                rationale_parts.append(f"Observed historical availability is approximately {observed_availability:.3f}%.")
            if profile.operations:
                rationale_parts.append(f"Trace operations were found: {', '.join(profile.operations[:5])}.")
            if profile.entrypoints:
                rationale_parts.append(f"Repository or configuration entrypoints were found: {', '.join(profile.entrypoints[:5])}.")
            if candidate.evidence:
                rationale_parts.append("Recommendation is backed by collected signal evidence.")

            recommendations.append(
                SLORecommendation(
                    service=profile.name,
                    name=candidate.name,
                    sli_type=candidate.sli_type,
                    objective=objective,
                    window=f"{window_days}d",
                    description=candidate.description,
                    good_query=candidate.good_query,
                    total_query=candidate.total_query,
                    rationale=" ".join(rationale_parts),
                    confidence=confidence,
                    page_alert=profile.criticality in {"critical", "high"} or candidate.sli_type in {"availability", "journey_availability"},
                    evidence=candidate.evidence,
                    assumptions=assumptions,
                )
            )

    if not existing_slos:
        findings.append(
            SLOFinding(
                service="all",
                severity="critical",
                title="No existing SLO coverage detected",
                description="No Sloth-style or Prometheus SLO rules were detected.",
                recommendation="Start by defining SLOs for critical user journeys and high-traffic services.",
            )
        )

    return recommendations, findings
