from __future__ import annotations

from typing import Any

from models import ServiceSLO, SLOFinding


def detect_existing_slos(rules: list[dict[str, Any]], alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    existing = []

    for rule in rules:
        labels = rule.get("labels", {}) or {}
        name = rule.get("name") or rule.get("alert") or ""

        if "sloth_slo" in labels or "sloth_service" in labels or "ErrorBudgetBurn" in name:
            existing.append(
                {
                    "name": name,
                    "service": labels.get("sloth_service") or labels.get("service") or "",
                    "slo": labels.get("sloth_slo") or "",
                    "severity": labels.get("severity") or "",
                    "source": "prometheus_rules",
                }
            )

    for alert in alerts:
        labels = alert.get("labels", {}) or {}
        name = labels.get("alertname", "")

        if "sloth_slo" in labels or "sloth_service" in labels or "ErrorBudgetBurn" in name:
            existing.append(
                {
                    "name": name,
                    "service": labels.get("sloth_service") or labels.get("service") or "",
                    "slo": labels.get("sloth_slo") or "",
                    "severity": labels.get("severity") or "",
                    "state": alert.get("state"),
                    "source": "prometheus_alerts",
                }
            )

    return existing


def recommend_slos(services: list[str], existing_slos: list[dict[str, Any]], default_objective: float = 99.9) -> list[ServiceSLO]:
    existing_services = {
        item.get("service")
        for item in existing_slos
        if item.get("service")
    }

    recommendations: list[ServiceSLO] = []

    for service in services:
        if not service or service in existing_services:
            continue

        recommendations.append(
            ServiceSLO(
                service=service,
                sli_type="availability",
                objective=default_objective,
                window="30d",
                description=f"{service} request success ratio over 30 days",
                query_good=f'sum(rate(http_requests_total{{service="{service}",status!~"5.."}}[5m]))',
                query_total=f'sum(rate(http_requests_total{{service="{service}"}}[5m]))',
                alerting=[
                    {"name": "fast_burn", "short_window": "5m", "long_window": "1h", "burn_rate": 14.4},
                    {"name": "slow_burn", "short_window": "30m", "long_window": "6h", "burn_rate": 3.0},
                ],
            )
        )

        recommendations.append(
            ServiceSLO(
                service=service,
                sli_type="latency",
                objective=95.0,
                window="30d",
                description=f"{service} p95 latency under 1 second",
                query_good=f'sum(rate(http_request_duration_seconds_bucket{{service="{service}",le="1"}}[5m]))',
                query_total=f'sum(rate(http_request_duration_seconds_count{{service="{service}"}}[5m]))',
                alerting=[
                    {"name": "latency_burn", "short_window": "5m", "long_window": "1h", "burn_rate": 14.4},
                ],
            )
        )

    return recommendations


def build_findings(services: list[str], existing_slos: list[dict[str, Any]], recommendations: list[ServiceSLO]) -> list[SLOFinding]:
    existing_services = {
        item.get("service")
        for item in existing_slos
        if item.get("service")
    }

    findings: list[SLOFinding] = []

    for service in services:
        if service not in existing_services:
            findings.append(
                SLOFinding(
                    service=service,
                    severity="high",
                    title="Missing service-level SLO",
                    description=f"No Prometheus/Sloth SLO was detected for service '{service}'.",
                    recommendation="Create at least one availability SLO and one latency SLO for this service.",
                )
            )

    if not existing_slos:
        findings.append(
            SLOFinding(
                service="all",
                severity="critical",
                title="No existing SLOs detected",
                description="No Sloth-style or Prometheus SLO rules were detected.",
                recommendation="Start with critical user journeys such as checkout, login, payment, and search.",
            )
        )

    return findings