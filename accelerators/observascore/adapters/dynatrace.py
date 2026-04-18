"""Dynatrace adapter - read-only extraction via the Dynatrace Environment API v1/v2.

Extracts monitored entities (services, hosts, applications), open problems,
SLO definitions, dashboards, alerting profiles, notification integrations,
synthetic monitors, and log management state. All data is normalised into
the Common Observability Model (COM).

Auth: API token with the following token scopes:
  entities.read, problems.read, slo.read, settings.read,
  dashboards.read, syntheticLocations.read, metrics.read, logs.read

Config keys:
  url         — e.g. https://<env-id>.live.dynatrace.com  (SaaS)
                     https://<managed-host>/e/<env-id>    (Managed)
  api_token   — the API token string
"""
from __future__ import annotations

import logging
from typing import Any

from observascore.adapters.base import BaseAdapter, AdapterError
from observascore.model import (
    AlertClassification,
    AlertRule,
    Dashboard,
    RecordingRule,
    Service,
    Signal,
    SignalType,
)

logger = logging.getLogger(__name__)

# Entity types Dynatrace exposes via entitySelector
_ENTITY_TYPES = {
    "SERVICE": SignalType.TRACE,
    "HOST": SignalType.METRIC,
    "APPLICATION": SignalType.TRACE,
    "PROCESS_GROUP": SignalType.METRIC,
}


