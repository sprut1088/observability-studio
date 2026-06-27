from __future__ import annotations

import math
import time
from typing import Any

import requests

from models import SignalEvidence


COUNT_METRICS = [
    "http_server_duration_milliseconds_count",
    "http_server_duration_seconds_count",
    "http_server_request_duration_seconds_count",
    "http_request_duration_seconds_count",
    "http_requests_total",
    "rpc_server_duration_milliseconds_count",
    "rpc_server_duration_seconds_count",
]

BUCKET_METRICS = [
    "http_server_duration_milliseconds_bucket",
    "http_server_duration_seconds_bucket",
    "http_server_request_duration_seconds_bucket",
    "http_request_duration_seconds_bucket",
    "rpc_server_duration_milliseconds_bucket",
    "rpc_server_duration_seconds_bucket",
]

SERVICE_LABELS = [
    "service_name",
    "service",
    "app",
    "application",
    "job",
]

STATUS_LABELS = [
    "http_status_code",
    "http_response_status_code",
    "status_code",
    "status",
    "code",
    "grpc_status_code",
    "rpc_grpc_status_code",
]

NOISE_SERVICES_EXACT = {
    "prometheus",
    "grafana",
    "alertmanager",
    "blackbox-exporter",
    "blackbox-frontend",
    "otel-collector",
    "opentelemetry-collector",
    "otelcol-contrib",
    "collector",
    "jaeger",
    "tempo",
    "loki",
    "promtail",
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
    "load-generator",
    "loadgenerator",
}

NOISE_SERVICE_TOKENS = [
    "prometheus",
    "grafana",
    "alertmanager",
    "exporter",
    "collector",
    "jaeger",
    "tempo",
    "loki",
    "promtail",
    "kafka",
    "opensearch",
    "elasticsearch",
    "cadvisor",
    "kube",
    "node",
    "docker",
    "container",
    "otelcol",
    "load-generator",
    "loadgenerator",
]


def canonical_service_name(value: str | None) -> str:
    value = str(value or "").strip()

    if not value:
        return ""

    if "/" in value:
        value = value.split("/")[-1]

    for prefix in [
        "opentelemetry-demo-",
        "otel-demo-",
        "astronomy-shop-",
    ]:
        if value.startswith(prefix):
            value = value[len(prefix):]

    return value.strip()


def is_application_service(value: str | None) -> bool:
    service = canonical_service_name(value).lower()

    if not service or service in NOISE_SERVICES_EXACT:
        return False

    if any(token in service for token in NOISE_SERVICE_TOKENS):
        return False

    return True


def _prom_url(prom: Any) -> str:
    return str(
        getattr(prom, "url", "")
        or getattr(prom, "base_url", "")
        or getattr(prom, "prometheus_url", "")
    ).rstrip("/")


def _range_window(prom: Any, lookback_days: int) -> tuple[int, int]:
    if hasattr(prom, "range_window"):
        try:
            start, end = prom.range_window(lookback_days)
            return int(start), int(end)
        except Exception:
            pass

    end = int(time.time())
    start = end - int(lookback_days) * 24 * 60 * 60
    return start, end


