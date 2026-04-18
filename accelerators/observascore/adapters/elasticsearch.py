"""Elasticsearch / OpenSearch adapter - read-only extraction.

Covers Elasticsearch and OpenSearch (API-compatible). Extracts index
inventory, data stream maturity, ILM policy presence, and APM index
patterns to assess log and APM observability coverage.
"""
from __future__ import annotations

import logging
from typing import Any

from observascore.adapters.base import BaseAdapter, AdapterError
from observascore.model import Signal, SignalType

logger = logging.getLogger(__name__)

# Index prefix patterns that indicate specific observability data
_LOG_PREFIXES = ("logs-", "filebeat-", "logstash-", "fluentd-", "fluentbit-", ".ds-logs")
_APM_PREFIXES = ("apm-", "traces-apm", "metrics-apm", ".ds-traces", ".ds-metrics-apm")
_METRICS_PREFIXES = ("metrics-", "metricbeat-", ".ds-metrics")


def _classify_index(name: str) -> str | None:
    n = name.lower()
    if any(n.startswith(p) for p in _APM_PREFIXES):
        return "apm"
    if any(n.startswith(p) for p in _LOG_PREFIXES):
        return "logs"
    if any(n.startswith(p) for p in _METRICS_PREFIXES):
        return "metrics"
    return None


class ElasticsearchAdapter(BaseAdapter):
    tool_name = "elasticsearch"

    def _configure_auth(self) -> None:
        super()._configure_auth()
        api_key = self.config.get("api_key")
        if api_key:
            self.session.headers.update({"Authorization": f"ApiKey {api_key}"})
        self.session.headers.update({"Content-Type": "application/json"})

    def health_check(self) -> bool:
        try:
            data = self._get("/_cluster/health")
            return data.get("status") in ("green", "yellow")
        except Exception as e:
            logger.error("Elasticsearch health check failed: %s", e)
            return False

    def extract(self) -> dict[str, Any]:
        logger.info("Extracting from Elasticsearch at %s", self.url)
        result: dict[str, Any] = {
            "indices": [],
            "data_streams": [],
            "has_ilm": False,
            "has_apm": False,
            "has_logs": False,
            "has_metrics": False,
            "signals": [],
            "cluster_health": "unknown",
            "errors": [],
        }

        # Cluster health
        try:
            health = self._get("/_cluster/health")
            result["cluster_health"] = health.get("status", "unknown")
            logger.info("  Cluster health: %s", result["cluster_health"])
        except AdapterError as e:
            result["errors"].append(f"cluster_health: {e}")

        # Indices (cap to avoid huge response)
        try:
            indices = self._get("/_cat/indices", params={"format": "json", "h": "index,health,status,docs.count"})
            for idx in indices or []:
                name = idx.get("index", "")
                if name.startswith("."):  # skip system indices
                    continue
                result["indices"].append(name)
                category = _classify_index(name)
                if category == "logs":
                    result["has_logs"] = True
                    result["signals"].append(Signal(
                        source_tool=self.tool_name,
                        identifier=name,
                        signal_type=SignalType.LOG,
                    ))
                elif category == "apm":
                    result["has_apm"] = True
                    result["signals"].append(Signal(
                        source_tool=self.tool_name,
                        identifier=name,
                        signal_type=SignalType.TRACE,
                    ))
                elif category == "metrics":
                    result["has_metrics"] = True
                    result["signals"].append(Signal(
                        source_tool=self.tool_name,
                        identifier=name,
                        signal_type=SignalType.METRIC,
                    ))
            logger.info("  Found %d non-system indices", len(result["indices"]))
        except AdapterError as e:
            result["errors"].append(f"indices: {e}")

        # Data streams (modern ES log management pattern)
        try:
            ds_resp = self._get("/_data_stream", params={"format": "json"})
            data_streams = ds_resp.get("data_streams", []) or []
            result["data_streams"] = [ds.get("name", "") for ds in data_streams]
            logger.info("  Found %d data streams", len(result["data_streams"]))
        except AdapterError as e:
            # Data streams API may not exist on older versions
            result["errors"].append(f"data_streams: {e}")

        # ILM policies (governance indicator)
        try:
            ilm = self._get("/_ilm/policy")
            # Filter out built-in policies
            custom_policies = {
                k: v for k, v in (ilm or {}).items()
                if not k.startswith(".")
            }
            result["has_ilm"] = len(custom_policies) > 0
            logger.info("  ILM policies: %d custom", len(custom_policies))
        except AdapterError as e:
            result["errors"].append(f"ilm: {e}")

        return result
