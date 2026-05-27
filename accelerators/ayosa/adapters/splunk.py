import json
from urllib.parse import urlparse

import requests


class SplunkAdapter:
    signal = "logs"

    def __init__(self, base_url: str, auth_token: str | None = None):
        self.base_url = self._normalize_splunk_api_url(base_url)
        self.auth_token = auth_token

    def _normalize_splunk_api_url(self, base_url: str) -> str:
        """
        AYOSA accepts either Splunk Web URL or Splunk API URL.

        Converts:
          http://host:8000/en-US
          http://host:8000
        To:
          https://host:8089
        """
        raw = (base_url or "").rstrip("/")
        parsed = urlparse(raw)

        if parsed.port == 8000 or "/en-US" in parsed.path:
            host = parsed.hostname
            return f"https://{host}:8089"

        return raw

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

        search_query = 'search index=user01-index (error OR exception OR timeout OR failed)'
        if service:
            search_query += f' "service.name"={service}'

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
                        events.append(item["result"])
                except Exception:
                    continue

            return [{
                "source": "splunk",
                "signal": "logs",
                "finding": f"Retrieved {len(events)} recent error-like log events from Splunk.",
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