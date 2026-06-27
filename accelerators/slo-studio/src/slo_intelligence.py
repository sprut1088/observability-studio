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

    match = re.search(r"availability=([0-9.]+)%", ev.value or "")
    if match:
        return float(match.group(1))

    return None


def choose_objective(observed_availability: float | None, style: str, criticality: str) -> float:
    style = (style or "balanced").lower()
    criticality = (criticality or "balanced").lower()

    # Fallback only when availability could not be measured.
    if observed_availability is None:
        if criticality in {"critical", "high"}:
            return 99.5
        if criticality == "low":
            return 95.0
        return 99.0

    # Behavior-based objective selection.
    # The recommendation should be slightly below observed reliability,
    # so it is achievable but still meaningful.
    if style == "conservative":
        margin = 0.30
    elif style == "aggressive":
        margin = 0.02
    else:
        margin = 0.10

    candidate = observed_availability - margin

    # Critical services should not get weak SLOs unless observed behavior is weak.
    if criticality in {"critical", "high"}:
        if observed_availability >= 99.95:
            candidate = max(candidate, 99.9)
        elif observed_availability >= 99.8:
            candidate = max(candidate, 99.5)
        elif observed_availability >= 99.0:
            candidate = max(candidate, 99.0)

    # Snap to standard SRE-friendly targets.
    standard_targets = [99.99, 99.95, 99.9, 99.8, 99.5, 99.0, 98.0, 95.0]
    for target in standard_targets:
        if candidate >= target:
            return target

    return 95.0

def choose_latency_threshold_ms(latency_ev: SignalEvidence | None, criticality: str) -> int:
    if not latency_ev:
        if criticality in {"critical", "high"}:
            return 1000
        return 2000

    raw_value = latency_ev.value or ""

    # Expected format:
    # avg_p95=0.1234, peak_p95=0.5678
    match = re.search(r"avg_p95=([0-9.]+)", raw_value)
    if not match:
        return 1000 if criticality in {"critical", "high"} else 2000

    avg_p95 = float(match.group(1))

    # Convert seconds to milliseconds if value looks like seconds.
    if avg_p95 < 100:
        avg_p95_ms = avg_p95 * 1000
    else:
        avg_p95_ms = avg_p95

    # Recommend a threshold slightly above observed p95.
    threshold = avg_p95_ms * 1.25

    standard_thresholds = [100, 200, 300, 500, 750, 1000, 1500, 2000, 3000, 5000, 8000]

    for item in standard_thresholds:
        if threshold <= item:
            return item

    return 10000


def _metric_label_from_evidence(evidence: list[SignalEvidence]) -> tuple[str, str]:
    traffic_ev = _evidence_value(evidence, "traffic_trend")
    if traffic_ev and traffic_ev.raw:
        metric = traffic_ev.raw.get("metric")
        service_label = traffic_ev.raw.get("service_label")
        if metric and service_label:
            return str(metric), str(service_label)

    return "http_requests_total", "service"


def _status_good_selector(evidence: list[SignalEvidence]) -> str:
    availability_ev = _evidence_value(evidence, "availability_trend")
    traffic_ev = _evidence_value(evidence, "traffic_trend")

    raw = {}
    if availability_ev and availability_ev.raw:
        raw = availability_ev.raw
    elif traffic_ev and traffic_ev.raw:
        raw = traffic_ev.raw

    status_label = raw.get("status_label") or "status"

    if status_label in {"http_response_status_code", "status_code", "code"}:
        return f'{status_label}!~"5.."'

    if status_label == "grpc_status_code":
        return f'{status_label}=~"0|OK|ok"'

    return f'{status_label}!~"5..|error|failed"'


