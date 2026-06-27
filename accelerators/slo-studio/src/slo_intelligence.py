from __future__ import annotations

import re

from models import ServiceProfile, SignalEvidence, SLICandidate, SLOFinding, SLORecommendation


CORE_SLI_TYPES = {"availability", "latency", "error_rate"}


def _evidence_value(evidence: list[SignalEvidence], signal_type: str) -> SignalEvidence | None:
    for item in evidence:
        if item.signal_type == signal_type:
            return item
    return None


def _all_evidence(evidence: list[SignalEvidence], signal_types: set[str]) -> list[SignalEvidence]:
    return [item for item in evidence if item.signal_type in signal_types]


def _availability_from_evidence(ev: SignalEvidence | None) -> float | None:
    if not ev:
        return None

    raw = ev.raw or {}
    for key in ["observed_availability", "estimated_availability", "availability"]:
        value = raw.get(key)
        if value is not None:
            try:
                return float(value)
            except Exception:
                pass

    for pattern in [r"observed_availability=([0-9.]+)%", r"availability=([0-9.]+)%"]:
        match = re.search(pattern, ev.value or "")
        if match:
            return float(match.group(1))

    return None


def _error_rate_from_evidence(ev: SignalEvidence | None) -> float | None:
    if not ev:
        return None

    raw = ev.raw or {}
    for key in ["avg_error_ratio", "error_ratio"]:
        value = raw.get(key)
        if value is not None:
            try:
                return float(value)
            except Exception:
                pass

    match = re.search(r"avg_error_rate=([0-9.]+)%", ev.value or "")
    if match:
        return float(match.group(1)) / 100.0

    return None


def _latency_p95_ms_from_evidence(ev: SignalEvidence | None) -> float | None:
    if not ev:
        return None

    raw = ev.raw or {}
    value = raw.get("avg_p95_ms")
    if value is not None:
        try:
            return float(value)
        except Exception:
            pass

    match = re.search(r"avg_p95_ms=([0-9.]+)", ev.value or "")
    if match:
        return float(match.group(1))

    # Backward compatibility for older evidence values.
    match = re.search(r"avg_p95=([0-9.]+)", ev.value or "")
    if match:
        raw_unit = (ev.raw or {}).get("unit", "seconds")
        value = float(match.group(1))
        return value if raw_unit == "milliseconds" else value * 1000.0

    return None


def choose_objective(observed_availability: float | None, style: str, criticality: str) -> float:
    style = (style or "balanced").lower()
    criticality = (criticality or "medium").lower()

    if observed_availability is None:
        if criticality in {"critical", "high"}:
            return 99.5
        if criticality == "low":
            return 95.0
        return 99.0

    if style == "conservative":
        margin = 0.30
    elif style == "aggressive":
        margin = 0.02
    else:
        margin = 0.10

    candidate = observed_availability - margin

    if criticality in {"critical", "high"}:
        if observed_availability >= 99.95:
            candidate = max(candidate, 99.9)
        elif observed_availability >= 99.80:
            candidate = max(candidate, 99.8)
        elif observed_availability >= 99.50:
            candidate = max(candidate, 99.5)
        elif observed_availability >= 99.00:
            candidate = max(candidate, 99.0)

    standard_targets = [99.99, 99.95, 99.9, 99.8, 99.5, 99.0, 98.0, 95.0]
    for target in standard_targets:
        if candidate >= target:
            return target

    return 95.0


def choose_error_rate_objective(avg_error_ratio: float | None, style: str, criticality: str) -> float:
    if avg_error_ratio is None:
        return choose_objective(None, style, criticality)

    observed_success = 100.0 * (1.0 - avg_error_ratio)
    return choose_objective(observed_success, style, criticality)


def choose_latency_threshold_ms(avg_p95_ms: float | None, criticality: str) -> int:
    if avg_p95_ms is None:
        return 1000 if criticality in {"critical", "high"} else 2000

    # Use a threshold slightly above observed p95. This keeps the SLO achievable while still useful.
    if criticality in {"critical", "high"}:
        threshold = avg_p95_ms * 1.20
    elif criticality == "low":
        threshold = avg_p95_ms * 1.50
    else:
        threshold = avg_p95_ms * 1.25

    standard_thresholds = [50, 100, 200, 300, 500, 750, 1000, 1500, 2000, 3000, 5000, 8000, 10000]
    for item in standard_thresholds:
        if threshold <= item:
            return item

    return 10000


