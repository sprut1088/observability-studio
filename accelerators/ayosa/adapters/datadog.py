import requests


class DatadogAdapter:
    signal = "observability"

    def __init__(self, base_url: str, auth_token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token

    def investigate(self, service: str | None, time_range: str, message: str):
        if not self.auth_token:
            return [{
                "source": "datadog",
                "signal": "observability",
                "finding": "Datadog API token was not provided.",
                "query": None,
                "status": "error",
                "raw": None,
            }]

        return [{
            "source": "datadog",
            "signal": "observability",
            "finding": "Datadog adapter is registered. Detailed metrics/logs/traces API integration will be added in the Datadog-specific phase.",
            "query": None,
            "status": "not_implemented",
            "raw": None,
        }]