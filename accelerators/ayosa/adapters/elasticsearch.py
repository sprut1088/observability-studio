import datetime
import time as _time_module

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

    def _parse_time_range_millis(self, time_range: str) -> int:
        tr = time_range.strip().lower()
        units = {"s": 1_000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}
        for suffix, mult in units.items():
            if tr.endswith(suffix):
                try:
                    return int(tr[:-1]) * mult
                except ValueError:
                    break
        return 1_800_000

    def _fixed_interval(self, time_range: str) -> str:
        tr = time_range.strip().lower()
        if tr.endswith("m"):
            mins = int(tr[:-1])
            return "1m" if mins <= 30 else "2m"
        if tr.endswith("h"):
            hours = int(tr[:-1])
            if hours <= 2:
                return "5m"
            if hours <= 6:
                return "15m"
            return "30m"
        if tr.endswith("d"):
            return "1h"
        return "5m"

    def get_charts(self, service: str | None, time_range: str) -> list[dict]:
        now_ms = int(_time_module.time() * 1000)
        from_ms = now_ms - self._parse_time_range_millis(time_range)
        interval = self._fixed_interval(time_range)

        service_filter: list[dict] = []
        if service:
            service_filter.append({
                "multi_match": {
                    "query": service,
                    "fields": ["service.name", "service_name", "resource.service.name"],
                    "operator": "and",
                }
            })

        charts_config = [
            {
                "title": "Error Count Over Time",
                "pattern": "error OR exception OR failed",
            },
            {
                "title": "Warning Count Over Time",
                "pattern": "warn OR warning",
            },
        ]

        charts = []
        for cfg in charts_config:
            body = {
                "size": 0,
                "query": {
                    "bool": {
                        "must": service_filter + [
                            {
                                "multi_match": {
                                    "query": cfg["pattern"],
                                    "fields": ["body", "message", "log", "severity"],
                                    "operator": "or",
                                }
                            }
                        ],
                        "filter": [
                            {
                                "range": {
                                    "@timestamp": {
                                        "gte": from_ms,
                                        "lte": now_ms,
                                        "format": "epoch_millis",
                                    }
                                }
                            }
                        ],
                    }
                },
                "aggs": {
                    "over_time": {
                        "date_histogram": {
                            "field": "@timestamp",
                            "fixed_interval": interval,
                            "min_doc_count": 0,
                        }
                    }
                },
            }
            data_points = []
            try:
                response = requests.post(
                    f"{self.base_url}/_search",
                    headers=self._headers(),
                    json=body,
                    verify=False,
                    timeout=15,
                )
                response.raise_for_status()
                buckets = (
                    response.json()
                    .get("aggregations", {})
                    .get("over_time", {})
                    .get("buckets", [])
                )
                for bucket in buckets:
                    try:
                        dt = datetime.datetime.fromtimestamp(
                            bucket["key"] / 1000, tz=datetime.timezone.utc
                        )
                        data_points.append({
                            "timestamp": dt.isoformat(),
                            "value": float(bucket["doc_count"]),
                        })
                    except (KeyError, ValueError, TypeError):
                        pass
            except Exception:
                pass

            charts.append({
                "title": cfg["title"],
                "type": "line",
                "signal": "logs",
                "source": "elasticsearch",
                "query": str(body),
                "data": data_points,
            })

        return charts

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