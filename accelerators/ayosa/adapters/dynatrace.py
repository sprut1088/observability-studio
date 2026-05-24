import requests


class DynatraceAdapter:
    signal = "observability"

    def __init__(self, base_url: str, auth_token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token

    def _headers(self):
        if self.auth_token:
            return {"Authorization": f"Api-Token {self.auth_token}"}
        return {}

    def investigate(self, service: str | None, time_range: str, message: str):
        if not self.auth_token:
            return [{
                "source": "dynatrace",
                "signal": "observability",
                "finding": "Dynatrace API token was not provided.",
                "query": None,
                "status": "error",
                "raw": None,
            }]

        try:
            response = requests.get(
                f"{self.base_url}/api/v2/problems",
                headers=self._headers(),
                timeout=10,
            )
            response.raise_for_status()

            return [{
                "source": "dynatrace",
                "signal": "problems",
                "finding": "Retrieved current Dynatrace problems.",
                "query": "/api/v2/problems",
                "status": "ok",
                "raw": response.json(),
            }]

        except Exception as exc:
            return [{
                "source": "dynatrace",
                "signal": "problems",
                "finding": f"Dynatrace query failed: {exc}",
                "query": "/api/v2/problems",
                "status": "error",
                "raw": None,
            }]