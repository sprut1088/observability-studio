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

    def _chart_span(self, time_range: str) -> str:
        """Determine Splunk timechart span for the given time_range."""
        tr = time_range.strip().lower()
        if tr.endswith("m"):
            return "1m"
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
        if not self.auth_token:
            return []

        span = self._chart_span(time_range)
        service_filter = f" {service}" if service else ""
        base_search = f"search index=user01-index{service_filter}"

        chart_queries = [
            {
                "title": "Error Count Over Time",
                "spl": f"{base_search} (error OR exception OR failed) | timechart span={span} count",
            },
            {
                "title": "Failed Request Count Over Time",
                "spl": (
                    f'{base_search} (timeout OR refused OR unavailable OR "request failed")'
                    f" | timechart span={span} count"
                ),
            },
        ]

        headers = {"Authorization": f"Bearer {self.auth_token}"}
        charts = []

        for item in chart_queries:
            data_points = []
            try:
                response = requests.post(
                    f"{self.base_url}/services/search/jobs/export",
                    headers=headers,
                    data={
                        "search": item["spl"],
                        "earliest_time": f"-{time_range}",
                        "latest_time": "now",
                        "output_mode": "json",
                    },
                    verify=False,
                    timeout=30,
                )
                response.raise_for_status()
                for line in response.text.splitlines():
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                        if "result" in obj:
                            result = obj["result"]
                            ts = result.get("_time", "")
                            raw_count = result.get("count", result.get("count()", 0))
                            try:
                                data_points.append({"timestamp": str(ts), "value": float(raw_count)})
                            except (ValueError, TypeError):
                                pass
                    except Exception:
                        continue
            except Exception:
                pass

            charts.append({
                "title": item["title"],
                "type": "line",
                "signal": "logs",
                "source": "splunk",
                "query": item["spl"],
                "data": data_points,
            })

        return charts

    _ERROR_KEYWORDS = ("error", "exception", "fail", "failed", "failure", "incident", "broken")

    def _build_search_query(
        self,
        service: str | None,
        message: str | None,
        intent: str | None = None,
    ) -> str | None:
        """Build an intent-aware SPL query.
        Returns None when the intent does not warrant a Splunk query.
        """
        base = "search index=user01-index"
        if service:
            base += f' "{service}"'
        msg_l = (message or "").lower()

        if intent == "latency_issues":
            return base + ' (latency OR slow OR timeout OR duration OR "response time")'

        if intent == "environment_health":
            return (
                base
                + ' (error OR exception OR failed OR failure OR warn OR warning)'
                + ' | stats count by host'
            )

        if intent == "healthy_services_list":
            return (
                base
                + ' | stats count as total_events'
                + ', count(eval(searchmatch("error OR exception OR failed OR failure"))) as error_count'
                + ' by host'
            )

        if intent in ("latest_error", "error_investigation"):
            return (
                base
                + ' (error OR exception OR timeout OR failed OR failure '
                + 'OR unavailable OR refused OR broken OR eof '
                + 'OR "invalid token" OR "request failed")'
            )

        if intent == "general_observability_question":
            if not any(k in msg_l for k in self._ERROR_KEYWORDS):
                return None
            return base + ' (error OR exception OR failed OR failure)'

        # Default / unknown intent: keep existing keyword-driven behaviour.
        if any(word in msg_l for word in self._ERROR_KEYWORDS + ("timeout", "latency", "issue")):
            return (
                base
                + ' (error OR exception OR timeout OR failed OR failure '
                + 'OR unavailable OR refused OR broken OR eof '
                + 'OR "invalid token" OR "request failed")'
            )
        return base

    def investigate(self, service: str | None, time_range: str, message: str, plan: dict | None = None):
        if not self.auth_token:
            return [{
                "source": "splunk",
                "signal": "logs",
                "finding": "Splunk auth token was not provided.",
                "query": None,
                "status": "error",
                "raw": None,
            }]

        intent = (plan or {}).get("intent") if isinstance(plan, dict) else None

        # ── Step 10: prefer the LLM-emitted SPL when present ──
        # When the tool-selector chose splunk with an explicit ``query``
        # argument we run it verbatim instead of the heuristic search.
        # Empty / non-string values fall through to the legacy builder.
        llm_query = _extract_llm_query(plan)
        if llm_query:
            search_query = llm_query
        else:
            search_query = self._build_search_query(service, message, intent)

        if search_query is None:
            return [{
                "source": "splunk",
                "signal": "logs",
                "finding": "Skipped Splunk error scan (general question with no error/failure keywords).",
                "query": None,
                "status": "skipped",
                "raw": None,
            }]

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

            if not events:
                return [{
                    "source": "splunk",
                    "signal": "logs",
                    "finding": "Splunk returned no matching events in the selected time window.",
                    "query": search_query,
                    "status": "no_data",
                    "raw": {
                        "results": [],
                        "count": 0,
                        "api_url_used": self.base_url,
                    },
                }]

            return [{
                "source": "splunk",
                "signal": "logs",
                "finding": f"Retrieved {len(events)} log events from Splunk.",
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


# ────────────────────────────────────────────────────────────────────── #
# Step 10 helper: pull the LLM-emitted query off the carried plan dict
# ────────────────────────────────────────────────────────────────────── #
def _extract_llm_query(plan: dict | None) -> str | None:
    """Return the LLM-chosen ``query`` from ``plan['active_tool_args']``
    or ``None`` when absent / blank. Defensive against malformed shapes.
    """
    if not isinstance(plan, dict):
        return None
    ta = plan.get("active_tool_args")
    if not isinstance(ta, dict):
        return None
    q = ta.get("query")
    if isinstance(q, str) and q.strip():
        return q.strip()
    return None