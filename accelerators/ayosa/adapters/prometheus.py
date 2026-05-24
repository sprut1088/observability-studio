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

        queries = [
            {
                "name": "targets",
                "query": "up",
            },
            {
                "name": "request_rate",
                "query": f"sum(rate(http_server_duration_count{{{selector}}}[5m]))",
            },
            {
                "name": "p95_latency",
                "query": f"histogram_quantile(0.95, sum(rate(http_server_duration_bucket{{{selector}}}[5m])) by (le))",
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

                evidence.append({
                    "source": "prometheus",
                    "signal": "metrics",
                    "finding": f"Prometheus query executed: {item['name']}",
                    "query": item["query"],
                    "status": "ok",
                    "raw": response.json(),
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