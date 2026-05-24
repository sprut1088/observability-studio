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

        must = [
            {
                "multi_match": {
                    "query": "error exception timeout failed",
                    "fields": ["body", "message", "log", "severity", "service.name", "service_name"],
                    "operator": "or"
                }
            }
        ]

        if service:
            must.append({
                "multi_match": {
                    "query": service,
                    "fields": ["service.name", "service_name", "resource.service.name", "body", "message"],
                    "operator": "and"
                }
            })

        body = {
            "size": 20,
            "query": {
                "bool": {
                    "must": must
                }
            },
            "sort": [
                {
                    "@timestamp": {
                        "order": "desc",
                        "unmapped_type": "date"
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