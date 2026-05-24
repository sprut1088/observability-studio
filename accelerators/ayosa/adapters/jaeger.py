import requests


class JaegerAdapter:
    signal = "traces"

    def __init__(self, base_url: str, auth_token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token

    def investigate(self, service: str | None, time_range: str, message: str):
        try:
            if service:
                endpoint = "/api/traces"
                params = {"service": service, "limit": 20}
            else:
                endpoint = "/api/services"
                params = {}

            response = requests.get(
                f"{self.base_url}{endpoint}",
                params=params,
                timeout=10,
            )
            response.raise_for_status()

            return [{
                "source": "jaeger",
                "signal": "traces",
                "finding": "Jaeger trace data retrieved successfully.",
                "query": f"{endpoint} {params}",
                "status": "ok",
                "raw": response.json(),
            }]
        except Exception as exc:
            return [{
                "source": "jaeger",
                "signal": "traces",
                "finding": f"Jaeger query failed: {exc}",
                "query": None,
                "status": "error",
                "raw": None,
            }]