def generate_sli_candidates(profile: ServiceProfile) -> list[SLICandidate]:
    candidates: list[SLICandidate] = []

    traffic = _evidence_value(profile.evidence, "traffic_trend")
    availability = _evidence_value(profile.evidence, "availability_trend")
    latency = _evidence_value(profile.evidence, "latency_trend")

    metric, service_label = _metric_label_from_evidence(profile.evidence)
    selector = f'{service_label}="{profile.name}"'

    good_status_selector = _status_good_selector(profile.evidence)

    if traffic or availability:
        candidates.append(
            SLICandidate(
                service=profile.name,
                name=f"{profile.name}-availability",
                sli_type="availability",
                description=f"{profile.name} availability based on successful requests divided by total valid requests.",
                good_query=f'sum(rate({metric}{{{selector},{good_status_selector}}}[5m]))',
                total_query=f'sum(rate({metric}{{{selector}}}[5m]))',
                evidence=[ev for ev in [traffic, availability] if ev],
            )
        )

        candidates.append(
            SLICandidate(
                service=profile.name,
                name=f"{profile.name}-error-rate",
                sli_type="error_rate",
                description=f"{profile.name} error-rate SLO based on non-5xx responses divided by total valid requests.",
                good_query=f'sum(rate({metric}{{{selector},{good_status_selector}}}[5m]))',
                total_query=f'sum(rate({metric}{{{selector}}}[5m]))',
                evidence=[ev for ev in [traffic, availability] if ev],
            )
        )

    if latency:
        threshold_ms = choose_latency_threshold_ms(latency, profile.criticality)
        candidates.append(
            SLICandidate(
                service=profile.name,
                name=f"{profile.name}-latency-p95-under-{threshold_ms}ms",
                sli_type="latency",
                description=f"{profile.name} p95 latency should stay under {threshold_ms}ms for valid requests.",
                good_query=f'sum(rate(http_request_duration_seconds_bucket{{service="{profile.name}",le="{threshold_ms / 1000}"}}[5m]))',
                total_query=f'sum(rate(http_request_duration_seconds_count{{service="{profile.name}"}}[5m]))',
                threshold=f"{threshold_ms}ms",
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
                description=f"Availability of the {journey} journey involving {profile.name}.",
                good_query=f'sum(rate({metric}{{{selector},{good_status_selector}}}[5m]))',
                total_query=f'sum(rate({metric}{{{selector}}}[5m]))',
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
    existing_services = {
        item.get("service")
        for item in existing_slos
        if item.get("service")
    }

    recommendations: list[SLORecommendation] = []
    findings: list[SLOFinding] = []

    for profile in profiles:
        candidates = generate_sli_candidates(profile)

        if profile.name not in existing_services:
            if profile.evidence:
                findings.append(
                    SLOFinding(
                        service=profile.name,
                        severity="high" if profile.criticality in {"critical", "high"} else "medium",
                        title="Missing service-level SLO",
                        description=f"No existing SLO was detected for service '{profile.name}'.",
                        recommendation="Define an availability or latency SLO after validating metric labels and ownership.",
                        evidence=profile.evidence[:3],
                    )
                )

        for candidate in candidates:
            availability_ev = _evidence_value(candidate.evidence, "availability_trend")
            observed_availability = _availability_from_evidence(availability_ev)
            objective = choose_objective(observed_availability, objective_style, profile.criticality)

            score = 0.20
            assumptions: list[str] = []

            traffic_ev = _evidence_value(profile.evidence, "traffic_trend")
            availability_ev = _evidence_value(profile.evidence, "availability_trend")
            latency_ev = _evidence_value(profile.evidence, "latency_trend")
            trace_ev = _evidence_value(profile.evidence, "trace_operations")
            repo_ev = _evidence_value(profile.evidence, "service_profile")

            if traffic_ev:
                score += 0.20
            else:
                assumptions.append("No request-volume evidence was found.")

            if candidate.sli_type in {"availability", "error_rate", "journey_availability"}:
                if availability_ev:
                    score += 0.35
                else:
                    assumptions.append("Historical availability/error ratio was not measured.")

            if candidate.sli_type == "latency":
                if latency_ev:
                    score += 0.35
                else:
                    assumptions.append("Historical latency percentile was not measured.")

            if trace_ev or profile.operations:
                score += 0.10

            if repo_ev or profile.entrypoints:
                score += 0.05

            if profile.criticality in {"critical", "high"}:
                score += 0.05

            if candidate.sli_type == "journey_availability" and not (availability_ev and trace_ev):
                score -= 0.15
                assumptions.append("Journey SLO needs trace or route-level validation.")

            confidence = max(0.25, min(0.95, round(score, 2)))

            if candidate.sli_type == "latency":
                if profile.criticality in {"critical", "high"}:
                    objective = 95.0
                elif profile.criticality == "low":
                    objective = 90.0
                else:
                    objective = 95.0

            rationale_parts = [
                f"Service '{profile.name}' was discovered from observability data."
            ]

            if observed_availability is not None:
                rationale_parts.append(
                    f"Observed availability over the selected lookback window was {observed_availability:.3f}%, so the recommended objective is calibrated below recent behavior."
                )

            if traffic_ev:
                rationale_parts.append("Request traffic was observed over the selected lookback window.")

            if latency_ev:
                rationale_parts.append("Latency telemetry is available for SLO validation.")

            if profile.operations:
                rationale_parts.append(
                    f"Trace operations were found: {', '.join(profile.operations[:5])}."
                )

            if candidate.evidence:
                rationale_parts.append("Recommendation is backed by collected telemetry evidence.")

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
                    page_alert=profile.criticality in {"critical", "high"} and confidence >= 0.70,
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
