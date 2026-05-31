import datetime
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

    def investigate(self, service: str | None, time_range: str, message: str):
        try:
            if service:
                endpoint = "/api/traces"
                params = {"service": service, "limit": 20}
            else:
                endpoint = "/api/services"
                params = {}

            response = requests.get(
                f"{self.base_url}{endpoint}",
                params=params,
                timeout=10,
            )
            response.raise_for_status()

            return [{
                "source": "jaeger",
                "signal": "traces",
                "finding": "Jaeger trace data retrieved successfully.",
                "query": f"{endpoint} {params}",
                "status": "ok",
                "raw": response.json(),
            }]
        except Exception as exc:
            return [{
                "source": "jaeger",
                "signal": "traces",
                "finding": f"Jaeger query failed: {exc}",
                "query": None,
                "status": "error",
                "raw": None,
            }]