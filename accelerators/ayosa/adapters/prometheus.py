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