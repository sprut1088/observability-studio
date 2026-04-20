"""
signal_collector.py
───────────────────
Collects observability signals from Prometheus, Grafana, Jaeger, and
OpenSearch/Elasticsearch using the same adapter pattern as ObservaScore.

Each fetch_* method returns a structured dict of signals so the
CorrelationEngine can work against a tool-agnostic view.
"""
from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

# ── Allow the observascore adapter package to be resolved when this module
#    is imported from the backend (sys.path augmentation).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_ACCELERATORS = _REPO_ROOT / "accelerators"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_ACCELERATORS) not in sys.path:
    sys.path.insert(0, str(_ACCELERATORS))

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Lightweight HTTP helper (no heavy dependency)
# ─────────────────────────────────────────────────────────────────────────────

def _http_get(base_url: str, path: str, token: str | None = None,
              timeout: int = 30) -> Any | None:
    """GET a JSON endpoint with simple retry on transient errors; return parsed body or None."""
    url = base_url.rstrip("/") + path
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    _retry_statuses = {429, 500, 502, 503, 504}
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout, verify=False)
            if resp.status_code in _retry_statuses and attempt < 2:
                wait = 1.0 * (2 ** attempt)
                logger.debug("GET %s → HTTP %s, retrying in %.0fs …", url, resp.status_code, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.ConnectionError as exc:
            if attempt < 2:
                time.sleep(1.0 * (2 ** attempt))
                continue
            logger.debug("GET %s failed after 3 attempts: %s", url, exc)
            return None
        except requests.exceptions.Timeout as exc:
            if attempt < 2:
                time.sleep(1.0 * (2 ** attempt))
                continue
            logger.debug("GET %s timed out after 3 attempts: %s", url, exc)
            return None
        except Exception as exc:
            logger.debug("GET %s failed: %s", url, exc)
            return None
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Data classes for normalised signals
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FiringAlert:
    name: str
    severity: str
    labels: dict
    annotations: dict
    starts_at: str
    source_tool: str


@dataclass
class MetricSample:
    name: str
    labels: dict
    value: float
    timestamp: float
    source_tool: str = "prometheus"


@dataclass
class TraceSpan:
    trace_id: str
    span_id: str
    operation_name: str
    service_name: str
    duration_us: int          # microseconds
    status: str               # ok | error
    tags: dict
    source_tool: str = "jaeger"


@dataclass
class LogEntry:
    timestamp: str
    level: str
    message: str
    index: str
    source_tool: str = "opensearch"


@dataclass
class CollectedSignals:
    """Container for all signals gathered across tools."""
    firing_alerts: list[FiringAlert] = field(default_factory=list)
    alert_rules: list[dict] = field(default_factory=list)
    recording_rules: list[dict] = field(default_factory=list)
    scrape_targets: list[dict] = field(default_factory=list)
    metric_samples: list[MetricSample] = field(default_factory=list)
    traces: list[TraceSpan] = field(default_factory=list)
    slow_traces: list[dict] = field(default_factory=list)   # top slow traces
    error_logs: list[LogEntry] = field(default_factory=list)
    dashboards: list[dict] = field(default_factory=list)
    services: list[str] = field(default_factory=list)       # service names from Jaeger
    collection_errors: list[str] = field(default_factory=list)
    tool_latencies: dict[str, float] = field(default_factory=dict)  # ms
    collected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ─────────────────────────────────────────────────────────────────────────────
# SignalCollector
# ─────────────────────────────────────────────────────────────────────────────

class SignalCollector:
    """
    Collects observability signals from multiple configured tool endpoints.

    Usage:
        collector = SignalCollector(tools)
        signals   = collector.collect_all(time_window_minutes=15)
    """

    def __init__(self, tools: list[dict[str, Any]]):
        """
        tools: list of dicts with keys:
            tool_name  – "prometheus" | "grafana" | "jaeger" | "opensearch" | "elasticsearch"
            base_url   – e.g. "http://host:9090"
            auth_token – optional bearer / API key
        """
        self.tools = {t["tool_name"].lower(): t for t in tools}

    # ─────────────────────────────────────────────────────────────────────────
    # Public entry point
    # ─────────────────────────────────────────────────────────────────────────

    def collect_all(self, time_window_minutes: int = 15) -> CollectedSignals:
        """Pull signals from every configured tool and merge into CollectedSignals."""
        signals = CollectedSignals()

        for tool_name, cfg in self.tools.items():
            t0 = time.monotonic()
            try:
                if tool_name == "prometheus":
                    self._collect_prometheus(cfg, signals)
                elif tool_name == "grafana":
                    self._collect_grafana(cfg, signals)
                elif tool_name in ("jaeger",):
                    self._collect_jaeger(cfg, signals, time_window_minutes)
                elif tool_name in ("opensearch", "elasticsearch"):
                    self._collect_opensearch(cfg, signals, time_window_minutes)
                else:
                    logger.warning("No collector for tool '%s'", tool_name)
            except Exception as exc:
                msg = f"{tool_name}: unexpected error — {exc}"
                signals.collection_errors.append(msg)
                logger.error(msg, exc_info=True)
            signals.tool_latencies[tool_name] = round((time.monotonic() - t0) * 1000, 1)

        return signals

    # ─────────────────────────────────────────────────────────────────────────
    # Prometheus
    # ─────────────────────────────────────────────────────────────────────────

    def _collect_prometheus(self, cfg: dict, signals: CollectedSignals) -> None:
        url   = cfg["base_url"]
        token = cfg.get("auth_token")

        # Firing alerts from Alertmanager via Prometheus /api/v1/alerts
        data = _http_get(url, "/api/v1/alerts", token)
        if data:
            for alert in data.get("data", {}).get("alerts", []):
                if alert.get("state") == "firing":
                    signals.firing_alerts.append(FiringAlert(
                        name=alert.get("labels", {}).get("alertname", "unknown"),
                        severity=alert.get("labels", {}).get("severity", "unknown"),
                        labels=alert.get("labels", {}),
                        annotations=alert.get("annotations", {}),
                        starts_at=alert.get("activeAt", ""),
                        source_tool="prometheus",
                    ))

        # Alert + recording rules
        rules_data = _http_get(url, "/api/v1/rules", token)
        if rules_data:
            for group in rules_data.get("data", {}).get("groups", []):
                for rule in group.get("rules", []):
                    rtype = rule.get("type", "")
                    if rtype == "alerting":
                        signals.alert_rules.append({
                            "name":       rule.get("name", ""),
                            "expression": rule.get("query", ""),
                            "severity":   rule.get("labels", {}).get("severity", ""),
                            "state":      rule.get("state", ""),
                            "group":      group.get("name", ""),
                        })
                    elif rtype == "recording":
                        signals.recording_rules.append({
                            "name":       rule.get("name", ""),
                            "expression": rule.get("query", ""),
                            "group":      group.get("name", ""),
                        })

        # Scrape targets
        targets_data = _http_get(url, "/api/v1/targets", token)
        if targets_data:
            for tgt in targets_data.get("data", {}).get("activeTargets", []):
                signals.scrape_targets.append({
                    "job":      tgt.get("labels", {}).get("job", ""),
                    "instance": tgt.get("labels", {}).get("instance", ""),
                    "health":   tgt.get("health", "unknown"),
                    "error":    tgt.get("lastError") or None,
                })

        # Key metric samples: error rates, latency p99, CPU, memory
        _GOLDEN_QUERIES = [
            ("http_requests_total",                'sum(rate(http_requests_total[5m])) by (job, status)'),
            ("http_request_duration_p99",          'histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket[5m])) by (le, job))'),
            ("process_cpu_seconds_rate",           'sum(rate(process_cpu_seconds_total[5m])) by (job)'),
            ("process_resident_memory_bytes",      'sum(process_resident_memory_bytes) by (job)'),
            ("go_gc_duration_seconds_p99",         'histogram_quantile(0.99, sum(rate(go_gc_duration_seconds_bucket[5m])) by (le))'),
        ]
        for metric_name, query in _GOLDEN_QUERIES:
            # Use params properly
            try:
                resp = requests.get(
                    url.rstrip("/") + "/api/v1/query",
                    params={"query": query},
                    headers={"Authorization": f"Bearer {token}"} if token else {},
                    timeout=10,
                    verify=False,
                )
                if resp.status_code == 200:
                    qdata = resp.json()
                    for res in qdata.get("data", {}).get("result", [])[:5]:  # cap at 5
                        try:
                            value = float(res["value"][1])
                        except (KeyError, ValueError, IndexError):
                            value = 0.0
                        signals.metric_samples.append(MetricSample(
                            name=metric_name,
                            labels=res.get("metric", {}),
                            value=value,
                            timestamp=res.get("value", [0])[0],
                        ))
            except Exception:
                pass  # best-effort metric collection

    # ─────────────────────────────────────────────────────────────────────────
    # Grafana
    # ─────────────────────────────────────────────────────────────────────────

    def _collect_grafana(self, cfg: dict, signals: CollectedSignals) -> None:
        url   = cfg["base_url"]
        token = cfg.get("auth_token")

        # Dashboards
        folders_data = _http_get(url, "/api/search?type=dash-db&limit=100", token)
        if folders_data and isinstance(folders_data, list):
            for db in folders_data[:50]:  # cap
                signals.dashboards.append({
                    "uid":   db.get("uid", ""),
                    "title": db.get("title", ""),
                    "url":   db.get("url", ""),
                    "tags":  db.get("tags", []),
                })

        # Grafana alert rules (Grafana 8+ Ruler API)
        alert_data = _http_get(url, "/api/ruler/grafana/api/v1/rules", token)
        if alert_data and isinstance(alert_data, dict):
            for groups in alert_data.values():
                for group in (groups or []):
                    for rule in group.get("rules", []):
                        grafana_state = rule.get("grafana_alert", {})
                        signals.alert_rules.append({
                            "name":     grafana_state.get("title", rule.get("alert", "")),
                            "state":    grafana_state.get("state", "unknown"),
                            "severity": rule.get("labels", {}).get("severity", ""),
                            "group":    group.get("name", ""),
                            "source":   "grafana",
                        })

        # Datasources (tells us what backends Grafana talks to)
        ds_data = _http_get(url, "/api/datasources", token)
        if ds_data and isinstance(ds_data, list):
            for ds in ds_data:
                signals.scrape_targets.append({
                    "job":      ds.get("name", ""),
                    "instance": ds.get("url", ""),
                    "health":   "ok" if ds.get("basicAuth") is not None else "unknown",
                    "error":    None,
                    "source":   "grafana-datasource",
                    "type":     ds.get("type", ""),
                })

    # ─────────────────────────────────────────────────────────────────────────
    # Jaeger
    # ─────────────────────────────────────────────────────────────────────────

    def _collect_jaeger(self, cfg: dict, signals: CollectedSignals,
                        time_window_minutes: int) -> None:
        url   = cfg["base_url"]
        token = cfg.get("auth_token")

        # Service list
        services_data = _http_get(url, "/api/services", token)
        if services_data:
            svcs = services_data.get("data", [])
            signals.services.extend(svcs)

            # For each service, sample recent traces to find slow/errored spans
            end_us   = int(time.time() * 1_000_000)
            start_us = end_us - time_window_minutes * 60 * 1_000_000

            for svc in svcs[:10]:  # cap: top 10 services
                params = {
                    "service":   svc,
                    "start":     start_us,
                    "end":       end_us,
                    "limit":     20,
                    "lookback":  "custom",
                }
                try:
                    resp = requests.get(
                        url.rstrip("/") + "/api/traces",
                        params=params,
                        headers={"Authorization": f"Bearer {token}"} if token else {},
                        timeout=15,
                        verify=False,
                    )
                    if resp.status_code != 200:
                        continue
                    trace_data = resp.json()
                    for trace in trace_data.get("data", []):
                        for span in trace.get("spans", []):
                            has_error = any(
                                t.get("key") == "error" and t.get("value") in (True, "true")
                                for t in span.get("tags", [])
                            )
                            duration_us = span.get("duration", 0)
                            signals.traces.append(TraceSpan(
                                trace_id=span.get("traceID", ""),
                                span_id=span.get("spanID", ""),
                                operation_name=span.get("operationName", ""),
                                service_name=svc,
                                duration_us=duration_us,
                                status="error" if has_error else "ok",
                                tags={t["key"]: t["value"] for t in span.get("tags", [])},
                            ))
                except Exception as exc:
                    signals.collection_errors.append(f"jaeger/{svc}: {exc}")

        # Build slow_traces summary (top 10 by duration)
        signals.slow_traces = sorted(
            [
                {
                    "service":        t.service_name,
                    "operation":      t.operation_name,
                    "duration_ms":    round(t.duration_us / 1000, 2),
                    "status":         t.status,
                }
                for t in signals.traces
            ],
            key=lambda x: x["duration_ms"],
            reverse=True,
        )[:10]

    # ─────────────────────────────────────────────────────────────────────────
    # OpenSearch / Elasticsearch
    # ─────────────────────────────────────────────────────────────────────────

    def _collect_opensearch(self, cfg: dict, signals: CollectedSignals,
                            time_window_minutes: int) -> None:
        url   = cfg["base_url"]
        token = cfg.get("auth_token")

        # Cluster health
        health = _http_get(url, "/_cluster/health", token)
        if health:
            status = health.get("status", "unknown")
            if status in ("yellow", "red"):
                signals.firing_alerts.append(FiringAlert(
                    name=f"OpenSearch cluster status: {status}",
                    severity="critical" if status == "red" else "warning",
                    labels={"tool": "opensearch", "cluster": health.get("cluster_name", "")},
                    annotations={"description": f"Cluster health is {status}. "
                                               f"Unassigned shards: {health.get('unassigned_shards', 0)}"},
                    starts_at=datetime.now(timezone.utc).isoformat(),
                    source_tool="opensearch",
                ))

        # Recent error logs via search
        now_ms   = int(time.time() * 1000)
        start_ms = now_ms - time_window_minutes * 60 * 1000
        query = {
            "query": {
                "bool": {
                    "must": [
                        {"range": {"@timestamp": {"gte": start_ms, "lte": now_ms, "format": "epoch_millis"}}},
                        {"terms": {"log.level": ["ERROR", "FATAL", "error", "fatal"]}},
                    ]
                }
            },
            "sort": [{"@timestamp": {"order": "desc"}}],
            "size": 50,
            "_source": ["@timestamp", "log.level", "message", "service.name"],
        }
        try:
            resp = requests.post(
                url.rstrip("/") + "/_search",
                json=query,
                headers={
                    "Content-Type": "application/json",
                    **({"Authorization": f"Bearer {token}"} if token else {}),
                },
                timeout=15,
                verify=False,
            )
            if resp.status_code == 200:
                hits = resp.json().get("hits", {}).get("hits", [])
                for hit in hits:
                    src = hit.get("_source", {})
                    signals.error_logs.append(LogEntry(
                        timestamp=src.get("@timestamp", ""),
                        level=src.get("log.level", src.get("level", "ERROR")),
                        message=src.get("message", "")[:300],  # truncate
                        index=hit.get("_index", ""),
                    ))
        except Exception as exc:
            signals.collection_errors.append(f"opensearch/search: {exc}")
