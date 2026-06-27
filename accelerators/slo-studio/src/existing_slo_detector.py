from __future__ import annotations

from typing import Any, Iterable


def _canonical_service(value: str | None) -> str:
    value = str(value or "").strip()
    if "/" in value:
        value = value.split("/")[-1]
    for prefix in ["opentelemetry-demo-", "otel-demo-", "astronomy-shop-"]:
        if value.startswith(prefix):
            value = value[len(prefix):]
    return value


def _iter_rules(rules_payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(rules_payload, list):
        for item in rules_payload:
            if isinstance(item, dict) and "rules" in item:
                for rule in item.get("rules", []) or []:
                    if isinstance(rule, dict):
                        yield rule
            elif isinstance(item, dict):
                yield item
        return

    if not isinstance(rules_payload, dict):
        return

    groups = rules_payload.get("groups") or rules_payload.get("data", {}).get("groups") or []
    for group in groups:
        for rule in group.get("rules", []) or []:
            if isinstance(rule, dict):
                yield rule


def _iter_alerts(alerts_payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(alerts_payload, list):
        for item in alerts_payload:
            if isinstance(item, dict):
                yield item
        return

    if not isinstance(alerts_payload, dict):
        return

    for alert in alerts_payload.get("alerts") or alerts_payload.get("data", {}).get("alerts") or []:
        if isinstance(alert, dict):
            yield alert


def _sli_type_from_text(*parts: str) -> str:
    text = " ".join(str(part or "") for part in parts).lower()

    if "latency" in text or "duration" in text or "p95" in text or "p99" in text:
        return "latency"
    if "error-rate" in text or "error_rate" in text or "error ratio" in text:
        return "error_rate"
    if "availability" in text or "burn" in text or "errorbudget" in text or "error_budget" in text:
        return "availability"

    return "unknown"


def _service_from_labels(labels: dict[str, Any]) -> str:
    service = (
        labels.get("sloth_service")
        or labels.get("service")
        or labels.get("service_name")
        or labels.get("app")
        or labels.get("application")
        or labels.get("job")
        or ""
    )
    return _canonical_service(str(service))


def _is_slo_signal(name: str, labels: dict[str, Any], expr: str = "") -> bool:
    text = f"{name} {expr} " + " ".join(f"{k}={v}" for k, v in labels.items())
    text_l = text.lower()

    return (
        "sloth_slo" in labels
        or "sloth_service" in labels
        or "errorbudgetburn" in text_l
        or "error_budget" in text_l
        or "slo:" in text_l
        or "slo" in text_l
        or "availability" in text_l
    )


def detect_existing_slos(rules: Any, alerts: Any) -> list[dict[str, Any]]:
    coverage: dict[tuple[str, str, str, str], dict[str, Any]] = {}

    for rule in _iter_rules(rules):
        labels = rule.get("labels", {}) or {}
        name = str(rule.get("name") or rule.get("alert") or rule.get("record") or "")
        expr = str(rule.get("query") or rule.get("expr") or "")

        if not _is_slo_signal(name, labels, expr):
            continue

        service = _service_from_labels(labels)
        slo_name = str(labels.get("sloth_slo") or labels.get("slo") or labels.get("slo_name") or name or "unknown")
        sli_type = _sli_type_from_text(name, slo_name, expr)
        severity = str(labels.get("severity") or "")

        # De-duplicate recording rules from the same Sloth SLO. One SLO may generate many rules.
        key = (service, slo_name, sli_type, severity or "n/a")
        coverage[key] = {
            "name": slo_name,
            "service": service,
            "slo": slo_name,
            "sli_type": sli_type,
            "severity": severity,
            "source": "prometheus_rules",
            "labels": labels,
            "rule_name": name,
        }

    for alert in _iter_alerts(alerts):
        labels = alert.get("labels", {}) or {}
        annotations = alert.get("annotations", {}) or {}
        name = str(labels.get("alertname") or alert.get("name") or "")

        if not _is_slo_signal(name, labels, " ".join(str(v) for v in annotations.values())):
            continue

        service = _service_from_labels(labels)
        slo_name = str(labels.get("sloth_slo") or labels.get("slo") or labels.get("slo_name") or name or "unknown")
        sli_type = _sli_type_from_text(name, slo_name, " ".join(str(v) for v in annotations.values()))
        severity = str(labels.get("severity") or "")

        key = (service, slo_name, sli_type, severity or "n/a")
        coverage[key] = {
            "name": slo_name,
            "service": service,
            "slo": slo_name,
            "sli_type": sli_type,
            "severity": severity,
            "state": alert.get("state") or alert.get("status", {}).get("state"),
            "source": "prometheus_alerts",
            "labels": labels,
            "annotations": annotations,
            "alert_name": name,
        }

    return sorted(
        coverage.values(),
        key=lambda item: (str(item.get("service") or ""), str(item.get("sli_type") or ""), str(item.get("slo") or "")),
    )
