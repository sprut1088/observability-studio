import json
from urllib.parse import urlparse

import requests


class SplunkAdapter:
    signal = "logs"

    def __init__(self, base_url: str, auth_token: str | None = None):
        self.base_url = self._normalize_splunk_api_url(base_url)
        self.auth_token = auth_token

    def _normalize_splunk_api_url(self, base_url: str) -> str:
        raw = (base_url or "").rstrip("/")
        parsed = urlparse(raw)

        # Accept Splunk Web URL and convert to Splunk management/API URL.
        # Example: http://host:8000/en-US -> https://host:8089
        if parsed.port == 8000 or "/en-US" in parsed.path:
            host = parsed.hostname
            return f"https://{host}:8089"

        return raw

    def _build_search_query(self, service: str | None, message: str | None) -> str:
        search_query = "search index=user01-index"

        if service:
            search_query += f" {service}"

        message_l = (message or "").lower()

        # Add broader incident/error keywords when the user is investigating failures.
        if any(word in message_l for word in ["error", "fail", "failed", "failure", "timeout", "latency", "issue", "incident"]):
            search_query += (
                " (error OR exception OR timeout OR failed OR failure "
                "OR unavailable OR refused OR broken OR eof "
                'OR "invalid token" OR "request failed")'
            )

        return search_query

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

        search_query = self._build_search_query(service, message)

        headers = {
            "Authorization": f"Bearer {self.auth_token}"
        }

        try:
            response = requests.post(
                f"{self.base_url}/services/search/jobs/export",
                headers=headers,
                data={
                    "search": search_query,
                    "earliest_time": f"-{time_range}",
                    "latest_time": "now",
                    "output_mode": "json",
                },
                verify=False,
                timeout=30,
            )
            response.raise_for_status()

            events = []
            for line in response.text.splitlines():
                if not line.strip():
                    continue

                try:
                    item = json.loads(line)
                    if "result" in item:
                        event = item["result"]
                        event["_ayosa_source"] = "splunk"
                        events.append(event)
                except Exception:
                    continue

            return [{
                "source": "splunk",
                "signal": "logs",
                "finding": f"Retrieved {len(events)} recent log events from Splunk.",
                "query": search_query,
                "status": "ok",
                "raw": {
                    "results": events[:20],
                    "count": len(events),
                    "api_url_used": self.base_url,
                },
            }]

        except Exception as exc:
            return [{
                "source": "splunk",
                "signal": "logs",
                "finding": f"Splunk query failed: {exc}",
                "query": search_query,
                "status": "error",
                "raw": {
                    "api_url_used": self.base_url,
                },
            }]