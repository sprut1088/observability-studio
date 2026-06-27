from __future__ import annotations

import statistics
from typing import Any

from models import SignalEvidence
from prometheus_client import PrometheusClient


SERVICE_LABELS = ["service", "service_name", "app", "job"]


def canonical_service_name(value: str) -> str:
    value = str(value or "").strip()

    # Convert opentelemetry-demo/frontend -> frontend
    if "/" in value:
        value = value.split("/")[-1]

    # Normalize common demo namespace prefixes
    for prefix in ["opentelemetry-demo-", "otel-demo-"]:
        if value.startswith(prefix):
            value = value[len(prefix):]

    return value.strip()


def is_application_service(value: str) -> bool:
    lowered = canonical_service_name(value).lower()

    deny_exact = {
        "prometheus",
        "grafana",
        "alertmanager",
        "blackbox-exporter",
        "blackbox-frontend",
        "otel-collector",
        "opentelemetry-collector",
        "otelcol-contrib",
        "jaeger",
        "kafka",
        "zookeeper",
        "opensearch",
        "elasticsearch",
        "node-exporter",
        "cadvisor",
        "kubernetes",
        "kube-state-metrics",
        "pushgateway",
        "docker",
        "container",
        "promtail",
        "loki",
    }

    deny_contains = [
        "prometheus",
        "grafana",
        "alertmanager",
        "exporter",
        "collector",
        "jaeger",
        "kafka",
        "opensearch",
        "elasticsearch",
        "cadvisor",
        "kube",
        "node",
        "docker",
        "container",
        "otelcol",
    ]

    if not lowered or lowered in deny_exact:
        return False

    if any(token in lowered for token in deny_contains):
        return False

    if lowered.startswith(("prometheus-", "grafana-", "kube-", "otel-", "node-")):
        return False

    return True

def _find_metric_series(
    prom: PrometheusClient,
    metric_names: list[str],
    service: str,
    lookback_days: int,
) -> tuple[str | None, str | None, str | None, dict | None]:
    start, end = prom.range_window(lookback_days)

    canonical = canonical_service_name(service)

    service_label_candidates = ["service", "service_name", "job", "app"]

    for metric in metric_names:
        try:
            data = prom.series([f"{metric}"], start, end)
        except Exception:
            continue

        for series in data.get("data", []) or []:
            if not isinstance(series, dict):
                continue

            for label in service_label_candidates:
                raw_value = series.get(label)
                if not raw_value:
                    continue

                if canonical_service_name(raw_value) == canonical:
                    status_label = None
                    for candidate in [
                        "http_response_status_code",
                        "status",
                        "status_code",
                        "code",
                        "grpc_status_code",
                    ]:
                        if candidate in series:
                            status_label = candidate
                            break

                    return metric, label, status_label, series

    return None, None, None, None


def _status_error_matcher(status_label: str) -> str:
    if status_label in {"http_response_status_code", "status_code", "code"}:
        return f'{status_label}=~"5..|500|501|502|503|504|error|failed"'
    if status_label == "grpc_status_code":
        return f'{status_label}!~"0|OK|ok"'
    return f'{status_label}=~"5..|error|failed"'


def _series_values(result: dict[str, Any]) -> list[float]:
    values: list[float] = []
    for item in result.get("data", {}).get("result", []) or []:
        for pair in item.get("values", []) or []:
            try:
                values.append(float(pair[1]))
            except Exception:
                continue
    return values


def _instant_value(result: dict[str, Any]) -> float | None:
    items = result.get("data", {}).get("result", []) or []
    if not items:
        return None
    try:
        return float(items[0].get("value", [None, None])[1])
    except Exception:
        return None


def _stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"avg": None, "min": None, "max": None, "p95": None}
    sorted_values = sorted(values)
    p95_index = max(0, int(len(sorted_values) * 0.95) - 1)
    return {
        "avg": statistics.mean(values),
        "min": min(values),
        "max": max(values),
        "p95": sorted_values[p95_index],
    }


def discover_services(prom: PrometheusClient) -> list[str]:
    services: set[str] = set()

    for label in SERVICE_LABELS:
        try:
            for value in prom.label_values(label):
                if not value:
                    continue

                normalized = canonical_service_name(value)

                if is_application_service(normalized):
                    services.add(normalized)
        except Exception:
            continue

    return sorted(services)


def service_selector(service: str) -> str:
    selectors = [f'service="{service}"', f'service_name="{service}"', f'app="{service}"', f'job="{service}"']
    return "|".join(selectors)