def _prom_series(prom: Any, matchers: list[str], start: int, end: int) -> dict[str, Any]:
    if hasattr(prom, "series"):
        try:
            return prom.series(matchers, start, end)
        except TypeError:
            return prom.series(matchers=matchers, start=start, end=end)

    base = _prom_url(prom)
    if not base:
        return {"status": "error", "data": []}

    params: list[tuple[str, str | int]] = [("start", start), ("end", end)]
    for matcher in matchers:
        params.append(("match[]", matcher))

    resp = requests.get(f"{base}/api/v1/series", params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _prom_query_range(prom: Any, query: str, start: int, end: int, step: str = "5m") -> dict[str, Any]:
    if hasattr(prom, "query_range"):
        try:
            return prom.query_range(query, start, end, step)
        except TypeError:
            return prom.query_range(query=query, start=start, end=end, step=step)

    base = _prom_url(prom)
    if not base:
        return {"status": "error", "data": {"result": []}}

    resp = requests.get(
        f"{base}/api/v1/query_range",
        params={"query": query, "start": start, "end": end, "step": step},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _values_from_range(result: dict[str, Any]) -> list[float]:
    values: list[float] = []

    for series in result.get("data", {}).get("result", []) or []:
        for point in series.get("values", []) or []:
            try:
                value = float(point[1])
                if math.isfinite(value):
                    values.append(value)
            except Exception:
                continue

    return values


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _max(values: list[float]) -> float | None:
    if not values:
        return None
    return max(values)


def _unit_for_metric(metric: str) -> str:
    return "milliseconds" if "milliseconds" in metric else "seconds"


def _count_metric_for_bucket(bucket_metric: str) -> str:
    if bucket_metric.endswith("_bucket"):
        return bucket_metric[:-7] + "_count"
    return bucket_metric.replace("bucket", "count")


def _selector(service_label: str, service: str) -> str:
    return f'{service_label}=~".*{service}.*"'


def _find_metric_binding(
    prom: Any,
    service: str,
    metric_names: list[str],
    lookback_days: int,
) -> dict[str, Any] | None:
    start, end = _range_window(prom, lookback_days)
    canonical = canonical_service_name(service)

    for metric in metric_names:
        try:
            series_resp = _prom_series(prom, [metric], start, end)
        except Exception:
            continue

        for series in series_resp.get("data", []) or []:
            if not isinstance(series, dict):
                continue

            for service_label in SERVICE_LABELS:
                raw_service = series.get(service_label)
                if not raw_service:
                    continue

                canonical_raw = canonical_service_name(raw_service)
                if canonical_raw != canonical:
                    continue

                status_label = None
                for candidate in STATUS_LABELS:
                    if candidate in series:
                        status_label = candidate
                        break

                return {
                    "metric": metric,
                    "service_label": service_label,
                    "status_label": status_label,
                    "unit": _unit_for_metric(metric),
                    "sample_series": series,
                    "selector": _selector(service_label, canonical),
                }

    return None


def discover_services_from_prometheus(prom: Any, lookback_days: int) -> list[str]:
    services: set[str] = set()
    start, end = _range_window(prom, lookback_days)

    for metric in COUNT_METRICS + BUCKET_METRICS:
        try:
            series_resp = _prom_series(prom, [metric], start, end)
        except Exception:
            continue

        for series in series_resp.get("data", []) or []:
            if not isinstance(series, dict):
                continue

            for label in SERVICE_LABELS:
                value = series.get(label)
                if not value:
                    continue

                canonical = canonical_service_name(value)
                if is_application_service(canonical):
                    services.add(canonical)

    return sorted(services)


def _error_matcher(status_label: str | None) -> str | None:
    if not status_label:
        return None

    if status_label in {"http_status_code", "http_response_status_code", "status_code", "code"}:
        return f'{status_label}=~"5.."'

    if status_label in {"grpc_status_code", "rpc_grpc_status_code"}:
        return f'{status_label}!~"0|OK|ok"'

    return f'{status_label}=~"5..|error|failed|failure"'


def _good_matcher(status_label: str | None) -> str | None:
    if not status_label:
        return None

    if status_label in {"http_status_code", "http_response_status_code", "status_code", "code"}:
        return f'{status_label}!~"5.."'

    if status_label in {"grpc_status_code", "rpc_grpc_status_code"}:
        return f'{status_label}=~"0|OK|ok"'

    return f'{status_label}!~"5..|error|failed|failure"'


def analyze_service_behavior(prom: Any, service: str, lookback_days: int) -> list[SignalEvidence]:
    evidence: list[SignalEvidence] = []
    start, end = _range_window(prom, lookback_days)
    service = canonical_service_name(service)

    count_binding = _find_metric_binding(prom, service, COUNT_METRICS, lookback_days)

    if count_binding:
        metric = str(count_binding["metric"])
        selector = str(count_binding["selector"])
        status_label = count_binding.get("status_label")

        total_query = f"sum(rate({metric}{{{selector}}}[5m]))"

        try:
            total_result = _prom_query_range(prom, total_query, start, end, "5m")
            total_values = _values_from_range(total_result)
        except Exception:
            total_values = []

        avg_rps = _avg(total_values)
        peak_rps = _max(total_values)

        if avg_rps is not None:
            evidence.append(
                SignalEvidence(
                    source="prometheus",
                    signal_type="traffic_trend",
                    service=service,
                    title="Request traffic observed",
                    value=f"avg_rps={avg_rps:.6f}, peak_rps={float(peak_rps or 0):.6f}",
                    query=total_query,
                    interpretation=f"Request volume was measured over the last {lookback_days} days.",
                    confidence=0.90,
                    raw={
                        **count_binding,
                        "avg_rps": avg_rps,
                        "peak_rps": peak_rps,
                    },
                )
            )

        error_matcher = _error_matcher(str(status_label) if status_label else None)
        good_matcher = _good_matcher(str(status_label) if status_label else None)

        if error_matcher and good_matcher:
            error_ratio_query = (
                f"((sum(rate({metric}{{{selector},{error_matcher}}}[5m])) or vector(0)) "
                f"/ clamp_min(sum(rate({metric}{{{selector}}}[5m])), 0.000001))"
            )

            try:
                error_result = _prom_query_range(prom, error_ratio_query, start, end, "5m")
                error_values = _values_from_range(error_result)
            except Exception:
                error_values = []

            avg_error_ratio = _avg(error_values)
            peak_error_ratio = _max(error_values)

            if avg_error_ratio is not None:
                avg_error_ratio = max(0.0, min(1.0, avg_error_ratio))
                peak_error_ratio = max(0.0, min(1.0, float(peak_error_ratio or 0)))
                observed_availability = max(0.0, min(100.0, 100.0 * (1.0 - avg_error_ratio)))

                common_raw = {
                    **count_binding,
                    "good_matcher": good_matcher,
                    "error_matcher": error_matcher,
                    "observed_availability": observed_availability,
                    "estimated_availability": observed_availability,
                    "avg_error_ratio": avg_error_ratio,
                    "peak_error_ratio": peak_error_ratio,
                    "avg_error_rate_percent": avg_error_ratio * 100.0,
                    "peak_error_rate_percent": peak_error_ratio * 100.0,
                }

                evidence.append(
                    SignalEvidence(
                        source="prometheus",
                        signal_type="availability_trend",
                        service=service,
                        title="Historical availability measured",
                        value=(
                            f"observed_availability={observed_availability:.3f}%, "
                            f"avg_error_rate={avg_error_ratio * 100.0:.4f}%, "
                            f"peak_error_rate={peak_error_ratio * 100.0:.4f}%"
                        ),
                        query=error_ratio_query,
                        interpretation=(
                            "Availability was calculated from request counters and status-code labels "
                            f"over the last {lookback_days} days."
                        ),
                        confidence=0.95,
                        raw=common_raw,
                    )
                )

                evidence.append(
                    SignalEvidence(
                        source="prometheus",
                        signal_type="error_rate_trend",
                        service=service,
                        title="Historical error-rate measured",
                        value=(
                            f"avg_error_rate={avg_error_ratio * 100.0:.4f}%, "
                            f"peak_error_rate={peak_error_ratio * 100.0:.4f}%"
                        ),
                        query=error_ratio_query,
                        interpretation=(
                            "Error-rate was calculated from failed request status codes over "
                            f"the last {lookback_days} days."
                        ),
                        confidence=0.95,
                        raw=common_raw,
                    )
                )

    bucket_binding = _find_metric_binding(prom, service, BUCKET_METRICS, lookback_days)

    if bucket_binding:
        bucket_metric = str(bucket_binding["metric"])
        selector = str(bucket_binding["selector"])
        unit = str(bucket_binding.get("unit") or _unit_for_metric(bucket_metric))
        count_metric = _count_metric_for_bucket(bucket_metric)

        p95_query = f"histogram_quantile(0.95, sum(rate({bucket_metric}{{{selector}}}[5m])) by (le))"
        p99_query = f"histogram_quantile(0.99, sum(rate({bucket_metric}{{{selector}}}[5m])) by (le))"

        try:
            p95_result = _prom_query_range(prom, p95_query, start, end, "5m")
            p95_values = _values_from_range(p95_result)
        except Exception:
            p95_values = []

        try:
            p99_result = _prom_query_range(prom, p99_query, start, end, "5m")
            p99_values = _values_from_range(p99_result)
        except Exception:
            p99_values = []

        avg_p95 = _avg(p95_values)
        peak_p95 = _max(p95_values)
        avg_p99 = _avg(p99_values)
        peak_p99 = _max(p99_values)

        if avg_p95 is not None:
            if unit == "seconds":
                avg_p95_ms = avg_p95 * 1000.0
                peak_p95_ms = float(peak_p95 or 0) * 1000.0
                avg_p99_ms = float(avg_p99 or 0) * 1000.0
                peak_p99_ms = float(peak_p99 or 0) * 1000.0
            else:
                avg_p95_ms = avg_p95
                peak_p95_ms = float(peak_p95 or 0)
                avg_p99_ms = float(avg_p99 or 0)
                peak_p99_ms = float(peak_p99 or 0)

            evidence.append(
                SignalEvidence(
                    source="prometheus",
                    signal_type="latency_trend",
                    service=service,
                    title="Historical latency measured",
                    value=(
                        f"avg_p95_ms={avg_p95_ms:.2f}, "
                        f"peak_p95_ms={peak_p95_ms:.2f}, "
                        f"avg_p99_ms={avg_p99_ms:.2f}, "
                        f"peak_p99_ms={peak_p99_ms:.2f}"
                    ),
                    query=p95_query,
                    interpretation=(
                        "Latency percentiles were calculated from histogram buckets over "
                        f"the last {lookback_days} days."
                    ),
                    confidence=0.95,
                    raw={
                        **bucket_binding,
                        "bucket_metric": bucket_metric,
                        "count_metric": count_metric,
                        "unit": unit,
                        "avg_p95_ms": avg_p95_ms,
                        "peak_p95_ms": peak_p95_ms,
                        "avg_p99_ms": avg_p99_ms,
                        "peak_p99_ms": peak_p99_ms,
                    },
                )
            )

    return evidence
