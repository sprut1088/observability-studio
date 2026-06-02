import datetime
import time as _time_module

import requests


class PrometheusAdapter:
    signal = "metrics"

    def __init__(self, base_url: str, auth_token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token

    def _headers(self):
        if self.auth_token:
            return {"Authorization": f"Bearer {self.auth_token}"}
        return {}

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
        now = int(_time_module.time())
        total_secs = self._parse_time_range_seconds(time_range)
        start = now - total_secs
        step = max(60, total_secs // 30)

        selector = f'service_name="{service}"' if service else ""
        error_selector = f'{selector}, http_status_code=~"5.."' if selector else 'http_status_code=~"5.."'
        chart_queries = [
            {
                "title": "Latency Trend (p95)",
                "query": (
                    f"histogram_quantile(0.95, sum(rate(http_server_duration_milliseconds_bucket{{{selector}}}[5m])) by (le))"
                    if selector
                    else "histogram_quantile(0.95, sum(rate(http_server_duration_milliseconds_bucket[5m])) by (le))"
                ),
            },
            {
                "title": "Request Rate Trend",
                "query": (
                    f"sum(rate(http_server_duration_milliseconds_count{{{selector}}}[5m]))"
                    if selector
                    else "sum(rate(http_server_duration_milliseconds_count[5m]))"
                ),
            },
            {
                "title": "Error Rate Trend",
                "query": f"sum(rate(http_server_duration_milliseconds_count{{{error_selector}}}[5m]))",
            },
        ]

        charts = []
        for item in chart_queries:
            data_points = []
            try:
                response = requests.get(
                    f"{self.base_url}/api/v1/query_range",
                    headers=self._headers(),
                    params={"query": item["query"], "start": start, "end": now, "step": step},
                    timeout=10,
                )
                response.raise_for_status()
                results = response.json().get("data", {}).get("result", [])
                if results:
                    for ts, val in results[0].get("values", []):
                        try:
                            dt = datetime.datetime.fromtimestamp(float(ts), tz=datetime.timezone.utc)
                            data_points.append({"timestamp": dt.isoformat(), "value": float(val)})
                        except (ValueError, TypeError):
                            pass
            except Exception:
                pass

            charts.append({
                "title": item["title"],
                "type": "line",
                "signal": "metrics",
                "source": "prometheus",
                "query": item["query"],
                "data": data_points,
            })

        return charts

    def investigate(self, service: str | None, time_range: str, message: str, plan: dict | None = None):
        intent = (plan or {}).get("intent") if isinstance(plan, dict) else None
        selector = f'service_name="{service}"' if service else ""

        # ── Service-stability / error-rate ranking ──
        # Metrics-only path; never used for RCA. Tries OTEL-style and
        # Spring-style HTTP server counters in order and uses the first
        # that returns data. Filters to services strictly below the
        # requested threshold and returns a single evidence dict whose
        # `raw.service_stability` carries the structured table.
        if intent == "service_stability_ranking":
            return self._rank_service_stability(
                time_range=time_range,
                threshold=float((plan or {}).get("threshold_percent") or 1.0),
            )

        if intent == "environment_health":
            queries = [
                {"name": "targets_total", "query": "count(up)"},
                {"name": "targets_up",    "query": "count(up == 1)"},
                {"name": "targets_down",  "query": "count(up == 0)"},
                {"name": "targets_by_job","query": "count by (job) (up == 1)"},
            ]
        elif intent == "healthy_services_list":
            # Return per-target up/down so the orchestrator can group by service/job/instance.
            queries = [
                {"name": "up_by_target", "query": "up"},
            ]
        elif intent == "latency_issues":
            if selector:
                latency_q = (
                    "histogram_quantile(0.95, "
                    f"sum by (le, service_name) (rate(http_server_duration_milliseconds_bucket{{{selector}}}[5m])))"
                )
            else:
                latency_q = (
                    "histogram_quantile(0.95, "
                    "sum by (le, service_name) (rate(http_server_duration_milliseconds_bucket[5m])))"
                )
            queries = [
                {"name": "p95_latency_by_service", "query": latency_q},
            ]
        elif intent == "service_health":
            up_q = f"up{{{selector}}}" if selector else "up"
            queries = [
                {"name": "targets", "query": up_q},
                {"name": "request_rate", "query": f"sum(rate(http_server_duration_milliseconds_count{{{selector}}}[5m]))"},
                {"name": "p95_latency",  "query": f"histogram_quantile(0.95, sum(rate(http_server_duration_milliseconds_bucket{{{selector}}}[5m])) by (le))"},
                {"name": "error_rate",   "query": f'sum(rate(http_server_duration_milliseconds_count{{{selector}, http_status_code=~"5.."}}[5m]))'},
            ]
        else:
            # Default / fallback (error_investigation, general_observability_question, unknown)
            queries = [
                {"name": "targets",      "query": "up"},
                {"name": "request_rate", "query": f"sum(rate(http_server_duration_milliseconds_count{{{selector}}}[5m]))"},
                {"name": "p95_latency",  "query": f"histogram_quantile(0.95, sum(rate(http_server_duration_milliseconds_bucket{{{selector}}}[5m])) by (le))"},
                {"name": "error_rate",   "query": f'sum(rate(http_server_duration_milliseconds_count{{{selector}, http_status_code=~"5.."}}[5m]))'},
            ]

        evidence = []

        for item in queries:
            try:
                response = requests.get(
                    f"{self.base_url}/api/v1/query",
                    headers=self._headers(),
                    params={"query": item["query"]},
                    timeout=10,
                )
                
                response.raise_for_status()
                data = response.json()
                result = data.get("data", {}).get("result", [])

                status = "ok" if result else "no_data"
                finding = (
                    f"Prometheus query executed: {item['name']}"
                    if result
                    else f"Prometheus query returned no data: {item['name']}"
                )

                evidence.append({
                    "source": "prometheus",
                    "signal": "metrics",
                    "finding": finding,
                    "query": item["query"],
                    "status": status,
                    "raw": data,
                })

            except Exception as exc:
                evidence.append({
                    "source": "prometheus",
                    "signal": "metrics",
                    "finding": f"Prometheus query failed: {item['name']} - {exc}",
                    "query": item["query"],
                    "status": "error",
                    "raw": None,
                })

        return evidence

    # ──────────────────────────────────────────────────────────────────
    # Service stability ranking
    # ──────────────────────────────────────────────────────────────────
    # PromQL templates tried in order. Replace `__RANGE__` with the
    # caller-supplied time range (e.g. "48h"). The first template that
    # returns a non-empty result wins.
    _STABILITY_TEMPLATES = [
        (
            "otel_http_server_duration_milliseconds_count",
            'sum by (service_name) (rate(http_server_duration_milliseconds_count'
            '{http_status_code=~"5.."}[__RANGE__]))'
            ' / '
            'sum by (service_name) (rate(http_server_duration_milliseconds_count'
            '[__RANGE__]))'
            ' * 100',
        ),
        (
            "otel_http_server_duration_count",
            'sum by (service_name) (rate(http_server_duration_count'
            '{http_status_code=~"5.."}[__RANGE__]))'
            ' / '
            'sum by (service_name) (rate(http_server_duration_count'
            '[__RANGE__]))'
            ' * 100',
        ),
        (
            "spring_http_server_requests_total",
            'sum by (service_name) (rate(http_server_requests_total'
            '{status=~"5.."}[__RANGE__]))'
            ' / '
            'sum by (service_name) (rate(http_server_requests_total'
            '[__RANGE__]))'
            ' * 100',
        ),
    ]

    def _rank_service_stability(
        self,
        *,
        time_range: str,
        threshold: float,
    ) -> list[dict]:
        """Return a single evidence dict carrying the stability ranking.

        Tries each PromQL template in order; uses whichever first returns
        data. Filters services to those strictly below `threshold` and
        sorts ascending by error rate. Never raises — failures become an
        ``error``-status evidence entry.
        """
        tr = (time_range or "").strip() or "30m"
        if threshold <= 0:
            threshold = 1.0

        last_query = ""
        last_error: str | None = None

        for template_name, template in self._STABILITY_TEMPLATES:
            query = template.replace("__RANGE__", tr)
            last_query = query
            try:
                response = requests.get(
                    f"{self.base_url}/api/v1/query",
                    headers=self._headers(),
                    params={"query": query},
                    timeout=15,
                )
                response.raise_for_status()
                data = response.json()
            except Exception as exc:
                last_error = f"{template_name}: {exc}"
                continue

            results = (data.get("data") or {}).get("result") or []
            if not results:
                continue

            ranked = self._parse_stability_results(
                results=results,
                threshold=threshold,
                query=query,
            )

            finding = (
                f"Found {len(ranked)} service(s) with error rate below "
                f"{threshold:g}% over the last {tr}."
                if ranked
                else f"No services found below {threshold:g}% error rate over the last {tr}."
            )

            return [{
                "source": "prometheus",
                "signal": "metrics",
                "finding": finding,
                "query": query,
                "status": "ok",
                "raw": {
                    "answer_type": "service_table",
                    "service_stability": ranked,
                    "threshold_percent": threshold,
                    "time_range": tr,
                    "template_used": template_name,
                    "raw_result_count": len(results),
                },
            }]

        # All templates exhausted without data
        finding_msg = (
            "No HTTP server metrics found in Prometheus for service "
            f"stability ranking over the last {tr}. "
            "Tried OTEL and Spring HTTP server counters."
        )
        if last_error:
            finding_msg += f" Last adapter error: {last_error}"

        return [{
            "source": "prometheus",
            "signal": "metrics",
            "finding": finding_msg,
            "query": last_query,
            "status": "no_data" if last_error is None else "error",
            "raw": {
                "answer_type": "service_table",
                "service_stability": [],
                "threshold_percent": threshold,
                "time_range": tr,
                "templates_tried": [name for name, _ in self._STABILITY_TEMPLATES],
            },
        }]

    @staticmethod
    def _parse_stability_results(
        *,
        results: list[dict],
        threshold: float,
        query: str,
    ) -> list[dict]:
        """Parse Prometheus instant-vector results into a stability table.

        Skips entries with missing `service_name`, non-numeric values,
        or NaN. Returns only services strictly below the threshold,
        sorted ascending by error rate.
        """
        rows: list[dict] = []
        seen: set[str] = set()
        for item in results:
            metric = item.get("metric") or {}
            svc = metric.get("service_name") or metric.get("service") or ""
            svc = str(svc).strip()
            if not svc or svc in seen:
                continue

            value = item.get("value") or [None, None]
            try:
                raw_pct = float(value[1])
            except (TypeError, ValueError, IndexError):
                continue
            if raw_pct != raw_pct:  # NaN guard
                continue

            pct = round(raw_pct, 4)
            if pct >= threshold:
                continue

            seen.add(svc)
            rows.append({
                "service": svc,
                "error_rate_percent": pct,
                "status": "stable",
                "threshold_percent": threshold,
                "source": "prometheus",
                "query": query,
            })

        rows.sort(key=lambda r: r["error_rate_percent"])
        return rows