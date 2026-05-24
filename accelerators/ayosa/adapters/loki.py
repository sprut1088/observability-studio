import requests


class LokiAdapter:
    signal = "logs"

    def __init__(self, base_url: str, auth_token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token

    def _headers(self):
        headers = {}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    def investigate(self, service: str | None, time_range: str, message: str):
        if service:
            query = f'{{service_name="{service}"}} |= "error"'
        else:
            query = '{job=~".+"} |= "error"'

        try:
            response = requests.get(
                f"{self.base_url}/loki/api/v1/query",
                headers=self._headers(),
                params={"query": query},
                timeout=10,
            )
            response.raise_for_status()

            return [{
                "source": "loki",
                "signal": "logs",
                "finding": "Retrieved recent error logs from Loki.",
                "query": query,
                "status": "ok",
                "raw": response.json(),
            }]

        except Exception as exc:
            return [{
                "source": "loki",
                "signal": "logs",
                "finding": f"Loki query failed: {exc}",
                "query": query,
                "status": "error",
                "raw": None,
            }]