def _metric_label_from_evidence(evidence: list[SignalEvidence]) -> tuple[str, str, str]:
    traffic_ev = _evidence_value(evidence, "traffic_trend")
    if traffic_ev and traffic_ev.raw:
        metric = traffic_ev.raw.get("metric")
        service_label = traffic_ev.raw.get("service_label")
        selector = traffic_ev.raw.get("selector")
        if metric and service_label and selector:
            return str(metric), str(service_label), str(selector)

    return "http_requests_total", "service", 'service=~".*unknown.*"'


def _status_good_selector(evidence: list[SignalEvidence]) -> str:
    availability_ev = _evidence_value(evidence, "availability_trend")
    traffic_ev = _evidence_value(evidence, "traffic_trend")

    raw = {}
    if availability_ev and availability_ev.raw:
        raw = availability_ev.raw
    elif traffic_ev and traffic_ev.raw:
        raw = traffic_ev.raw

    if raw.get("good_matcher"):
        return str(raw.get("good_matcher"))

    status_label = raw.get("status_label") or "status"

    if status_label in {"http_status_code", "http_response_status_code", "status_code", "code"}:
        return f'{status_label}!~"5.."'

    if status_label in {"grpc_status_code", "rpc_grpc_status_code"}:
        return f'{status_label}=~"0|OK|ok"'

    return f'{status_label}!~"5..|error|failed|failure"'


def _existing_sli_matches(existing_slos: list[dict], service: str, sli_type: str) -> bool:
    service_l = service.lower()

    for item in existing_slos:
        text = " ".join(
            str(item.get(key, ""))
            for key in ["service", "name", "slo", "sli_type", "rule_name", "alert_name"]
        ).lower()

        if service_l not in text:
            continue

        if sli_type == "availability" and ("availability" in text or "burn" in text or "errorbudget" in text or "error_budget" in text):
            return True
        if sli_type == "latency" and ("latency" in text or "duration" in text or "p95" in text or "p99" in text):
            return True
        if sli_type == "error_rate" and ("error_rate" in text or "error-rate" in text or "error ratio" in text):
            return True
        if sli_type in text:
            return True

    return False


def _latency_queries(profile: ServiceProfile, latency_ev: SignalEvidence, threshold_ms: int) -> tuple[str, str]:
    raw = latency_ev.raw or {}
    bucket_metric = str(raw.get("bucket_metric") or raw.get("metric") or "http_request_duration_seconds_bucket")
    count_metric = str(raw.get("count_metric") or bucket_metric.replace("_bucket", "_count"))
    selector = str(raw.get("selector") or f'service=~".*{profile.name}.*"')
    unit = str(raw.get("unit") or "seconds")

    threshold_bucket = threshold_ms if unit == "milliseconds" else threshold_ms / 1000.0
    threshold_value = str(int(threshold_bucket)) if float(threshold_bucket).is_integer() else str(threshold_bucket)

    good_query = f'sum(rate({bucket_metric}{{{selector},le="{threshold_value}"}}[5m]))'
    total_query = f'sum(rate({count_metric}{{{selector}}}[5m]))'
    return good_query, total_query


def generate_sli_candidates(profile: ServiceProfile, existing_slos: list[dict] | None = None) -> list[SLICandidate]:
    existing_slos = existing_slos or []
    candidates: list[SLICandidate] = []

    traffic = _evidence_value(profile.evidence, "traffic_trend")
    availability = _evidence_value(profile.evidence, "availability_trend")
    error_rate = _evidence_value(profile.evidence, "error_rate_trend")
    latency = _evidence_value(profile.evidence, "latency_trend")

    metric, _service_label, selector = _metric_label_from_evidence(profile.evidence)
    good_status_selector = _status_good_selector(profile.evidence)

    if traffic and availability and not _existing_sli_matches(existing_slos, profile.name, "availability"):
        candidates.append(
            SLICandidate(
                service=profile.name,
                name=f"{profile.name}-availability",
                sli_type="availability",
                description=(
                    f"Availability SLO for {profile.name}: successful requests divided by total valid requests."
                ),
                good_query=f"sum(rate({metric}{{{selector},{good_status_selector}}}[5m]))",
                total_query=f"sum(rate({metric}{{{selector}}}[5m]))",
                evidence=[traffic, availability],
            )
        )

    if traffic and error_rate and not _existing_sli_matches(existing_slos, profile.name, "error_rate"):
        candidates.append(
            SLICandidate(
                service=profile.name,
                name=f"{profile.name}-error-rate",
                sli_type="error_rate",
                description=(
                    f"Error-rate SLO for {profile.name}: non-5xx requests divided by total valid requests."
                ),
                good_query=f"sum(rate({metric}{{{selector},{good_status_selector}}}[5m]))",
                total_query=f"sum(rate({metric}{{{selector}}}[5m]))",
                evidence=[traffic, error_rate],
            )
        )

    if traffic and latency and not _existing_sli_matches(existing_slos, profile.name, "latency"):
        avg_p95_ms = _latency_p95_ms_from_evidence(latency)
        threshold_ms = choose_latency_threshold_ms(avg_p95_ms, profile.criticality)
        good_query, total_query = _latency_queries(profile, latency, threshold_ms)

        candidates.append(
            SLICandidate(
                service=profile.name,
                name=f"{profile.name}-latency-p95-under-{threshold_ms}ms",
                sli_type="latency",
                description=(
                    f"Latency SLO for {profile.name}: at least 95% of requests should complete under {threshold_ms} ms."
                ),
                good_query=good_query,
                total_query=total_query,
                threshold=f"{threshold_ms}ms",
                evidence=[traffic, latency],
            )
        )

    return candidates


