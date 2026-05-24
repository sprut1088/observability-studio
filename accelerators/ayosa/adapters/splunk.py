import time
import requests


class SplunkAdapter:
    signal = "logs"

    def __init__(self, base_url: str, auth_token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token

    def investigate(self, service: str | None, time_range: str, message: str):
        if not self.auth_token:
            return [{
                "source": "splunk",
                "signal": "logs",
                "finding": "Splunk auth token was not provided.",
                "query": None,
                "status": "error",
                "raw": None,
            }]

        search_query = 'search index=* (error OR exception OR timeout OR failed)'
        if service:
            search_query += f' "{service}"'

        headers = {
            "Authorization": f"Bearer {self.auth_token}"
        }

        try:
            create_response = requests.post(
                f"{self.base_url}/services/search/jobs",
                headers=headers,
                data={
                    "search": search_query,
                    "earliest_time": f"-{time_range}",
                    "latest_time": "now",
                    "output_mode": "json",
                },
                verify=False,
                timeout=20,
            )
            create_response.raise_for_status()

            sid = create_response.json().get("sid")

            if not sid:
                return [{
                    "source": "splunk",
                    "signal": "logs",
                    "finding": "Splunk search job was created but no search id was returned.",
                    "query": search_query,
                    "status": "error",
                    "raw": create_response.json(),
                }]

            time.sleep(2)

            results_response = requests.get(
                f"{self.base_url}/services/search/jobs/{sid}/results",
                headers=headers,
                params={
                    "output_mode": "json",
                    "count": 20,
                },
                verify=False,
                timeout=20,
            )
            results_response.raise_for_status()

            return [{
                "source": "splunk",
                "signal": "logs",
                "finding": "Retrieved recent error-like log events from Splunk.",
                "query": search_query,
                "status": "ok",
                "raw": results_response.json(),
            }]

        except Exception as exc:
            return [{
                "source": "splunk",
                "signal": "logs",
                "finding": f"Splunk query failed: {exc}",
                "query": search_query,
                "status": "error",
                "raw": None,
            }]