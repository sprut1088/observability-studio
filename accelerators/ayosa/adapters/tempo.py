import requests


class TempoAdapter:
    signal = "traces"

    def __init__(self, base_url: str, auth_token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token

    def _headers(self):
        headers = {}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    def investigate(self, service: str | None, time_range: str, message: str):
        if not service:
            return [{
                "source": "tempo",
                "signal": "traces",
                "finding": "Tempo requires a service name for this first AYOSA version.",
                "query": None,
                "status": "not_implemented",
                "raw": None,
            }]

        query = f'{{resource.service.name="{service}"}}'

        try:
            response = requests.get(
                f"{self.base_url}/api/search",
                headers=self._headers(),
                params={"q": query, "limit": 20},
                timeout=10,
            )
            response.raise_for_status()

            return [{
                "source": "tempo",
                "signal": "traces",
                "finding": f"Retrieved Tempo trace search results for {service}.",
                "query": query,
                "status": "ok",
                "raw": response.json(),
            }]

        except Exception as exc:
            return [{
                "source": "tempo",
                "signal": "traces",
                "finding": f"Tempo query failed: {exc}",
                "query": query,
                "status": "error",
                "raw": None,
            }]