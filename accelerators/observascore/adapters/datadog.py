"""Datadog adapter - read-only extraction via the Datadog API v1/v2.

Extracts monitors, dashboards, hosts, SLOs, Synthetics tests, APM services,
log management configuration, security monitoring status, and the service
catalog. All data is normalised into the Common Observability Model (COM).

Auth: Requires both DD-API-KEY (identify the org) and DD-APPLICATION-KEY
(application-level access). Set ``api_key`` and ``app_key`` in config.

Regions: defaults to US1 (api.datadoghq.com). Set ``site`` to
``datadoghq.eu``, ``us3.datadoghq.com``, etc. for other regions.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from observascore.adapters.base import BaseAdapter, AdapterError
from observascore.model import (
    AlertClassification,
    AlertRule,
    Dashboard,
    DashboardPanel,
    RecordingRule,
    Service,
    Signal,
    SignalType,
)

logger = logging.getLogger(__name__)

# Regex to find @-notification handles in a Datadog monitor message
_NOTIFY_RE = re.compile(r"@[\w.\-/]+")

# Datadog monitor priority → ObservaScore severity
_PRIORITY_SEV = {1: "critical", 2: "critical", 3: "high", 4: "medium", 5: "low"}

# Datadog monitor type prefixes that signal APM usage
_APM_MONITOR_TYPES = {"apm alert", "trace-analytics alert", "service check"}


def _dd_severity(monitor: dict) -> str:
    """Infer severity from Datadog monitor priority or threshold labels."""
    priority = monitor.get("priority")
    if priority:
        return _PRIORITY_SEV.get(int(priority), "medium")
    name_lower = monitor.get("name", "").lower()
    if "p1" in name_lower or "critical" in name_lower:
        return "critical"
    if "p2" in name_lower or "high" in name_lower:
        return "high"
    if "p3" in name_lower or "warn" in name_lower:
        return "medium"
    return "medium"


def _extract_runbook(message: str) -> str | None:
    """Try to extract a runbook URL from the monitor message/description."""
    match = re.search(r"https?://\S+runbook\S*", message, re.IGNORECASE)
    if match:
        return match.group(0).rstrip(")")
    match = re.search(r"https?://\S+/wiki/\S+", message, re.IGNORECASE)
    if match:
        return match.group(0).rstrip(")")
    return None


class DatadogAdapter(BaseAdapter):
    """Read-only adapter for the Datadog API."""

    tool_name = "datadog"

    def __init__(self, config: dict[str, Any]):
        site = config.get("site", "datadoghq.com")
        # Ensure config carries the resolved base URL
        if not config.get("url"):
            config = {**config, "url": f"https://api.{site}"}
        super().__init__(config)
        self.api_key = config.get("api_key", "")
        self.app_key = config.get("app_key", "")

    def _configure_auth(self) -> None:
        self.session.headers.update({
            "DD-API-KEY": self.api_key,
            "DD-APPLICATION-KEY": self.app_key,
            "Content-Type": "application/json",
        })

    def health_check(self) -> bool:
        try:
            data = self._get("/api/v1/validate")
            return data.get("valid", False)
        except Exception as e:
            logger.error("Datadog health check failed: %s", e)
            return False

    def extract(self) -> dict[str, Any]:
        logger.info("Extracting from Datadog at %s", self.url)
        result: dict[str, Any] = {
            "monitors": [],
            "alert_rules": [],
            "dashboards": [],
            "services": [],
            "recording_rules": [],   # SLOs → RecordingRule
            "signals": [],
            "hosts_count": 0,
            "synthetics_count": 0,
            "has_apm": False,
            "has_log_management": False,
            "has_security_monitoring": False,
            "has_service_catalog": False,
            "monitors_with_notifications": 0,
            "errors": [],
        }

        self._fetch_monitors(result)
        self._fetch_dashboards(result)
        self._fetch_hosts(result)
        self._fetch_slos(result)
        self._fetch_synthetics(result)
        self._fetch_apm_services(result)
        self._fetch_log_indexes(result)
        self._fetch_security_monitoring(result)
        self._fetch_service_catalog(result)

        logger.info(
            "Datadog: %d monitors, %d dashboards, %d hosts, %d SLOs, %d synthetics",
            len(result["alert_rules"]),
            len(result["dashboards"]),
            result["hosts_count"],
            len(result["recording_rules"]),
            result["synthetics_count"],
        )
        return result

    # ------------------------------------------------------------------
    # Monitors → AlertRule
    # ------------------------------------------------------------------

    def _fetch_monitors(self, result: dict) -> None:
        try:
            page, page_size = 0, 200
            while True:
                batch = self._get("/api/v1/monitor", params={
                    "count": page_size, "start": page * page_size,
                    "with_downtimes": False,
                })
                monitors = batch if isinstance(batch, list) else []
                if not monitors:
                    break
                result["monitors"].extend(monitors)
                for m in monitors:
                    self._monitor_to_alert_rule(m, result)
                    # Detect APM monitors
                    if any(t in m.get("type", "").lower() for t in _APM_MONITOR_TYPES):
                        result["has_apm"] = True
                if len(monitors) < page_size:
                    break
                page += 1
            logger.info("  Fetched %d Datadog monitors", len(result["monitors"]))
        except AdapterError as e:
            result["errors"].append(f"monitors: {e}")

    def _monitor_to_alert_rule(self, m: dict, result: dict) -> None:
        name = m.get("name", "")
        message = m.get("message", "") or ""
        tags = m.get("tags", []) or []
        severity = _dd_severity(m)
        runbook = _extract_runbook(message)
        notify_handles = _NOTIFY_RE.findall(message)
        has_notification = bool(notify_handles)
        if has_notification:
            result["monitors_with_notifications"] = result.get("monitors_with_notifications", 0) + 1

        # Classify: monitors on error/latency are symptom-based
        query = m.get("query", "") or ""
        name_lower = name.lower()
        if any(kw in name_lower for kw in ("slo", "burn", "error_budget")):
            classification = AlertClassification.BURN_RATE
        elif any(kw in name_lower for kw in ("latency", "p99", "error", "availability", "success")):
            classification = AlertClassification.SYMPTOM
        elif any(kw in name_lower for kw in ("cpu", "memory", "disk", "queue_depth")):
            classification = AlertClassification.CAUSE
        else:
            classification = AlertClassification.UNKNOWN

        result["alert_rules"].append(AlertRule(
            source_tool=self.tool_name,
            name=name,
            expression=query,
            severity=severity,
            classification=classification,
            for_duration=None,  # Datadog uses evaluation windows differently
            labels={t.split(":")[0]: t.split(":", 1)[1] if ":" in t else t for t in tags[:10]},
            annotations={
                "summary": name,
                "description": message[:500] if message else "",
                "notify_handles": ", ".join(notify_handles[:5]),
            },
            runbook_url=runbook,
            group=m.get("type", ""),
            raw={},
        ))

    # ------------------------------------------------------------------
    # Dashboards
    # ------------------------------------------------------------------

    def _fetch_dashboards(self, result: dict) -> None:
        try:
            resp = self._get("/api/v1/dashboard")
            for d in resp.get("dashboards", []) or []:
                tags = d.get("tags", []) or []
                result["dashboards"].append(Dashboard(
                    source_tool=self.tool_name,
                    uid=d.get("id", ""),
                    title=d.get("title", "Untitled"),
                    folder=None,
                    tags=tags,
                    has_templating=bool(d.get("template_variables")),
                ))
            logger.info("  Fetched %d Datadog dashboards", len(result["dashboards"]))
        except AdapterError as e:
            result["errors"].append(f"dashboards: {e}")

    # ------------------------------------------------------------------
    # Hosts
    # ------------------------------------------------------------------

    def _fetch_hosts(self, result: dict) -> None:
        try:
            resp = self._get("/api/v1/hosts", params={"count": 1})
            result["hosts_count"] = resp.get("total_matching", 0)
            logger.info("  Datadog host count: %d", result["hosts_count"])
        except AdapterError as e:
            result["errors"].append(f"hosts: {e}")

    # ------------------------------------------------------------------
    # SLOs → RecordingRule (proxy for SLO-as-code)
    # ------------------------------------------------------------------

    def _fetch_slos(self, result: dict) -> None:
        try:
            resp = self._get("/api/v2/slo", params={"limit": 100})
            for slo in resp.get("data", []) or []:
                attrs = slo.get("attributes", {}) or {}
                result["recording_rules"].append(RecordingRule(
                    source_tool=self.tool_name,
                    name=f"slo:{attrs.get('name', slo.get('id', ''))}",
                    expression=f"datadog_slo:{slo.get('id', '')}",
                    labels={
                        "target": str(attrs.get("target_threshold", "")),
                        "type": attrs.get("slo_type", ""),
                    },
                ))
            logger.info("  Fetched %d Datadog SLOs", len(result["recording_rules"]))
        except AdapterError as e:
            result["errors"].append(f"slos: {e}")

    # ------------------------------------------------------------------
    # Synthetics
    # ------------------------------------------------------------------

    def _fetch_synthetics(self, result: dict) -> None:
        try:
            resp = self._get("/api/v2/synthetics/tests", params={"page_size": 100})
            tests = resp.get("tests", []) or []
            result["synthetics_count"] = len(tests)
            for t in tests:
                result["signals"].append(Signal(
                    source_tool=self.tool_name,
                    identifier=t.get("name", ""),
                    signal_type=SignalType.METRIC,
                    semantic_type="traffic",
                    labels={"test_type": t.get("type", "")},
                ))
            logger.info("  Fetched %d Datadog Synthetic tests", result["synthetics_count"])
        except AdapterError as e:
            result["errors"].append(f"synthetics: {e}")

    # ------------------------------------------------------------------
    # APM Services
    # ------------------------------------------------------------------

    def _fetch_apm_services(self, result: dict) -> None:
        try:
            resp = self._get("/api/v2/services/definitions", params={"schema_version": "v2.1"})
            services = resp.get("data", []) or []
            for svc in services:
                attrs = svc.get("attributes", {}).get("schema", {}) or {}
                result["services"].append(Service(
                    name=attrs.get("dd-service", svc.get("id", "")),
                    source_tool=self.tool_name,
                    tier=attrs.get("team", None),
                ))
                result["signals"].append(Signal(
                    source_tool=self.tool_name,
                    identifier=attrs.get("dd-service", ""),
                    signal_type=SignalType.TRACE,
                ))
            if services:
                result["has_apm"] = True
                result["has_service_catalog"] = True
            logger.info("  Fetched %d Datadog service catalog entries", len(services))
        except AdapterError as e:
            # Service catalog may not be enabled for all tiers — check APM via monitors
            result["errors"].append(f"service_catalog: {e}")

    # ------------------------------------------------------------------
    # Log Management
    # ------------------------------------------------------------------

    def _fetch_log_indexes(self, result: dict) -> None:
        try:
            resp = self._get("/api/v1/logs/config/indexes")
            indexes = resp.get("indexes", []) or []
            result["has_log_management"] = len(indexes) > 0
            for idx in indexes:
                result["signals"].append(Signal(
                    source_tool=self.tool_name,
                    identifier=idx.get("name", ""),
                    signal_type=SignalType.LOG,
                ))
            logger.info("  Datadog log indexes: %d", len(indexes))
        except AdapterError as e:
            result["errors"].append(f"log_indexes: {e}")

    # ------------------------------------------------------------------
    # Security Monitoring
    # ------------------------------------------------------------------

    def _fetch_security_monitoring(self, result: dict) -> None:
        try:
            resp = self._get("/api/v2/security_monitoring/rules", params={"page[size]": 1})
            total = resp.get("meta", {}).get("page", {}).get("total_count", 0)
            result["has_security_monitoring"] = total > 0
            logger.info("  Datadog security rules: %d", total)
        except AdapterError as e:
            result["errors"].append(f"security_monitoring: {e}")

    # ------------------------------------------------------------------
    # Service Catalog (additional check)
    # ------------------------------------------------------------------

    def _fetch_service_catalog(self, result: dict) -> None:
        if result.get("has_service_catalog"):
            return  # already detected via service definitions
        try:
            resp = self._get("/api/v2/catalog/entity", params={"pageSize": 1})
            total = resp.get("meta", {}).get("pagination", {}).get("totalCount", 0)
            result["has_service_catalog"] = total > 0
        except AdapterError:
            pass