def _supporting_evidence(profile: ServiceProfile, candidate: SLICandidate) -> list[SignalEvidence]:
    selected: list[SignalEvidence] = []
    seen: set[tuple[str, str, str]] = set()

    def add(ev: SignalEvidence | None) -> None:
        if not ev:
            return
        key = (ev.source, ev.signal_type, ev.title)
        if key in seen:
            return
        seen.add(key)
        selected.append(ev)

    for ev in candidate.evidence:
        add(ev)

    support_types = {
        "trace_operations",
        "service_map",
        "trace_health_context",
        "dashboard_coverage",
        "alert_context",
        "log_error_context",
        "splunk_error_context",
        "service_profile",
    }

    for ev in _all_evidence(profile.evidence, support_types):
        add(ev)

    return selected[:8]


def _confidence(profile: ServiceProfile, candidate: SLICandidate) -> tuple[float, list[str]]:
    score = 0.20
    assumptions: list[str] = []

    traffic_ev = _evidence_value(profile.evidence, "traffic_trend")
    availability_ev = _evidence_value(profile.evidence, "availability_trend")
    error_rate_ev = _evidence_value(profile.evidence, "error_rate_trend")
    latency_ev = _evidence_value(profile.evidence, "latency_trend")
    trace_ev = _evidence_value(profile.evidence, "trace_operations")
    service_map_ev = _evidence_value(profile.evidence, "service_map")
    dashboard_ev = _evidence_value(profile.evidence, "dashboard_coverage")
    alert_ev = _evidence_value(profile.evidence, "alert_context")
    log_ev = _evidence_value(profile.evidence, "log_error_context") or _evidence_value(profile.evidence, "splunk_error_context")
    repo_ev = _evidence_value(profile.evidence, "service_profile")

    if traffic_ev:
        score += 0.15
    else:
        assumptions.append("No request-volume evidence was found.")

    if candidate.sli_type == "availability":
        if availability_ev:
            score += 0.30
        else:
            assumptions.append("Historical availability was not measured.")

    if candidate.sli_type == "error_rate":
        if error_rate_ev:
            score += 0.30
        else:
            assumptions.append("Historical error-rate was not measured.")

    if candidate.sli_type == "latency":
        if latency_ev:
            score += 0.30
        else:
            assumptions.append("Historical latency percentile was not measured.")

    if trace_ev:
        score += 0.08
    else:
        assumptions.append("Trace operation evidence was not found.")

    if service_map_ev:
        score += 0.07

    if dashboard_ev:
        score += 0.05

    if alert_ev:
        score += 0.03

    if log_ev:
        score += 0.05

    if repo_ev or profile.entrypoints:
        score += 0.03

    if profile.criticality in {"critical", "high"}:
        score += 0.04

    return max(0.25, min(0.95, round(score, 2))), assumptions


def _latency_objective(profile: ServiceProfile) -> float:
    if profile.criticality in {"critical"}:
        return 99.0
    return 95.0