def analyze_service_trends(
    prom: PrometheusClient,
    service: str,
    lookback_days: int,
) -> list[SignalEvidence]:
    evidence: list[SignalEvidence] = []
    start, end = prom.range_window(lookback_days)

    canonical = canonical_service_name(service)

    count_metric_candidates = [
        "http_server_duration_milliseconds_count",
        "http_server_request_duration_seconds_count",
        "http_request_duration_seconds_count",
        "http_requests_total",
        "rpc_server_duration_milliseconds_count",
    ]

    bucket_metric_candidates = [
        "http_server_duration_milliseconds_bucket",
        "http_request_duration_seconds_bucket",
        "http_server_request_duration_seconds_bucket",
        "rpc_server_duration_milliseconds_bucket",
    ]

    count_metric, service_label, status_label, sample_series = _find_metric_series(
        prom,
        count_metric_candidates,
        canonical,
        lookback_days,
    )

    if count_metric and service_label:
        selector = f'{service_label}=~".*{canonical}.*"'
        total_query = f"sum(rate({count_metric}{{{selector}}}[5m]))"

        try:
            total = prom.query_range(total_query, start, end, "5m")
            total_values = _series_values(total)
        except Exception:
            total_values = []

        if total_values:
            stats = _stats(total_values)
            evidence.append(
                SignalEvidence(
                    source="prometheus",
                    signal_type="traffic_trend",
                    service=canonical,
                    title="Request traffic observed",
                    value=f"avg={stats['avg']:.4f} rps, peak={stats['max']:.4f} rps",
                    query=total_query,
                    interpretation="The service has measurable request volume, so request-based SLOs are eligible.",
                    confidence=0.85,
                    raw={
                        "metric": count_metric,
                        "service_label": service_label,
                        "sample_series": sample_series or {},
                    },
                )
            )

        if status_label:
            error_matcher = _status_error_matcher(status_label)
            error_query = (
                f"sum(rate({count_metric}{{{selector},{error_matcher}}}[5m])) "
                f"/ clamp_min(sum(rate({count_metric}{{{selector}}}[5m])), 0.000001)"
            )

            try:
                err = prom.query_range(error_query, start, end, "5m")
                err_values = _series_values(err)
            except Exception:
                err_values = []

            if err_values:
                err_stats = _stats(err_values)
                avg_error_ratio = float(err_stats["avg"] or 0.0)
                max_error_ratio = float(err_stats["max"] or 0.0)
                availability = max(0.0, min(100.0, 100.0 * (1.0 - avg_error_ratio)))

                evidence.append(
                    SignalEvidence(
                        source="prometheus",
                        signal_type="availability_trend",
                        service=canonical,
                        title="Historical availability estimated",
                        value=(
                            f"availability={availability:.3f}%, "
                            f"avg_error_ratio={avg_error_ratio:.6f}, "
                            f"peak_error_ratio={max_error_ratio:.6f}"
                        ),
                        query=error_query,
                        interpretation="Historical error ratio was measured and used to calibrate the SLO objective.",
                        confidence=0.9,
                        raw={
                            "estimated_availability": availability,
                            "avg_error_ratio": avg_error_ratio,
                            "max_error_ratio": max_error_ratio,
                            "metric": count_metric,
                            "service_label": service_label,
                            "status_label": status_label,
                        },
                    )
                )

    bucket_metric, bucket_service_label, _, _ = _find_metric_series(
        prom,
        bucket_metric_candidates,
        canonical,
        lookback_days,
    )

    if bucket_metric and bucket_service_label:
        selector = f'{bucket_service_label}=~".*{canonical}.*"'
        p95_query = f"histogram_quantile(0.95, sum(rate({bucket_metric}{{{selector}}}[5m])) by (le))"

        try:
            latency = prom.query_range(p95_query, start, end, "5m")
            latency_values = _series_values(latency)
        except Exception:
            latency_values = []

        if latency_values:
            stats = _stats(latency_values)
            evidence.append(
                SignalEvidence(
                    source="prometheus",
                    signal_type="latency_trend",
                    service=canonical,
                    title="Historical p95 latency observed",
                    value=f"avg_p95={float(stats['avg'] or 0):.4f}, peak_p95={float(stats['max'] or 0):.4f}",
                    query=p95_query,
                    interpretation="Latency histogram data is available and can support a latency SLO.",
                    confidence=0.85,
                    raw={
                        "metric": bucket_metric,
                        "service_label": bucket_service_label,
                    },
                )
            )

    return evidence