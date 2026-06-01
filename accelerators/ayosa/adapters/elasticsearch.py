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

    # ------------------------------------------------------------------
    # Intent-aware investigation (Stage 3)
    # ------------------------------------------------------------------

    _ERROR_KEYWORDS = ("error", "exception", "failed", "failure", "incident", "broken")

    def _service_filter(self, service: str | None) -> list[dict]:
        if not service:
            return []
        return [{
            "multi_match": {
                "query": service,
                "fields": ["service.name", "service_name", "resource.service.name"],
                "operator": "and",
            }
        }]

    def _time_filter(self, time_range: str) -> dict:
        now_ms = int(_time_module.time() * 1000)
        from_ms = now_ms - self._parse_time_range_millis(time_range)
        return {
            "range": {
                "@timestamp": {"gte": from_ms, "lte": now_ms, "format": "epoch_millis"}
            }
        }

    def _post_search(self, body: dict) -> dict:
        response = requests.post(
            f"{self.base_url}/_search",
            headers=self._headers(),
            json=body,
            verify=False,
            timeout=15,
        )
        response.raise_for_status()
        return response.json()

    def _search_logs(
        self,
        service: str | None,
        time_range: str,
        pattern: str,
        description: str,
    ) -> list[dict]:
        body = {
            "size": 20,
            "query": {
                "bool": {
                    "must": self._service_filter(service) + [{
                        "multi_match": {
                            "query": pattern,
                            "fields": ["body", "message", "log", "severity"],
                            "operator": "or",
                        }
                    }],
                    "filter": [self._time_filter(time_range)],
                }
            },
            "sort": [{"@timestamp": {"order": "desc", "unmapped_type": "date"}}],
        }
        try:
            data = self._post_search(body)
            hits = data.get("hits", {}).get("hits", [])
            if not hits:
                return [{
                    "source": "elasticsearch", "signal": "logs",
                    "finding": f"No {description} found in the selected time window.",
                    "query": str(body), "status": "no_data", "raw": data,
                }]
            return [{
                "source": "elasticsearch", "signal": "logs",
                "finding": f"Retrieved {len(hits)} {description} from Elasticsearch/OpenSearch.",
                "query": str(body), "status": "ok", "raw": data,
            }]
        except Exception as exc:
            return [{
                "source": "elasticsearch", "signal": "logs",
                "finding": f"Elasticsearch/OpenSearch query failed: {exc}",
                "query": str(body), "status": "error", "raw": None,
            }]

    def _aggregate_errors_by_service(self, service: str | None, time_range: str) -> list[dict]:
        body = {
            "size": 0,
            "query": {
                "bool": {
                    "must": self._service_filter(service) + [{
                        "multi_match": {
                            "query": "error exception failed failure warn warning",
                            "fields": ["body", "message", "log", "severity"],
                            "operator": "or",
                        }
                    }],
                    "filter": [self._time_filter(time_range)],
                }
            },
            "aggs": {
                "by_service": {
                    "terms": {
                        "field": "service.name.keyword",
                        "size": 25,
                        "missing": "(unknown)",
                    },
                    "aggs": {
                        "by_severity": {
                            "terms": {"field": "severity.keyword", "size": 5}
                        }
                    },
                }
            },
        }
        try:
            data = self._post_search(body)
            buckets = data.get("aggregations", {}).get("by_service", {}).get("buckets", [])
            if not buckets:
                return [{
                    "source": "elasticsearch", "signal": "logs",
                    "finding": "No error/warn logs found in the selected time window.",
                    "query": str(body), "status": "no_data", "raw": data,
                }]
            return [{
                "source": "elasticsearch", "signal": "logs",
                "finding": f"Aggregated error/warn log counts across {len(buckets)} services.",
                "query": str(body), "status": "ok", "raw": data,
            }]
        except Exception as exc:
            return [{
                "source": "elasticsearch", "signal": "logs",
                "finding": f"Elasticsearch aggregation failed: {exc}",
                "query": str(body), "status": "error", "raw": None,
            }]

    def _discover_services_with_errors(self, time_range: str) -> list[dict]:
        # Discover service names from any log in the window (no error filter) and
        # report error-count per service as a sub-aggregation.
        body = {
            "size": 0,
            "query": {
                "bool": {"filter": [self._time_filter(time_range)]}
            },
            "aggs": {
                "by_service": {
                    "terms": {
                        "field": "service.name.keyword",
                        "size": 50,
                        "missing": "(unknown)",
                    },
                    "aggs": {
                        "errors": {
                            "filter": {
                                "multi_match": {
                                    "query": "error exception failed failure",
                                    "fields": ["body", "message", "log", "severity"],
                                    "operator": "or",
                                }
                            }
                        }
                    },
                }
            },
        }
        try:
            data = self._post_search(body)
            buckets = data.get("aggregations", {}).get("by_service", {}).get("buckets", [])
            if not buckets:
                return [{
                    "source": "elasticsearch", "signal": "logs",
                    "finding": "Could not discover any services from logs (no service.name field or no data).",
                    "query": str(body), "status": "no_data", "raw": data,
                }]
            return [{
                "source": "elasticsearch", "signal": "logs",
                "finding": f"Discovered {len(buckets)} services with recent log activity.",
                "query": str(body), "status": "ok", "raw": data,
            }]
        except Exception as exc:
            return [{
                "source": "elasticsearch", "signal": "logs",
                "finding": f"Elasticsearch service discovery failed: {exc}",
                "query": str(body), "status": "error", "raw": None,
            }]

    def investigate(self, service: str | None, time_range: str, message: str, plan: dict | None = None):
        intent = (plan or {}).get("intent") if isinstance(plan, dict) else None
        msg_l = (message or "").lower()

        if intent == "latency_issues":
            return self._search_logs(
                service, time_range,
                pattern='latency OR slow OR timeout OR duration OR "response time"',
                description="latency-related log events",
            )

        if intent == "environment_health":
            return self._aggregate_errors_by_service(service, time_range)

        if intent == "healthy_services_list":
            return self._discover_services_with_errors(time_range)

        if intent == "general_observability_question":
            if not any(k in msg_l for k in self._ERROR_KEYWORDS):
                return [{
                    "source": "elasticsearch", "signal": "logs",
                    "finding": "Skipped generic error log scan (question did not mention errors/failures).",
                    "query": None, "status": "skipped", "raw": None,
                }]
            # fall through to default error search

        # Default: latest_error, error_investigation, and fallback for unknown intents.
        return self._search_logs(
            service, time_range,
            pattern="error OR exception OR failed OR failure OR timeout",
            description="recent error-like events",
        )