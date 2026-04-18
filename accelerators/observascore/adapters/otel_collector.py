"""OpenTelemetry Collector adapter - read-only extraction.

The OTel Collector is the de-facto standard for vendor-neutral telemetry
pipelines. Its presence and configuration signal OTel adoption maturity.

We probe:
  - Health check extension   (:13133/)
  - Internal Prometheus metrics  (:8888/metrics)  — reveals active receivers/exporters
  - zpages extension         (:55679/debug/pipelinez)  — pipeline topology
"""
from __future__ import annotations

import logging
import re
from typing import Any

import requests

from observascore.adapters.base import BaseAdapter, AdapterError

logger = logging.getLogger(__name__)

# Regex to extract receiver/exporter names from Prometheus metric names
# e.g.  otelcol_receiver_accepted_spans_total{receiver="otlp",...}
_RECEIVER_RE = re.compile(r'otelcol_receiver_\w+\{.*?receiver="([^"]+)"')
_EXPORTER_RE = re.compile(r'otelcol_exporter_\w+\{.*?exporter="([^"]+)"')
_PIPELINE_RE = re.compile(r'otelcol_processor_\w+\{.*?pipeline="([^"]+)"')


class OtelCollectorAdapter(BaseAdapter):
    tool_name = "otel_collector"

    def __init__(self, config: dict):
        super().__init__(config)
        # Separate ports for health, metrics, and zpages
        self.metrics_port = config.get("metrics_port", 8888)
        self.health_port = config.get("health_port", 13133)
        self.zpages_port = config.get("zpages_port", 55679)
        # Build alternate base URLs for the sidecar ports
        base_host = self._extract_host(self.url)
        self.metrics_url = f"http://{base_host}:{self.metrics_port}"
        self.health_url = f"http://{base_host}:{self.health_port}"
        self.zpages_url = f"http://{base_host}:{self.zpages_port}"

    @staticmethod
    def _extract_host(url: str) -> str:
        """Strip scheme and port to get just the hostname."""
        url = url.replace("https://", "").replace("http://", "")
        return url.split(":")[0].split("/")[0]

    def health_check(self) -> bool:
        for check_url in [f"{self.health_url}/", self.url + "/", self.url + "/-/healthy"]:
            try:
                resp = self.session.get(check_url, timeout=self.timeout, verify=self.verify_tls)
                if resp.status_code in (200, 204):
                    return True
            except Exception:
                continue
        logger.error("OTel Collector health check failed for all endpoints")
        return False

    def extract(self) -> dict[str, Any]:
        logger.info("Extracting from OTel Collector at %s", self.url)
        result: dict[str, Any] = {
            "receivers": [],
            "exporters": [],
            "pipelines": [],
            "pipeline_count": 0,
            "errors": [],
        }

        # Try to get internal Prometheus metrics (most reliable signal)
        raw_metrics = self._fetch_prometheus_metrics()
        if raw_metrics:
            result["receivers"] = sorted(set(_RECEIVER_RE.findall(raw_metrics)))
            result["exporters"] = sorted(set(_EXPORTER_RE.findall(raw_metrics)))
            pipelines = sorted(set(_PIPELINE_RE.findall(raw_metrics)))
            result["pipelines"] = pipelines
            result["pipeline_count"] = len(pipelines)
            logger.info(
                "  OTel Collector: %d receivers, %d exporters, %d pipelines",
                len(result["receivers"]), len(result["exporters"]), result["pipeline_count"],
            )
        else:
            result["errors"].append("metrics endpoint not reachable; pipeline topology unknown")

        # Try zpages for richer pipeline info
        zpages_data = self._fetch_zpages()
        if zpages_data and not result["pipelines"]:
            result["pipelines"] = zpages_data.get("pipelines", [])
            result["pipeline_count"] = len(result["pipelines"])

        return result

    def _fetch_prometheus_metrics(self) -> str | None:
        """Fetch raw Prometheus text metrics from the collector's internal port."""
        for metrics_url in [f"{self.metrics_url}/metrics", f"{self.url}:8888/metrics",
                             f"{self.url}/metrics"]:
            try:
                resp = self.session.get(metrics_url, timeout=self.timeout, verify=self.verify_tls)
                if resp.status_code == 200 and "otelcol" in resp.text:
                    logger.info("  Got OTel Collector metrics from %s", metrics_url)
                    return resp.text
            except Exception:
                continue
        return None

    def _fetch_zpages(self) -> dict | None:
        """Try zpages extension for pipeline topology."""
        try:
            resp = self.session.get(
                f"{self.zpages_url}/debug/pipelinez",
                timeout=self.timeout, verify=self.verify_tls
            )
            if resp.status_code == 200:
                # zpages returns HTML; extract pipeline names via regex
                pipeline_names = re.findall(r'<td>([^<]+/[^<]+)</td>', resp.text)
                return {"pipelines": list(set(pipeline_names))}
        except Exception:
            pass
        return None