def _rationale(profile: ServiceProfile, candidate: SLICandidate, objective: float, window_days: int) -> str:
    parts = [f"Missing {candidate.sli_type} SLO detected for service '{profile.name}'."]

    availability_ev = _evidence_value(profile.evidence, "availability_trend")
    error_rate_ev = _evidence_value(profile.evidence, "error_rate_trend")
    latency_ev = _evidence_value(profile.evidence, "latency_trend")
    traffic_ev = _evidence_value(profile.evidence, "traffic_trend")

    observed_availability = _availability_from_evidence(availability_ev)
    avg_error_ratio = _error_rate_from_evidence(error_rate_ev)
    avg_p95_ms = _latency_p95_ms_from_evidence(latency_ev)

    if candidate.sli_type == "availability" and observed_availability is not None:
        parts.append(
            f"Observed availability was {observed_availability:.3f}%, so the recommended SLO is {objective}% over {window_days} days."
        )

    if candidate.sli_type == "error_rate" and avg_error_ratio is not None:
        parts.append(
            f"Observed average error rate was {avg_error_ratio * 100.0:.4f}%, so the recommended success objective is {objective}% over {window_days} days."
        )

    if candidate.sli_type == "latency" and avg_p95_ms is not None:
        parts.append(
            f"Observed average p95 latency was {avg_p95_ms:.2f} ms, so the threshold was calibrated from recent behavior."
        )

    if traffic_ev:
        parts.append("Prometheus request traffic supports the SLI denominator.")

    if _evidence_value(profile.evidence, "trace_operations"):
        parts.append("Jaeger trace operations confirm the service handles real request paths.")

    if _evidence_value(profile.evidence, "service_map"):
        parts.append("Jaeger service-map evidence adds dependency and blast-radius context.")

    if _evidence_value(profile.evidence, "dashboard_coverage"):
        parts.append("Grafana dashboard coverage was checked for operational readiness.")

    if _evidence_value(profile.evidence, "alert_context"):
        parts.append("Alertmanager context was included for alert and burn-rate readiness.")

    if _evidence_value(profile.evidence, "log_error_context") or _evidence_value(profile.evidence, "splunk_error_context"):
        parts.append("Log evidence was checked for error and exception context.")

    return " ".join(parts)


def recommend_slos(
    profiles: list[ServiceProfile],
    existing_slos: list[dict],
    objective_style: str,
    window_days: int,
) -> tuple[list[SLORecommendation], list[SLOFinding]]:
    recommendations: list[SLORecommendation] = []
    findings: list[SLOFinding] = []

    for profile in profiles:
        candidates = generate_sli_candidates(profile, existing_slos)
        evidence_types = {ev.signal_type for ev in profile.evidence}

        missing_core_slis = []
        for sli_type, required_ev in [
            ("availability", "availability_trend"),
            ("error_rate", "error_rate_trend"),
            ("latency", "latency_trend"),
        ]:
            if not _existing_sli_matches(existing_slos, profile.name, sli_type) and required_ev in evidence_types:
                missing_core_slis.append(sli_type)

        if missing_core_slis:
            findings.append(
                SLOFinding(
                    service=profile.name,
                    severity="high" if profile.criticality in {"critical", "high"} else "medium",
                    title="Missing core SLO coverage",
                    description=(
                        f"Service '{profile.name}' has telemetry for {', '.join(missing_core_slis)}, "
                        "but no matching existing SLO coverage was detected."
                    ),
                    recommendation="Create evidence-backed SLOs for the missing SLIs and wire burn-rate alerts to the service owner.",
                    evidence=profile.evidence[:4],
                )
            )
        elif not profile.evidence:
            findings.append(
                SLOFinding(
                    service=profile.name,
                    severity="medium",
                    title="No usable telemetry evidence collected",
                    description=f"SLO Studio could not collect actionable telemetry for service '{profile.name}'.",
                    recommendation="Verify metric labels, trace service names, and log source configuration.",
                )
            )

        for candidate in candidates:
            availability_ev = _evidence_value(profile.evidence, "availability_trend")
            error_rate_ev = _evidence_value(profile.evidence, "error_rate_trend")

            observed_availability = _availability_from_evidence(availability_ev)
            avg_error_ratio = _error_rate_from_evidence(error_rate_ev)

            if candidate.sli_type == "latency":
                objective = _latency_objective(profile)
            elif candidate.sli_type == "error_rate":
                objective = choose_error_rate_objective(avg_error_ratio, objective_style, profile.criticality)
            else:
                objective = choose_objective(observed_availability, objective_style, profile.criticality)

            confidence, assumptions = _confidence(profile, candidate)
            supporting_evidence = _supporting_evidence(profile, candidate)

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
                    rationale=_rationale(profile, candidate, objective, window_days),
                    confidence=confidence,
                    page_alert=profile.criticality in {"critical", "high"} and confidence >= 0.70,
                    evidence=supporting_evidence,
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

    recommendations = sorted(
        recommendations,
        key=lambda rec: (
            rec.confidence,
            1 if rec.sli_type == "availability" else 0,
            1 if rec.sli_type == "latency" else 0,
        ),
        reverse=True,
    )

    return recommendations, findings
