import requests


class AlertmanagerAdapter:
    signal = "alerts"

    def __init__(self, base_url: str, auth_token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token

    def investigate(self, service: str | None, time_range: str, message: str):
        try:
            response = requests.get(
                f"{self.base_url}/api/v2/alerts",
                timeout=10,
            )
            response.raise_for_status()

            alerts = response.json()

            if service:
                alerts = [
                    alert for alert in alerts
                    if service in str(alert)
                ]

            return [{
                "source": "alertmanager",
                "signal": "alerts",
                "finding": f"Retrieved {len(alerts)} matching active alerts.",
                "query": "/api/v2/alerts",
                "status": "ok",
                "raw": alerts,
            }]
        except Exception as exc:
            return [{
                "source": "alertmanager",
                "signal": "alerts",
                "finding": f"Alertmanager query failed: {exc}",
                "query": "/api/v2/alerts",
                "status": "error",
                "raw": None,
            }]