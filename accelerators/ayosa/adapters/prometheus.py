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

    def investigate(self, service: str | None, time_range: str, message: str, plan: dict | None = None):
        intent = (plan or {}).get("intent") if isinstance(plan, dict) else None
        selector = f'service_name="{service}"' if service else ""

        if intent == "environment_health":
            queries = [
                {"name": "targets_total", "query": "count(up)"},
                {"name": "targets_up",    "query": "count(up == 1)"},
                {"name": "targets_down",  "query": "count(up == 0)"},
                {"name": "targets_by_job","query": "count by (job) (up == 1)"},
            ]
        elif intent == "healthy_services_list":
            # Return per-target up/down so the orchestrator can group by service/job/instance.
            queries = [
                {"name": "up_by_target", "query": "up"},
            ]
        elif intent == "latency_issues":
            if selector:
                latency_q = (
                    "histogram_quantile(0.95, "
                    f"sum by (le, service_name) (rate(http_server_duration_milliseconds_bucket{{{selector}}}[5m])))"
                )
            else:
                latency_q = (
                    "histogram_quantile(0.95, "
                    "sum by (le, service_name) (rate(http_server_duration_milliseconds_bucket[5m])))"
                )
            queries = [
                {"name": "p95_latency_by_service", "query": latency_q},
            ]
        elif intent == "service_health":
            up_q = f"up{{{selector}}}" if selector else "up"
            queries = [
                {"name": "targets", "query": up_q},
                {"name": "request_rate", "query": f"sum(rate(http_server_duration_milliseconds_count{{{selector}}}[5m]))"},
                {"name": "p95_latency",  "query": f"histogram_quantile(0.95, sum(rate(http_server_duration_milliseconds_bucket{{{selector}}}[5m])) by (le))"},
                {"name": "error_rate",   "query": f'sum(rate(http_server_duration_milliseconds_count{{{selector}, http_status_code=~"5.."}}[5m]))'},
            ]
        else:
            # Default / fallback (error_investigation, general_observability_question, unknown)
            queries = [
                {"name": "targets",      "query": "up"},
                {"name": "request_rate", "query": f"sum(rate(http_server_duration_milliseconds_count{{{selector}}}[5m]))"},
                {"name": "p95_latency",  "query": f"histogram_quantile(0.95, sum(rate(http_server_duration_milliseconds_bucket{{{selector}}}[5m])) by (le))"},
                {"name": "error_rate",   "query": f'sum(rate(http_server_duration_milliseconds_count{{{selector}, http_status_code=~"5.."}}[5m]))'},
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