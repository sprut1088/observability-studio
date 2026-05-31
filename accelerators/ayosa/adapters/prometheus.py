import datetime
import time as _time_module

import requests


class PrometheusAdapter:
    signal = "metrics"

    def __init__(self, base_url: str, auth_token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token

    def _headers(self):
        if self.auth_token:
            return {"Authorization": f"Bearer {self.auth_token}"}
        return {}

    def _parse_time_range_seconds(self, time_range: str) -> int:
        tr = time_range.strip().lower()
        units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        for suffix, mult in units.items():
            if tr.endswith(suffix):
                try:
                    return int(tr[:-1]) * mult
                except ValueError:
                    break
        return 1800

    def get_charts(self, service: str | None, time_range: str) -> list[dict]:
        now = int(_time_module.time())
        total_secs = self._parse_time_range_seconds(time_range)
        start = now - total_secs
        step = max(60, total_secs // 30)

        selector = f'service_name="{service}"' if service else ""

        if service == "checkout":
            chart_queries = [
                {
                    "title": "Latency Trend (p95)",
                    "query": "histogram_quantile(0.95, sum(rate(app_cart_add_item_latency_seconds_bucket[5m])) by (le))",
                },
                {
                    "title": "Request Rate Trend",
                    "query": "sum(rate(app_cart_add_item_latency_seconds_count[5m]))",
                },
                {
                    "title": "Error Rate Trend",
                    "query": 'slo:sli_error:ratio_rate2h{service="checkout"}',
                },
            ]
        else:
            error_selector = f'{selector}, http_status_code=~"5.."' if selector else 'http_status_code=~"5.."'
            chart_queries = [
                {
                    "title": "Latency Trend (p95)",
                    "query": (
                        f"histogram_quantile(0.95, sum(rate(http_server_duration_milliseconds_bucket{{{selector}}}[5m])) by (le))"
                        if selector
                        else "histogram_quantile(0.95, sum(rate(http_server_duration_milliseconds_bucket[5m])) by (le))"
                    ),
                },
                {
                    "title": "Request Rate Trend",
                    "query": (
                        f"sum(rate(http_server_duration_milliseconds_count{{{selector}}}[5m]))"
                        if selector
                        else "sum(rate(http_server_duration_milliseconds_count[5m]))"
                    ),
                },
                {
                    "title": "Error Rate Trend",
                    "query": f"sum(rate(http_server_duration_milliseconds_count{{{error_selector}}}[5m]))",
                },
            ]

        charts = []
        for item in chart_queries:
            data_points = []
            try:
                response = requests.get(
                    f"{self.base_url}/api/v1/query_range",
                    headers=self._headers(),
                    params={"query": item["query"], "start": start, "end": now, "step": step},
                    timeout=10,
                )
                response.raise_for_status()
                results = response.json().get("data", {}).get("result", [])
                if results:
                    for ts, val in results[0].get("values", []):
                        try:
                            dt = datetime.datetime.fromtimestamp(float(ts), tz=datetime.timezone.utc)
                            data_points.append({"timestamp": dt.isoformat(), "value": float(val)})
                        except (ValueError, TypeError):
                            pass
            except Exception:
                pass

            charts.append({
                "title": item["title"],
                "type": "line",
                "signal": "metrics",
                "source": "prometheus",
                "query": item["query"],
                "data": data_points,
            })

        return charts

    def investigate(self, service: str | None, time_range: str, message: str):
        selector = f'service_name="{service}"' if service else ""

        if service == "checkout":
            queries = [
                {
                    "name": "targets",
                    "query": "up",
                },
                {
                    "name": "checkout_cart_add_item_rate",
                    "query": "sum(rate(app_cart_add_item_latency_seconds_count[5m]))",
                },
                {
                    "name": "checkout_cart_add_item_p95_latency",
                    "query": "histogram_quantile(0.95, sum(rate(app_cart_add_item_latency_seconds_bucket[5m])) by (le))",
                },
                {
                    "name": "checkout_cart_get_cart_rate",
                    "query": "sum(rate(app_cart_get_cart_latency_seconds_count[5m]))",
                },
                {
                    "name": "checkout_cart_get_cart_p95_latency",
                    "query": "histogram_quantile(0.95, sum(rate(app_cart_get_cart_latency_seconds_bucket[5m])) by (le))",
                },
                {
                    "name": "checkout_slo_error_ratio_2h",
                    "query": 'slo:sli_error:ratio_rate2h{service="checkout"}',
                },
            ]
        else:
            selector = f'service_name="{service}"' if service else ""

            queries = [
                {
                    "name": "targets",
                    "query": "up",
                },
                {
                    "name": "request_rate",
                    "query": f"sum(rate(http_server_duration_milliseconds_count{{{selector}}}[5m]))",
                },
                {
                    "name": "p95_latency",
                    "query": f"histogram_quantile(0.95, sum(rate(http_server_duration_milliseconds_bucket{{{selector}}}[5m])) by (le))",
                },
                {
                    "name": "error_rate",
                    "query": f'sum(rate(http_server_duration_milliseconds_count{{{selector}, http_status_code=~"5.."}}[5m]))',
                },
            ]

        evidence = []

        for item in queries:
            try:
                response = requests.get(
                    f"{self.base_url}/api/v1/query",
                    headers=self._headers(),
                    params={"query": item["query"]},
                    timeout=10,
                )
                
                response.raise_for_status()
                data = response.json()
                result = data.get("data", {}).get("result", [])

                status = "ok" if result else "no_data"
                finding = (
                    f"Prometheus query executed: {item['name']}"
                    if result
                    else f"Prometheus query returned no data: {item['name']}"
                )

                evidence.append({
                    "source": "prometheus",
                    "signal": "metrics",
                    "finding": finding,
                    "query": item["query"],
                    "status": status,
                    "raw": data,
                })

            except Exception as exc:
                evidence.append({
                    "source": "prometheus",
                    "signal": "metrics",
                    "finding": f"Prometheus query failed: {item['name']} - {exc}",
                    "query": item["query"],
                    "status": "error",
                    "raw": None,
                })

        return evidence