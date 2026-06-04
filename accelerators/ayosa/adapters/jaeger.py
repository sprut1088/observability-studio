import datetime
import json
import time as _time_module

import requests


class JaegerAdapter:
    signal = "traces"

    def __init__(self, base_url: str, auth_token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token

    def _parse_time_range_seconds(self, time_range: str) -> int:
        tr = time_range.strip().lower()
        units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        for suffix, mult in units.items():
            if tr.endswith(suffix):
                try:
                    return int(tr[:-1]) * mult
                except ValueError:
                    break
        return 1800

    def get_charts(self, service: str | None, time_range: str) -> list[dict]:
        """Return trace count trend and slow trace count charts from Jaeger."""
        _empty = [
            {
                "title": "Trace Count Trend",
                "type": "line",
                "signal": "traces",
                "source": "jaeger",
                "query": None,
                "data": [],
            },
            {
                "title": "Slow Trace Count (>500ms)",
                "type": "line",
                "signal": "traces",
                "source": "jaeger",
                "query": None,
                "data": [],
            },
        ]

        if not service:
            return _empty

        now_us = int(_time_module.time() * 1_000_000)
        range_secs = self._parse_time_range_seconds(time_range)
        start_us = now_us - (range_secs * 1_000_000)
        bucket_secs = max(60, range_secs // 30)
        query_str = f"/api/traces?service={service}&start={start_us}&end={now_us}&limit=100"

        try:
            response = requests.get(
                f"{self.base_url}/api/traces",
                params={"service": service, "start": start_us, "end": now_us, "limit": 100},
                timeout=15,
            )
            response.raise_for_status()
            traces = response.json().get("data", [])
        except Exception:
            for chart in _empty:
                chart["query"] = query_str
            return _empty

        _SLOW_THRESHOLD_US = 500_000  # 500 ms

        trace_buckets: dict[int, int] = {}
        slow_buckets: dict[int, int] = {}

        for trace in traces:
            spans = trace.get("spans", [])
            if not spans:
                continue
            root_span = min(spans, key=lambda s: s.get("startTime", float("inf")), default=None)
            if not root_span:
                continue
            start_time_us = root_span.get("startTime", 0)
            duration_us = root_span.get("duration", 0)
            bucket_key = (int(start_time_us / (bucket_secs * 1_000_000))) * bucket_secs
            trace_buckets[bucket_key] = trace_buckets.get(bucket_key, 0) + 1
            if duration_us >= _SLOW_THRESHOLD_US:
                slow_buckets[bucket_key] = slow_buckets.get(bucket_key, 0) + 1

        def _to_points(buckets: dict[int, int]) -> list[dict]:
            points = []
            for ts, count in sorted(buckets.items()):
                try:
                    dt = datetime.datetime.fromtimestamp(float(ts), tz=datetime.timezone.utc)
                    points.append({"timestamp": dt.isoformat(), "value": float(count)})
                except (ValueError, OSError):
                    pass
            return points

        return [
            {
                "title": "Trace Count Trend",
                "type": "line",
                "signal": "traces",
                "source": "jaeger",
                "query": query_str,
                "data": _to_points(trace_buckets),
            },
            {
                "title": "Slow Trace Count (>500ms)",
                "type": "line",
                "signal": "traces",
                "source": "jaeger",
                "query": query_str,
                "data": _to_points(slow_buckets),
            },
        ]

    def investigate(self, service: str | None, time_range: str, message: str, plan: dict | None = None):
        intent = (plan or {}).get("intent") if isinstance(plan, dict) else None

        # ── Step 11: run LLM-emitted query first (additive) ──
        llm_evidence = self._run_llm_query(plan, service, time_range)

        # trace_lookup: needs a service. Without one, skip rather than dumping all services.
        if intent == "trace_lookup":
            if not service:
                return llm_evidence + [{
                    "source": "jaeger", "signal": "traces",
                    "finding": "Trace lookup requires a service name; none was provided or inferred.",
                    "query": None, "status": "skipped", "raw": None,
                }]
            endpoint = "/api/traces"
            params: dict = {"service": service, "limit": 20}

        # latency_issues: prefer slow traces for the service (minDuration filter).
        elif intent == "latency_issues":
            if not service:
                return llm_evidence + [{
                    "source": "jaeger", "signal": "traces",
                    "finding": "Latency analysis from traces requires a service; none was provided.",
                    "query": None, "status": "skipped", "raw": None,
                }]
            now_us = int(_time_module.time() * 1_000_000)
            start_us = now_us - (self._parse_time_range_seconds(time_range) * 1_000_000)
            endpoint = "/api/traces"
            params = {
                "service": service,
                "start": start_us,
                "end": now_us,
                "limit": 50,
                "minDuration": "500ms",
            }

        else:
            # Default behaviour preserved for other intents (service_health,
            # error_investigation, general_observability_question, etc.).
            if service:
                endpoint = "/api/traces"
                params = {"service": service, "limit": 20}
            else:
                endpoint = "/api/services"
                params = {}

        try:
            response = requests.get(
                f"{self.base_url}{endpoint}",
                params=params,
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
            query_repr = f"{endpoint} {params}"

            if intent == "latency_issues":
                traces = data.get("data", []) or []
                if not traces:
                    return llm_evidence + [{
                        "source": "jaeger", "signal": "traces",
                        "finding": "No slow traces (>500ms) found in the selected time window.",
                        "query": query_repr, "status": "no_data", "raw": data,
                    }]
                return llm_evidence + [{
                    "source": "jaeger", "signal": "traces",
                    "finding": f"Found {len(traces)} slow traces (>500ms) for {service}.",
                    "query": query_repr, "status": "ok", "raw": data,
                }]

            if intent == "trace_lookup":
                traces = data.get("data", []) or []
                if not traces:
                    return llm_evidence + [{
                        "source": "jaeger", "signal": "traces",
                        "finding": f"No traces found for service '{service}' in Jaeger.",
                        "query": query_repr, "status": "no_data", "raw": data,
                    }]
                return llm_evidence + [{
                    "source": "jaeger", "signal": "traces",
                    "finding": f"Retrieved {len(traces)} recent traces for {service}.",
                    "query": query_repr, "status": "ok", "raw": data,
                }]

            return llm_evidence + [{
                "source": "jaeger", "signal": "traces",
                "finding": "Jaeger trace data retrieved successfully.",
                "query": query_repr, "status": "ok", "raw": data,
            }]
        except Exception as exc:
            return llm_evidence + [{
                "source": "jaeger", "signal": "traces",
                "finding": f"Jaeger query failed: {exc}",
                "query": None, "status": "error", "raw": None,
            }]

    # ──────────────────────────────────────────────────────────────────
    # Step 11: execute the LLM-chosen Jaeger query
    # ──────────────────────────────────────────────────────────────────
    def _run_llm_query(
        self, plan: dict | None, service: str | None, time_range: str,
    ) -> list[dict]:
        """Run ``plan['active_tool_args']['query']`` against Jaeger.

        Accepts two shapes:
        * a JSON object → merged as query parameters to ``/api/traces``
          (e.g. ``{"service":"api","operation":"POST /pay","tags":"{\\"http.status_code\\":\\"500\\"}"}``);
        * any other text → used as the ``operation`` parameter, scoped
          to ``service`` when provided.
        Returns an empty list when no LLM query was emitted.
        """
        if not isinstance(plan, dict):
            return []
        ta = plan.get("active_tool_args")
        if not isinstance(ta, dict):
            return []
        raw_q = ta.get("query")
        if not isinstance(raw_q, str) or not raw_q.strip():
            return []
        reason = str(ta.get("reason") or "").strip()

        try:
            parsed = json.loads(raw_q)
        except (TypeError, ValueError):
            parsed = None

        if isinstance(parsed, dict):
            params: dict = {"limit": 20}
            if service:
                params["service"] = service
            params.update({k: v for k, v in parsed.items() if v is not None})
        else:
            if not service:
                return [{
                    "source": "jaeger", "signal": "traces",
                    "finding": "LLM-selected Jaeger query skipped: needs a service.",
                    "query": raw_q, "status": "skipped", "raw": None,
                }]
            params = {"service": service, "operation": raw_q, "limit": 20}

        try:
            response = requests.get(
                f"{self.base_url}/api/traces",
                params=params,
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
            traces = data.get("data", []) or []
            status = "ok" if traces else "no_data"
            finding = (
                f"LLM-selected Jaeger query executed: {reason}" if reason
                else "LLM-selected Jaeger query executed."
            ) if traces else (
                f"LLM-selected Jaeger query returned no traces: {reason}" if reason
                else "LLM-selected Jaeger query returned no traces."
            )
            return [{
                "source": "jaeger", "signal": "traces",
                "finding": finding,
                "query": raw_q,
                "status": status,
                "raw": data,
            }]
        except Exception as exc:
            return [{
                "source": "jaeger", "signal": "traces",
                "finding": f"LLM-selected Jaeger query failed: {exc}",
                "query": raw_q,
                "status": "error",
                "raw": None,
            }]