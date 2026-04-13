"""Prometheus adapter - read-only extraction via HTTP API."""
from __future__ import annotations

import logging
import re
from typing import Any

from observascore.adapters.base import BaseAdapter, AdapterError
from observascore.model import (
    AlertClassification,
    AlertRule,
    RecordingRule,
    ScrapeTarget,
    Signal,
    SignalType,
)

logger = logging.getLogger(__name__)


# Heuristic patterns to classify alerts
BURN_RATE_PATTERNS = [
    re.compile(r"slo[_:]", re.IGNORECASE),
    re.compile(r"burn[_\-]?rate", re.IGNORECASE),
    re.compile(r"error[_\-]?budget", re.IGNORECASE),
]

SYMPTOM_KEYWORDS = ["latency", "error_rate", "errors_total", "availability", "success_rate"]
CAUSE_KEYWORDS = ["cpu", "memory", "disk", "thread", "gc_", "pool_", "connection"]


def classify_alert(name: str, expression: str) -> AlertClassification:
    """Classify an alert as symptom/cause/burn-rate based on name and expression."""
    combined = f"{name} {expression}".lower()
    for pattern in BURN_RATE_PATTERNS:
        if pattern.search(combined):
            return AlertClassification.BURN_RATE
    if any(kw in combined for kw in SYMPTOM_KEYWORDS):
        return AlertClassification.SYMPTOM
    if any(kw in combined for kw in CAUSE_KEYWORDS):
        return AlertClassification.CAUSE
    return AlertClassification.UNKNOWN


class PrometheusAdapter(BaseAdapter):
    tool_name = "prometheus"

    def health_check(self) -> bool:
        try:
            resp = self.session.get(f"{self.url}/-/healthy", timeout=self.timeout, verify=self.verify_tls)
            return resp.status_code == 200
        except Exception:
            try:
                self._get("/api/v1/status/buildinfo")
                return True
            except Exception as e:
                logger.error("Prometheus health check failed: %s", e)
                return False

    def extract(self) -> dict[str, Any]:
        """Pull targets, rules, and sample metrics."""
        logger.info("Extracting from Prometheus at %s", self.url)
        result: dict[str, Any] = {
            "alert_rules": [],
            "recording_rules": [],
            "scrape_targets": [],
            "signals": [],
            "errors": [],
        }

        # Targets
        try:
            targets_data = self._get("/api/v1/targets")
            active = targets_data.get("data", {}).get("activeTargets", [])
            for tgt in active:
                result["scrape_targets"].append(
                    ScrapeTarget(
                        source_tool=self.tool_name,
                        job=tgt.get("labels", {}).get("job", "unknown"),
                        instance=tgt.get("labels", {}).get("instance", "unknown"),
                        health=tgt.get("health", "unknown"),
                        last_scrape_error=tgt.get("lastError") or None,
                        labels=tgt.get("labels", {}),
                    )
                )
            logger.info("  Extracted %d scrape targets", len(result["scrape_targets"]))
        except AdapterError as e:
            result["errors"].append(f"targets: {e}")

        # Rules
        try:
            rules_data = self._get("/api/v1/rules")
            groups = rules_data.get("data", {}).get("groups", [])
            for group in groups:
                group_name = group.get("name", "")
                for rule in group.get("rules", []):
                    rtype = rule.get("type", "")
                    if rtype == "alerting":
                        name = rule.get("name", "")
                        expr = rule.get("query", "")
                        labels = rule.get("labels", {}) or {}
                        annotations = rule.get("annotations", {}) or {}
                        runbook_url = (
                            annotations.get("runbook_url")
                            or annotations.get("runbook")
                            or labels.get("runbook_url")
                            or labels.get("runbook")
                        )
                        result["alert_rules"].append(
                            AlertRule(
                                source_tool=self.tool_name,
                                name=name,
                                expression=expr,
                                severity=labels.get("severity"),
                                classification=classify_alert(name, expr),
                                for_duration=rule.get("duration") and str(rule["duration"]) or None,
                                labels=labels,
                                annotations=annotations,
                                runbook_url=runbook_url,
                                group=group_name,
                                raw=rule,
                            )
                        )
                    elif rtype == "recording":
                        result["recording_rules"].append(
                            RecordingRule(
                                source_tool=self.tool_name,
                                name=rule.get("name", ""),
                                expression=rule.get("query", ""),
                                group=group_name,
                                labels=rule.get("labels", {}) or {},
                            )
                        )
            logger.info(
                "  Extracted %d alert rules, %d recording rules",
                len(result["alert_rules"]),
                len(result["recording_rules"]),
            )
        except AdapterError as e:
            result["errors"].append(f"rules: {e}")

        # Sample metrics - names and cardinality estimate
        try:
            metrics_data = self._get("/api/v1/label/__name__/values")
            metric_names = metrics_data.get("data", [])[:500]  # cap for demo
            for m in metric_names:
                semantic = self._infer_semantic(m)
                result["signals"].append(
                    Signal(
                        source_tool=self.tool_name,
                        identifier=m,
                        signal_type=SignalType.METRIC,
                        semantic_type=semantic,
                    )
                )
            logger.info("  Sampled %d metric names", len(result["signals"]))
        except AdapterError as e:
            result["errors"].append(f"metrics: {e}")

        return result

    @staticmethod
    def _infer_semantic(metric_name: str) -> str | None:
        """Tag a metric with its likely golden-signal category."""
        n = metric_name.lower()
        if any(k in n for k in ["duration", "latency", "response_time"]):
            return "latency"
        if any(k in n for k in ["error", "failure", "fail_"]):
            return "error"
        if any(k in n for k in ["cpu", "memory", "disk", "saturation", "pool"]):
            return "saturation"
        if any(k in n for k in ["requests_total", "request_count", "traffic", "throughput"]):
            return "traffic"
        return None