class DynatraceAdapter(BaseAdapter):
    """Read-only adapter for the Dynatrace Environment API."""

    tool_name = "dynatrace"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.api_token = config.get("api_token", "") or config.get("api_key", "")

    def _configure_auth(self) -> None:
        self.session.headers.update({
            "Authorization": f"Api-Token {self.api_token}",
            "Content-Type": "application/json",
        })

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def health_check(self) -> bool:
        try:
            data = self._get("/api/v2/entities", params={"entitySelector": 'type("HOST")', "pageSize": 1})
            return "totalCount" in data or "entities" in data
        except Exception as e:
            logger.error("Dynatrace health check failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # Extract
    # ------------------------------------------------------------------

    def extract(self) -> dict[str, Any]:
        logger.info("Extracting from Dynatrace at %s", self.url)
        result: dict[str, Any] = {
            "services": [],
            "alert_rules": [],
            "dashboards": [],
            "recording_rules": [],  # SLOs
            "signals": [],
            "entity_counts": {},
            "problems_open": 0,
            "synthetics_count": 0,
            "alerting_profiles": 0,
            "notification_integrations": 0,
            "has_log_management": False,
            "has_rum": False,
            "errors": [],
        }

        self._fetch_entities(result)
        self._fetch_problems(result)
        self._fetch_slos(result)
        self._fetch_dashboards(result)
        self._fetch_alerting_profiles(result)
        self._fetch_notification_integrations(result)
        self._fetch_synthetic_monitors(result)
        self._fetch_log_management(result)
        self._check_rum(result)

        logger.info(
            "Dynatrace: services=%d, hosts=%d, apps=%d, problems=%d, slos=%d, synthetics=%d",
            result["entity_counts"].get("SERVICE", 0),
            result["entity_counts"].get("HOST", 0),
            result["entity_counts"].get("APPLICATION", 0),
            result["problems_open"],
            len(result["recording_rules"]),
            result["synthetics_count"],
        )
        return result

    # ------------------------------------------------------------------
    # Entities
    # ------------------------------------------------------------------

    def _fetch_entities(self, result: dict) -> None:
        for entity_type, signal_type in _ENTITY_TYPES.items():
            try:
                resp = self._get("/api/v2/entities", params={
                    "entitySelector": f'type("{entity_type}")',
                    "pageSize": 100,
                    "fields": "+properties",
                })
                entities = resp.get("entities", []) or []
                total = resp.get("totalCount", len(entities))
                result["entity_counts"][entity_type] = total

                for ent in entities:
                    name = ent.get("displayName", ent.get("entityId", ""))
                    result["signals"].append(Signal(
                        source_tool=self.tool_name,
                        identifier=name,
                        signal_type=signal_type,
                        semantic_type="latency" if entity_type == "SERVICE" else "saturation",
                        labels={
                            "entity_type": entity_type,
                            "entity_id": ent.get("entityId", ""),
                        },
                    ))
                    if entity_type == "SERVICE":
                        result["services"].append(Service(
                            name=name,
                            source_tool=self.tool_name,
                        ))

                logger.info("  Dynatrace %s: %d total", entity_type, total)
            except AdapterError as e:
                result["errors"].append(f"entities/{entity_type}: {e}")

    # ------------------------------------------------------------------
    # Problems (Davis AI open issues)
    # ------------------------------------------------------------------

    def _fetch_problems(self, result: dict) -> None:
        try:
            resp = self._get("/api/v2/problems", params={
                "problemSelector": "status(OPEN)",
                "pageSize": 50,
            })
            result["problems_open"] = resp.get("totalCount", 0)
            # Each open problem is an active alert — surface as an AlertRule
            for problem in resp.get("problems", []) or []:
                severity_map = {
                    "AVAILABILITY": "critical",
                    "ERROR": "high",
                    "PERFORMANCE": "medium",
                    "RESOURCE_CONTENTION": "medium",
                    "CUSTOM_ALERT": "medium",
                }
                sev = severity_map.get(problem.get("severityLevel", ""), "medium")
                result["alert_rules"].append(AlertRule(
                    source_tool=self.tool_name,
                    name=problem.get("title", ""),
                    expression=f"dynatrace:problem:{problem.get('problemId', '')}",
                    severity=sev,
                    classification=AlertClassification.SYMPTOM,
                    labels={"status": "OPEN", "impact": problem.get("impactLevel", "")},
                    annotations={"summary": problem.get("title", "")},
                    group="davis_ai",
                ))
            logger.info("  Dynatrace open problems: %d", result["problems_open"])
        except AdapterError as e:
            result["errors"].append(f"problems: {e}")

    # ------------------------------------------------------------------
    # SLOs → RecordingRule
    # ------------------------------------------------------------------

    def _fetch_slos(self, result: dict) -> None:
        try:
            resp = self._get("/api/v2/slo", params={"pageSize": 100})
            for slo in resp.get("slos", []) or []:
                result["recording_rules"].append(RecordingRule(
                    source_tool=self.tool_name,
                    name=f"slo:{slo.get('name', '')}",
                    expression=slo.get("metricExpression", ""),
                    labels={
                        "target": str(slo.get("target", "")),
                        "warning": str(slo.get("warning", "")),
                        "status": slo.get("status", ""),
                    },
                ))
            logger.info("  Dynatrace SLOs: %d", len(result["recording_rules"]))
        except AdapterError as e:
            result["errors"].append(f"slos: {e}")

    # ------------------------------------------------------------------
    # Dashboards
    # ------------------------------------------------------------------

    def _fetch_dashboards(self, result: dict) -> None:
        try:
            resp = self._get("/api/v1/dashboards")
            for d in resp.get("dashboards", []) or []:
                owner = d.get("owner", "")
                title = d.get("name", "Untitled")
                # Skip Dynatrace built-in dashboards
                if owner in ("Dynatrace", "") and "#Dynatrace" in title:
                    continue
                result["dashboards"].append(Dashboard(
                    source_tool=self.tool_name,
                    uid=d.get("id", ""),
                    title=title,
                    owner=owner,
                    tags=d.get("tags", []) or [],
                ))
            logger.info("  Dynatrace custom dashboards: %d", len(result["dashboards"]))
        except AdapterError as e:
            result["errors"].append(f"dashboards: {e}")

    # ------------------------------------------------------------------
    # Alerting profiles
    # ------------------------------------------------------------------

    def _fetch_alerting_profiles(self, result: dict) -> None:
        try:
            resp = self._get("/api/v1/config/alertingProfiles")
            profiles = resp.get("values", []) or []
            # Count non-default profiles
            custom = [p for p in profiles if p.get("name", "") != "Default"]
            result["alerting_profiles"] = len(custom)
            logger.info("  Dynatrace custom alerting profiles: %d", result["alerting_profiles"])
        except AdapterError as e:
            result["errors"].append(f"alerting_profiles: {e}")

    # ------------------------------------------------------------------
    # Notification integrations (PagerDuty, Slack, OpsGenie, etc.)
    # ------------------------------------------------------------------

    def _fetch_notification_integrations(self, result: dict) -> None:
        try:
            resp = self._get("/api/v1/config/notifications")
            notifications = resp.get("values", []) or []
            result["notification_integrations"] = len(notifications)
            logger.info("  Dynatrace notification integrations: %d", result["notification_integrations"])
        except AdapterError as e:
            result["errors"].append(f"notifications: {e}")

    # ------------------------------------------------------------------
    # Synthetic monitors
    # ------------------------------------------------------------------

    def _fetch_synthetic_monitors(self, result: dict) -> None:
        try:
            resp = self._get("/api/v1/synthetic/monitors", params={"pageSize": 100})
            monitors = resp.get("monitors", []) or []
            result["synthetics_count"] = len(monitors)
            for m in monitors:
                result["signals"].append(Signal(
                    source_tool=self.tool_name,
                    identifier=m.get("name", ""),
                    signal_type=SignalType.METRIC,
                    semantic_type="traffic",
                    labels={"monitor_type": m.get("type", ""), "enabled": str(m.get("enabled", True))},
                ))
            logger.info("  Dynatrace synthetic monitors: %d", result["synthetics_count"])
        except AdapterError as e:
            result["errors"].append(f"synthetic_monitors: {e}")

    # ------------------------------------------------------------------
    # Log Management (via Settings API)
    # ------------------------------------------------------------------

    def _fetch_log_management(self, result: dict) -> None:
        # Try to detect log management via DQL / Log Ingest config
        try:
            resp = self._get("/api/v2/settings/objects", params={
                "schemaIds": "builtin:logmonitoring.log-ddu-pool",
                "scopes": "environment",
                "pageSize": 1,
            })
            items = resp.get("items", []) or []
            result["has_log_management"] = len(items) > 0
        except AdapterError:
            # Try alternate endpoint
            try:
                resp = self._get("/api/v2/logs/search", params={"limit": 1})
                result["has_log_management"] = True
            except AdapterError as e:
                result["errors"].append(f"log_management: {e}")

    # ------------------------------------------------------------------
    # Real User Monitoring (RUM / Digital Experience Monitoring)
    # ------------------------------------------------------------------

    def _check_rum(self, result: dict) -> None:
        """Infer RUM presence from APPLICATION entity count."""
        app_count = result["entity_counts"].get("APPLICATION", 0)
        result["has_rum"] = app_count > 0
