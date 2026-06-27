from __future__ import annotations

import statistics
from typing import Any

from models import SignalEvidence
from prometheus_client import PrometheusClient


SERVICE_LABELS = ["service", "service_name", "app", "job"]


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

    deny_exact = {
        "prometheus",
        "grafana",
        "alertmanager",
        "blackbox-exporter",
        "otel-collector",
        "opentelemetry-collector",
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
    ]

    for label in SERVICE_LABELS:
        try:
            for value in prom.label_values(label):
                if not value:
                    continue

                value = str(value).strip()
                lowered = value.lower()

                if lowered in deny_exact:
                    continue

                if any(token in lowered for token in deny_contains):
                    continue

                if lowered.startswith(("prometheus-", "grafana-", "kube-", "otel-")):
                    continue

                services.add(value)
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

    selectors = [
        f'service="{service}"',
        f'service_name="{service}"',
        f'app="{service}"',
        f'job="{service}"',
    ]

    request_metric_candidates = [
        "http_requests_total",
        "http_server_requests_seconds_count",
        "http_server_duration_milliseconds_count",
        "rpc_server_duration_milliseconds_count",
    ]

    duration_bucket_candidates = [
        "http_request_duration_seconds_bucket",
        "http_server_duration_milliseconds_bucket",
        "rpc_server_duration_milliseconds_bucket",
    ]

    duration_sum_count_candidates = [
        ("http_request_duration_seconds_sum", "http_request_duration_seconds_count"),
        ("http_server_duration_milliseconds_sum", "http_server_duration_milliseconds_count"),
        ("rpc_server_duration_milliseconds_sum", "rpc_server_duration_milliseconds_count"),
    ]

    for selector in selectors:
        for metric in request_metric_candidates:
            total_query = f'sum(rate({metric}{{{selector}}}[5m]))'
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
                        service=service,
                        title="Observed request traffic",
                        value=f"avg={stats['avg']:.4f} rps, max={stats['max']:.4f} rps",
                        query=total_query,
                        interpretation="Service has measurable request traffic and is eligible for request-based SLOs.",
                        confidence=0.85,
                    )
                )

                error_query = f'sum(rate({metric}{{{selector},status=~"5..|error|failed"}}[5m])) / clamp_min(sum(rate({metric}{{{selector}}}[5m])), 0.000001)'
                try:
                    err = prom.query_range(error_query, start, end, "5m")
                    err_values = _series_values(err)
                except Exception:
                    err_values = []

                if err_values:
                    err_stats = _stats(err_values)
                    availability = max(0.0, min(100.0, 100.0 * (1.0 - float(err_stats["avg"] or 0.0))))
                    evidence.append(
                        SignalEvidence(
                            source="prometheus",
                            signal_type="availability_trend",
                            service=service,
                            title="Observed availability trend",
                            value=f"estimated availability={availability:.3f}%, avg_error_ratio={float(err_stats['avg'] or 0):.6f}, max_error_ratio={float(err_stats['max'] or 0):.6f}",
                            query=error_query,
                            interpretation="Historical error ratio can be used to calibrate availability SLO objective.",
                            confidence=0.75,
                            raw={"estimated_availability": availability},
                        )
                    )
                return evidence

    for selector in selectors:
        for bucket_metric in duration_bucket_candidates:
            p95_query = f'histogram_quantile(0.95, sum(rate({bucket_metric}{{{selector}}}[5m])) by (le))'
            try:
                data = prom.query_range(p95_query, start, end, "5m")
                values = _series_values(data)
            except Exception:
                values = []

            if values:
                stats = _stats(values)
                evidence.append(
                    SignalEvidence(
                        source="prometheus",
                        signal_type="latency_trend",
                        service=service,
                        title="Observed p95 latency trend",
                        value=f"avg_p95={float(stats['avg'] or 0):.4f}, max_p95={float(stats['max'] or 0):.4f}",
                        query=p95_query,
                        interpretation="Latency trend can be used to recommend a latency SLO threshold.",
                        confidence=0.8,
                    )
                )
                return evidence

    for selector in selectors:
        for sum_metric, count_metric in duration_sum_count_candidates:
            avg_query = f'sum(rate({sum_metric}{{{selector}}}[5m])) / clamp_min(sum(rate({count_metric}{{{selector}}}[5m])), 0.000001)'
            try:
                data = prom.query_range(avg_query, start, end, "5m")
                values = _series_values(data)
            except Exception:
                values = []

            if values:
                stats = _stats(values)
                evidence.append(
                    SignalEvidence(
                        source="prometheus",
                        signal_type="latency_trend",
                        service=service,
                        title="Observed average latency trend",
                        value=f"avg={float(stats['avg'] or 0):.4f}, max={float(stats['max'] or 0):.4f}",
                        query=avg_query,
                        interpretation="Average latency exists, but p95/p99 histogram data would improve SLO quality.",
                        confidence=0.6,
                    )
                )
                return evidence

    return evidence