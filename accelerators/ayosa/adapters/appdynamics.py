import requests


class AppDynamicsAdapter:
    signal = "observability"

    def __init__(self, base_url: str, auth_token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token

    def _headers(self):
        if self.auth_token:
            return {"Authorization": f"Bearer {self.auth_token}"}
        return {}

    def investigate(self, service: str | None, time_range: str, message: str):
        if not self.auth_token:
            return [{
                "source": "appdynamics",
                "signal": "observability",
                "finding": "AppDynamics auth token was not provided.",
                "query": None,
                "status": "error",
                "raw": None,
            }]

        return [{
            "source": "appdynamics",
            "signal": "observability",
            "finding": "AppDynamics adapter is registered. Detailed application, tier, node, and event integration will be added in the AppDynamics-specific phase.",
            "query": None,
            "status": "not_implemented",
            "raw": None,
        }]