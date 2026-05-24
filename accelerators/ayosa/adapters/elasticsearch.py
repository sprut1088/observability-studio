import requests


class ElasticsearchAdapter:
    signal = "logs"

    def __init__(self, base_url: str, auth_token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token

    def _headers(self):
        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    def investigate(self, service: str | None, time_range: str, message: str):
        query_text = "error OR exception OR timeout OR failed"
        if service:
            query_text = f'({query_text}) AND "{service}"'

        body = {
            "size": 20,
            "query": {
                "query_string": {
                    "query": query_text
                }
            },
            "sort": [
                {
                    "@timestamp": {
                        "order": "desc"
                    }
                }
            ]
        }

        try:
            response = requests.post(
                f"{self.base_url}/_search",
                headers=self._headers(),
                json=body,
                verify=False,
                timeout=15,
            )
            response.raise_for_status()

            return [{
                "source": "elasticsearch",
                "signal": "logs",
                "finding": "Retrieved recent error-like events from Elasticsearch/OpenSearch.",
                "query": str(body),
                "status": "ok",
                "raw": response.json(),
            }]

        except Exception as exc:
            return [{
                "source": "elasticsearch",
                "signal": "logs",
                "finding": f"Elasticsearch/OpenSearch query failed: {exc}",
                "query": str(body),
                "status": "error",
                "raw": None,
            }]