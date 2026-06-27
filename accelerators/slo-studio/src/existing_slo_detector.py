from __future__ import annotations

from typing import Any


def detect_existing_slos(rules: list[dict[str, Any]], alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    existing: list[dict[str, Any]] = []

    for rule in rules:
        labels = rule.get("labels", {}) or {}
        name = rule.get("name") or rule.get("alert") or ""

        if "sloth_slo" in labels or "sloth_service" in labels or "ErrorBudgetBurn" in name or "SLO" in name:
            existing.append(
                {
                    "name": name,
                    "service": labels.get("sloth_service") or labels.get("service") or labels.get("app") or "",
                    "slo": labels.get("sloth_slo") or labels.get("slo") or "",
                    "severity": labels.get("severity") or "",
                    "source": "prometheus_rules",
                    "labels": labels,
                }
            )

    for alert in alerts:
        labels = alert.get("labels", {}) or {}
        name = labels.get("alertname", "")

        if "sloth_slo" in labels or "sloth_service" in labels or "ErrorBudgetBurn" in name or "SLO" in name:
            existing.append(
                {
                    "name": name,
                    "service": labels.get("sloth_service") or labels.get("service") or labels.get("app") or "",
                    "slo": labels.get("sloth_slo") or labels.get("slo") or "",
                    "severity": labels.get("severity") or "",
                    "state": alert.get("state"),
                    "source": "prometheus_alerts",
                    "labels": labels,
                }
            )

    return existing